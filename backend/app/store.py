from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import fsum, isclose, isfinite
from pathlib import Path
import sqlite3
import threading
from time import monotonic, time
from typing import Any, Iterable

from .indexing import (
    IndexingBlocked, bootstrap_indexing, complete_work, enqueue_session, enqueue_work, initialize_indexing,
    indexing_status, record_conversations, require_storage_capacity, retry_work,
    stage_artifacts, stage_log_changes, write_prompts,
)
from .insights import _complete_usage, _parse_iso, build_insights
from .indexing_job import prepare_in_process
from .intervals import union_interval_duration
from .log_io import discover_logs, isolated_log_io
from .prompt_index import (
    conversation_ids,
    extract_prompt_content as _extract_prompt_content,
    group_prompts,
    prompt_group_counters_valid,
    read_copilot_turns as _read_copilot_turns,
)
from .session_identity import (
    prompt_id as _prompt_id,
    public_session_digest,
    public_session_id as _public_session_id,
    session_digest as _session_digest,
)
from .trace_archive import (
    archive_paths as _trace_archive_paths,
    archive_signature as _trace_archive_signature,
    archive_snapshot as _trace_archive_snapshot,
    attributes as _attributes,
    collect_trace_sessions,
    collect_trace_sessions_from_records,
    iter_json_lines as _iter_json_lines,
    milliseconds as _nanoseconds_to_milliseconds,
    read_json_lines as _read_json_lines,
)


SCENARIOS = ("pessimistic", "base", "optimistic")
_UNSET = object()

# Must match benchmarkFormulaVersion in shared/benchmark.ts; results from other versions are not comparable.
CURRENT_FORMULA_VERSION = 2
MAX_AGGREGATE_COMPONENT = float.fromhex("0x1.fffffffffffffp+1023") / 2**64
MODELED_TOTAL_KEYS = (
    "estimatedManualMinutes", "estimatedMinutesSaved", "estimatedManualLaborCostUsd",
    "estimatedAiAssistedLaborCostUsd", "estimatedAiAssistedTotalCostUsd",
    "estimatedGrossCostSavingsUsd", "estimatedBenefitUsd", "netValueUsd",
)
MODELED_RATIO_KEYS = ("taskTimeReduction", "modeledDeliveryCostReduction", "roi")
SCENARIO_REQUIRED_NUMERIC_FIELDS = (*MODELED_TOTAL_KEYS, "breakEvenManualMinutes")
PHASE_SCENARIO_NUMERIC_FIELDS = (
    "measuredAiMinutes", "measuredAiSeconds", "estimatedManualMinutes",
    "estimatedMinutesSaved", "timeReduction",
)
BENCHMARK_PHASES = ("planning", "research", "coding", "validation", "unclassified")
MAX_JSON_DEPTH = 100
MAX_JSON_NODES = 100_000
MAX_FORMULA_VERSION = 2**31 - 1
MIN_SQLITE_INTEGER = -(2**63)
MAX_SQLITE_INTEGER = 2**63 - 1
MAX_JSON_SAFE_INTEGER = 2**53 - 1
LEGACY_TRACE_IMPORT_KEY = "legacy_trace_inbox_import_v1"
OTEL_IMPORT_BATCH_SIZE = 1_000
OTEL_INBOX_PAGE_BYTES = 8 * 1024 * 1024
INSIGHTS_SESSION_QUERY = """
    SELECT sessions.*
    FROM session_time_index AS time_index INDEXED BY idx_session_time_epoch
    JOIN sessions ON sessions.experiment = time_index.experiment
    WHERE time_index.completed_at_epoch >= ?
    UNION ALL
    SELECT sessions.*
    FROM session_time_index AS time_index INDEXED BY idx_session_time_epoch
    JOIN sessions ON sessions.experiment = time_index.experiment
    WHERE time_index.completed_at_epoch IS NULL
"""


def _iso_milliseconds(value: str) -> int:
    try:
        parsed = _parse_iso(value)
        return int(parsed.timestamp() * 1_000) if parsed is not None else 0
    except (OSError, OverflowError, ValueError):
        return 0


def _iso_epoch(value: Any) -> float | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    seconds = (parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
    return seconds if isfinite(seconds) else None


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
        return number if isfinite(number) and abs(number) <= MAX_AGGREGATE_COMPONENT else 0
    except (OverflowError, TypeError, ValueError):
        return 0


def _modeled_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if isfinite(number) and abs(number) <= MAX_AGGREGATE_COMPONENT else None


def _bounded_json_integer(value: str) -> int | str:
    digits = value.removeprefix("-")
    return value if len(digits) > 1_000 else int(value)


def _json_loads(value: str) -> Any:
    return json.loads(value, parse_int=_bounded_json_integer)


def _integer(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return value if MIN_SQLITE_INTEGER <= value <= MAX_SQLITE_INTEGER else 0


def _nonnegative_integer(value: Any) -> int:
    number = _integer(value)
    return number if number >= 0 else 0


def _safe_json_integer(value: int) -> int | None:
    return value if -MAX_JSON_SAFE_INTEGER <= value <= MAX_JSON_SAFE_INTEGER else None


def _bounded_json_tree(value: Any, leaf_validator: Any) -> bool:
    stack = [(value, 0)]
    remaining = MAX_JSON_NODES - 1
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            if (
                depth >= MAX_JSON_DEPTH
                or len(current) > remaining
                or any(not isinstance(key, str) for key in current)
            ):
                return False
            remaining -= len(current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            if depth >= MAX_JSON_DEPTH or len(current) > remaining:
                return False
            remaining -= len(current)
            stack.extend((item, depth + 1) for item in current)
        elif not leaf_validator(current):
            return False
    return True


def _is_json_safe(value: Any) -> bool:
    def valid_leaf(item: Any) -> bool:
        if item is None or isinstance(item, (bool, str)):
            return True
        if isinstance(item, (int, float)):
            try:
                return isfinite(float(item))
            except OverflowError:
                return False
        return False

    return _bounded_json_tree(value, valid_leaf)


def _public_object(value: Any) -> dict[str, Any]:
    try:
        return value if isinstance(value, dict) and _is_json_safe(value) else {}
    except RecursionError:
        return {}


def _is_numeric_output(value: Any) -> bool:
    return _bounded_json_tree(
        value,
        lambda item: item is None or _modeled_number(item) is not None,
    )


def _valid_nullable_modeled_number(value: Any) -> bool:
    return value is None or _modeled_number(value) is not None


def _close(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


def _finite_sum(values: Iterable[float]) -> float | None:
    try:
        result = fsum(values)
    except (OverflowError, ValueError):
        return None
    return result if isfinite(result) and abs(result) <= MAX_AGGREGATE_COMPONENT else None


def _nullable_identity(value: Any, expected: float | None) -> bool:
    if expected is None:
        return value is None
    actual = _modeled_number(value)
    return actual is not None and _close(actual, expected)


def _valid_phase_measurement(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    seconds = _modeled_number(value.get("measuredAiSeconds"))
    minutes = _modeled_number(value.get("measuredAiMinutes"))
    return (
        seconds is not None and seconds >= 0
        and minutes is not None and minutes >= 0
        and _close(minutes, seconds / 60)
    )


def _valid_phase_scenario(value: Any, measurement: dict[str, Any]) -> bool:
    if not isinstance(value, dict) or any(
        _modeled_number(value.get(field)) is None
        for field in PHASE_SCENARIO_NUMERIC_FIELDS
    ):
        return False
    measured_seconds = _modeled_number(value["measuredAiSeconds"])
    measured_minutes = _modeled_number(value["measuredAiMinutes"])
    manual_minutes = _modeled_number(value["estimatedManualMinutes"])
    saved_minutes = _modeled_number(value["estimatedMinutesSaved"])
    reduction = _modeled_number(value["timeReduction"])
    expected_seconds = _modeled_number(measurement.get("measuredAiSeconds"))
    expected_minutes = _modeled_number(measurement.get("measuredAiMinutes"))
    if any(item is None for item in (
        measured_seconds, measured_minutes, manual_minutes, saved_minutes,
        reduction, expected_seconds, expected_minutes,
    )):
        return False
    expected_saved = manual_minutes - measured_minutes
    expected_reduction = expected_saved / manual_minutes if manual_minutes != 0 else 0
    return (
        measured_seconds >= 0 and measured_minutes >= 0 and manual_minutes >= 0
        and _close(measured_seconds, expected_seconds)
        and _close(measured_minutes, expected_minutes)
        and _close(measured_minutes, measured_seconds / 60)
        and _close(saved_minutes, expected_saved)
        and _close(reduction, expected_reduction)
    )


def _valid_capacity_point(
    value: Any,
    *,
    measured_minutes: float,
    saved_minutes: float,
    labor_cost_per_minute: float,
    ai_cost: float,
) -> bool:
    if not isinstance(value, dict):
        return False
    capacity = _modeled_number(value.get("capacityRealization"))
    benefit = _modeled_number(value.get("estimatedBenefitUsd"))
    net_value = _modeled_number(value.get("netValueUsd"))
    break_even = _modeled_number(value.get("breakEvenManualMinutes"))
    if any(item is None for item in (capacity, benefit, net_value, break_even)):
        return False
    expected_benefit = _checked_product(saved_minutes, labor_cost_per_minute, capacity)
    expected_net = _checked_subtract(expected_benefit, ai_cost) if expected_benefit is not None else None
    break_even_adjustment = _checked_divide(ai_cost, labor_cost_per_minute * capacity)
    expected_break_even = (
        _checked_add(measured_minutes, break_even_adjustment)
        if break_even_adjustment is not None else None
    )
    expected_roi = _checked_divide(expected_net, ai_cost) if ai_cost > 0 and expected_net is not None else None
    return (
        0 < capacity <= 1
        and expected_benefit is not None and _close(benefit, expected_benefit)
        and expected_net is not None and _close(net_value, expected_net)
        and expected_break_even is not None and _close(break_even, expected_break_even)
        and "roi" in value and _nullable_identity(value.get("roi"), expected_roi)
    )


def _validated_scenario_result(
    value: Any,
    *,
    measurements: dict[str, Any],
    usage_phases: dict[str, Any],
    measured_minutes: float,
    ai_cost: float,
    retained_source_characters: int,
    characters_per_word: float,
    scenario_config: dict[str, Any],
) -> tuple[float, tuple[float, ...], frozenset[float]] | None:
    if not isinstance(value, dict) or any(
        _modeled_number(value.get(field)) is None
        for field in SCENARIO_REQUIRED_NUMERIC_FIELDS
    ):
        return None
    if any(field not in value for field in ("roi", "modeledDeliveryCostReduction")) or not all(
        _valid_nullable_modeled_number(value.get(field))
        for field in ("roi", "modeledDeliveryCostReduction")
    ) or _modeled_number(value.get("taskTimeReduction")) is None:
        return None
    phases = value.get("phases")
    if (
        not isinstance(phases, dict) or set(phases) != set(BENCHMARK_PHASES)
        or any(not _valid_phase_scenario(phases[name], measurements[name]) for name in BENCHMARK_PHASES)
    ):
        return None
    expected_phase_manual: dict[str, float | None] = {}
    for name in ("planning", "research", "validation"):
        evidence = usage_phases[name]
        assumption = scenario_config[name]
        reviewable_field = "uncachedInputTokens" if name == "research" else "outputTokens"
        reviewable = _modeled_number(evidence.get(reviewable_field))
        reasoning = 0.0 if name == "research" else _modeled_number(evidence.get("reasoningTokens"))
        tool_calls = _modeled_number(evidence.get("toolCalls"))
        tool_active = _modeled_number(evidence.get("toolActiveSeconds"))
        if any(number is None or number < 0 for number in (
            reviewable, reasoning, tool_calls, tool_active,
        )):
            return None
        weighted_reasoning = _checked_product(reasoning, assumption["reasoningTokenWeight"])
        weighted_tokens = (
            _checked_add(reviewable, weighted_reasoning)
            if weighted_reasoning is not None else None
        )
        token_minutes = (
            _checked_divide(
                weighted_tokens * assumption["relevantTokenFraction"],
                assumption["tokensPerMinute"],
            )
            if weighted_tokens is not None else None
        )
        interaction_minutes = _checked_product(
            tool_calls, assumption["interactionMinutesPerTool"],
        )
        expected = (
            _checked_add(token_minutes, interaction_minutes)
            if token_minutes is not None and interaction_minutes is not None else None
        )
        if name == "validation" and expected is not None:
            expected = _checked_add(expected, tool_active / 60)
        expected_phase_manual[name] = expected
    coding_config = scenario_config["coding"]
    expected_phase_manual["coding"] = _checked_divide(
        retained_source_characters * coding_config["manualEntryFraction"],
        characters_per_word * coding_config["wordsPerMinute"],
    )
    expected_phase_manual["unclassified"] = _checked_product(
        _modeled_number(measurements["unclassified"]["measuredAiMinutes"]),
        scenario_config["unclassifiedManualMultiplier"],
    )
    if any(
        expected is None
        or not _close(_modeled_number(phases[name]["estimatedManualMinutes"]), expected)
        for name, expected in expected_phase_manual.items()
    ):
        return None
    scenario_numbers = {
        field: _modeled_number(value[field])
        for field in (*SCENARIO_REQUIRED_NUMERIC_FIELDS, "taskTimeReduction")
    }
    if any(number is None for number in scenario_numbers.values()):
        return None
    manual_minutes = scenario_numbers["estimatedManualMinutes"]
    saved_minutes = scenario_numbers["estimatedMinutesSaved"]
    manual_cost = scenario_numbers["estimatedManualLaborCostUsd"]
    assisted_labor_cost = scenario_numbers["estimatedAiAssistedLaborCostUsd"]
    assisted_total_cost = scenario_numbers["estimatedAiAssistedTotalCostUsd"]
    gross_savings = scenario_numbers["estimatedGrossCostSavingsUsd"]
    benefit = scenario_numbers["estimatedBenefitUsd"]
    net_value = scenario_numbers["netValueUsd"]
    task_reduction = scenario_numbers["taskTimeReduction"]
    break_even = scenario_numbers["breakEvenManualMinutes"]
    if any(number < 0 for number in (
        manual_minutes, manual_cost, assisted_labor_cost, assisted_total_cost, break_even,
    )):
        return None
    phase_manual = _finite_sum(
        _modeled_number(phases[name]["estimatedManualMinutes"]) for name in BENCHMARK_PHASES
    )
    phase_saved = _finite_sum(
        _modeled_number(phases[name]["estimatedMinutesSaved"]) for name in BENCHMARK_PHASES
    )
    if phase_manual is None or phase_saved is None or measured_minutes <= 0:
        return None
    labor_cost_per_minute = _checked_divide(assisted_labor_cost, measured_minutes)
    if labor_cost_per_minute is None or labor_cost_per_minute <= 0:
        return None
    expected_manual_cost = _checked_product(manual_minutes, labor_cost_per_minute)
    expected_total_cost = _checked_add(assisted_labor_cost, ai_cost)
    expected_gross_savings = (
        _checked_subtract(manual_cost, expected_total_cost)
        if expected_total_cost is not None else None
    )
    expected_saved = _checked_subtract(manual_minutes, measured_minutes)
    expected_net = _checked_subtract(benefit, ai_cost)
    expected_task_reduction = _checked_divide(saved_minutes, manual_minutes) if manual_minutes != 0 else 0
    expected_delivery_reduction = (
        _checked_divide(gross_savings, manual_cost) if manual_cost > 0 else None
    )
    expected_roi = _checked_divide(net_value, ai_cost) if ai_cost > 0 else None
    if (
        any(item is None for item in (
            expected_manual_cost, expected_total_cost, expected_gross_savings,
            expected_saved, expected_net, expected_task_reduction,
        ))
        or not _close(phase_manual, manual_minutes)
        or not _close(phase_saved, saved_minutes)
        or not _close(saved_minutes, expected_saved)
        or not _close(manual_cost, expected_manual_cost)
        or not _close(assisted_total_cost, expected_total_cost)
        or not _close(gross_savings, expected_gross_savings)
        or not _close(net_value, expected_net)
        or not _close(task_reduction, expected_task_reduction)
        or not _nullable_identity(value.get("modeledDeliveryCostReduction"), expected_delivery_reduction)
        or not _nullable_identity(value.get("roi"), expected_roi)
    ):
        return None
    capacity_band = value.get("capacityBand")
    if not isinstance(capacity_band, list) or not capacity_band or any(
        not _valid_capacity_point(
            point,
            measured_minutes=measured_minutes,
            saved_minutes=saved_minutes,
            labor_cost_per_minute=labor_cost_per_minute,
            ai_cost=ai_cost,
        )
        for point in capacity_band
    ):
        return None
    capacities = tuple(_modeled_number(point["capacityRealization"]) for point in capacity_band)
    if any(capacities[index] >= capacities[index + 1] for index in range(len(capacities) - 1)):
        return None
    selected = frozenset(
        capacity
        for capacity, point in zip(capacities, capacity_band, strict=True)
        if _close(_modeled_number(point["estimatedBenefitUsd"]), benefit)
        and _close(_modeled_number(point["netValueUsd"]), net_value)
        and _close(_modeled_number(point["breakEvenManualMinutes"]), break_even)
        and _nullable_identity(point.get("roi"), expected_roi)
    )
    return (labor_cost_per_minute, capacities, selected) if selected else None


def _validated_current_benchmark(
    value: Any,
    *,
    usage: Any,
    source: Any,
    model_config_values: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("formulaVersion") != CURRENT_FORMULA_VERSION:
        return None
    if not _is_numeric_output(value):
        return None
    required_top_level = (
        "measuredAiMinutes", "aiCostUsd", "qualityFactor",
        "retainedSourceCharacters", "typingEquivalentMinutes",
    )
    numbers = {field: _modeled_number(value.get(field)) for field in required_top_level}
    if any(number is None for number in numbers.values()):
        return None
    measured_minutes = numbers["measuredAiMinutes"]
    ai_cost = numbers["aiCostUsd"]
    quality_factor = numbers["qualityFactor"]
    retained_characters = value.get("retainedSourceCharacters")
    typing_minutes = numbers["typingEquivalentMinutes"]
    expected_ai_cost = _modeled_number(usage.get("aiCostUsd")) if isinstance(usage, dict) else None
    usage_phases = usage.get("phases") if isinstance(usage, dict) else None
    loaded_hourly_rate = model_config_values["loadedHourlyRateUsd"]
    expected_capacity = model_config_values["capacityRealization"]
    characters_per_word = model_config_values["charactersPerWord"]
    typing_words_per_minute = model_config_values["typingWordsPerMinute"]
    if (
        measured_minutes <= 0 or ai_cost <= 0 or not 0 <= quality_factor <= 1
        or expected_ai_cost is None or expected_ai_cost <= 0 or ai_cost != expected_ai_cost
        or not isinstance(retained_characters, int) or isinstance(retained_characters, bool)
        or retained_characters < 0 or typing_minutes < 0
        or (retained_characters == 0) != (typing_minutes == 0)
        or not isinstance(usage_phases, dict) or set(usage_phases) != set(BENCHMARK_PHASES)
    ):
        return None
    source_characters = 0
    if isinstance(source, dict) and source.get("source") == "otel_source_delta":
        raw_source_characters = source.get("charactersAdded")
        if (
            not isinstance(raw_source_characters, int)
            or isinstance(raw_source_characters, bool)
            or not 0 <= raw_source_characters <= MAX_SQLITE_INTEGER
        ):
            return None
        source_characters = raw_source_characters
    if retained_characters != source_characters:
        return None
    expected_typing_minutes = _checked_divide(
        retained_characters,
        characters_per_word * typing_words_per_minute,
    )
    if expected_typing_minutes is None or not _close(typing_minutes, expected_typing_minutes):
        return None
    measurements = value.get("phases")
    if (
        not isinstance(measurements, dict) or set(measurements) != set(BENCHMARK_PHASES)
        or any(not _valid_phase_measurement(measurements[name]) for name in BENCHMARK_PHASES)
    ):
        return None
    for name in BENCHMARK_PHASES:
        usage_phase = usage_phases[name]
        allocated_seconds = (
            _modeled_number(usage_phase.get("allocatedSeconds"))
            if isinstance(usage_phase, dict) else None
        )
        measured_seconds = _modeled_number(measurements[name].get("measuredAiSeconds"))
        if (
            allocated_seconds is None or allocated_seconds < 0
            or measured_seconds is None or not _close(measured_seconds, allocated_seconds)
        ):
            return None
    measured_total = _finite_sum(
        _modeled_number(measurements[name]["measuredAiMinutes"]) for name in BENCHMARK_PHASES
    )
    if measured_total is None or not _close(measured_total, measured_minutes):
        return None
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(SCENARIOS):
        return None
    validated = [
        _validated_scenario_result(
            scenarios[name],
            measurements=measurements,
            usage_phases=usage_phases,
            measured_minutes=measured_minutes,
            ai_cost=ai_cost,
            retained_source_characters=retained_characters,
            characters_per_word=characters_per_word,
            scenario_config=model_config_values["scenarios"][name],
        )
        for name in SCENARIOS
    ]
    if any(result is None for result in validated):
        return None
    labor_rates = [result[0] for result in validated]
    capacity_bands = [result[1] for result in validated]
    selected_capacities = [result[2] for result in validated]
    expected_capacity_band = tuple(sorted(set([
        *model_config_values["capacityRealizationBand"], expected_capacity,
    ])))
    if (
        any(not _close(rate, loaded_hourly_rate / 60) for rate in labor_rates)
        or any(
            len(band) != len(expected_capacity_band)
            or any(not _close(left, right) for left, right in zip(band, expected_capacity_band, strict=True))
            for band in capacity_bands
        )
        or any(
            not any(_close(capacity, expected_capacity) for capacity in capacities)
            for capacities in selected_capacities
        )
    ):
        return None
    return value


def _checked_add(left: float, right: float) -> float | None:
    result = left + right
    return result if isfinite(result) else None


def _checked_subtract(left: float, right: float) -> float | None:
    result = left - right
    return result if isfinite(result) else None


def _checked_product(*values: float) -> float | None:
    result = 1.0
    for value in values:
        result *= value
        if not isfinite(result):
            return None
    return result


def _checked_divide(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    result = numerator / denominator
    return result if isfinite(result) else None


def _reset_modeled_totals(totals: dict[str, Any]) -> None:
    totals.update({key: 0.0 for key in MODELED_TOTAL_KEYS})
    totals.update({key: None for key in MODELED_RATIO_KEYS})


def _positive_config_number(value: Any, maximum: float | None = None) -> float | None:
    number = _modeled_number(value)
    if number is None or number <= 0 or maximum is not None and number > maximum:
        return None
    return number


def _config_fraction(value: Any) -> float | None:
    number = _modeled_number(value)
    return number if number is not None and 0 <= number <= 1 else None


def _sanitized_token_assumption(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    relevant_fraction = _config_fraction(value.get("relevantTokenFraction"))
    token_rate = _positive_config_number(value.get("tokensPerMinute"))
    interaction = _modeled_number(value.get("interactionMinutesPerTool"))
    reasoning = _config_fraction(value.get("reasoningTokenWeight", 0))
    if (
        relevant_fraction is None or token_rate is None or interaction is None
        or interaction < 0 or reasoning is None
    ):
        return None
    return {
        "relevantTokenFraction": relevant_fraction,
        "tokensPerMinute": token_rate,
        "interactionMinutesPerTool": interaction,
        "reasoningTokenWeight": reasoning,
    }


def _sanitized_config_scenario(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("coding"), dict):
        return None
    planning = _sanitized_token_assumption(value.get("planning"))
    research = _sanitized_token_assumption(value.get("research"))
    validation = _sanitized_token_assumption(value.get("validation"))
    manual_fraction = _config_fraction(value["coding"].get("manualEntryFraction"))
    coding_rate = _positive_config_number(value["coding"].get("wordsPerMinute"))
    unclassified = _positive_config_number(value.get("unclassifiedManualMultiplier"))
    if (
        planning is None or research is None or validation is None
        or manual_fraction is None or coding_rate is None or unclassified is None
    ):
        return None
    return {
        "planning": planning,
        "research": research,
        "coding": {"manualEntryFraction": manual_fraction, "wordsPerMinute": coding_rate},
        "validation": validation,
        "unclassifiedManualMultiplier": unclassified,
    }


def _sanitized_config_scenarios(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(name not in value for name in SCENARIOS):
        return None
    result = {name: _sanitized_config_scenario(value[name]) for name in SCENARIOS}
    if any(scenario is None for scenario in result.values()):
        return None
    pessimistic, base, optimistic = (result[name] for name in SCENARIOS)
    for phase in ("planning", "research", "validation"):
        if (
            pessimistic[phase]["relevantTokenFraction"] > base[phase]["relevantTokenFraction"]
            or base[phase]["relevantTokenFraction"] > optimistic[phase]["relevantTokenFraction"]
            or pessimistic[phase]["tokensPerMinute"] < base[phase]["tokensPerMinute"]
            or base[phase]["tokensPerMinute"] < optimistic[phase]["tokensPerMinute"]
            or pessimistic[phase]["interactionMinutesPerTool"] > base[phase]["interactionMinutesPerTool"]
            or base[phase]["interactionMinutesPerTool"] > optimistic[phase]["interactionMinutesPerTool"]
            or pessimistic[phase]["reasoningTokenWeight"] > base[phase]["reasoningTokenWeight"]
            or base[phase]["reasoningTokenWeight"] > optimistic[phase]["reasoningTokenWeight"]
        ):
            return None
    if (
        pessimistic["coding"]["manualEntryFraction"] > base["coding"]["manualEntryFraction"]
        or base["coding"]["manualEntryFraction"] > optimistic["coding"]["manualEntryFraction"]
        or pessimistic["coding"]["wordsPerMinute"] < base["coding"]["wordsPerMinute"]
        or base["coding"]["wordsPerMinute"] < optimistic["coding"]["wordsPerMinute"]
        or pessimistic["unclassifiedManualMultiplier"] > base["unclassifiedManualMultiplier"]
        or base["unclassifiedManualMultiplier"] > optimistic["unclassifiedManualMultiplier"]
    ):
        return None
    return result


def _sanitized_phase_patterns(value: Any) -> dict[str, list[str]] | None:
    names = ("planning", "research", "coding", "validation")
    if not isinstance(value, dict) or any(
        not isinstance(value.get(name), list)
        or any(not isinstance(pattern, str) for pattern in value[name])
        for name in names
    ):
        return None
    return {name: list(value[name]) for name in names}


def _sanitized_calibration_sources(value: Any) -> list[dict[str, Any]] | None:
    evidence_classes = {
        "controlled_experiment", "literature_benchmark", "official_statistic",
        "survey_context", "local_measurement", "other",
    }
    support_levels = {"direct", "proxy", "context"}
    required_strings = (
        "title", "publisher", "publishedAt", "url", "evidenceClass", "finding", "limitation",
    )
    if not isinstance(value, list):
        return None
    result = []
    for source in value:
        if (
            not isinstance(source, dict)
            or any(not isinstance(source.get(field), str) for field in required_strings)
            or source.get("evidenceClass") not in evidence_classes
            or not isinstance(source.get("appliesTo"), list)
            or any(not isinstance(item, str) for item in source["appliesTo"])
            or (
                source.get("supportLevels") is not None
                and (
                    not isinstance(source["supportLevels"], dict)
                    or any(
                        not isinstance(key, str) or not isinstance(level, str)
                        or level not in support_levels
                        for key, level in source["supportLevels"].items()
                    )
                )
            )
        ):
            return None
        projected = {field: source[field] for field in required_strings}
        projected["appliesTo"] = list(source["appliesTo"])
        if source.get("supportLevels") is not None:
            projected["supportLevels"] = dict(source["supportLevels"])
        result.append(projected)
    return result


def _sanitized_benchmark_config(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not _is_json_safe(value):
        return None
    result: dict[str, Any] = {}
    capacity = _positive_config_number(value.get("capacityRealization"), 1)
    typing_rate = _positive_config_number(value.get("typingWordsPerMinute"))
    characters_per_word = _positive_config_number(value.get("charactersPerWord"))
    if capacity is not None:
        result["capacityRealization"] = capacity
    if typing_rate is not None:
        result["typingWordsPerMinute"] = typing_rate
    if characters_per_word is not None:
        result["charactersPerWord"] = characters_per_word
    band = value.get("capacityRealizationBand")
    if isinstance(band, list) and band:
        normalized_band = [_positive_config_number(item, 1) for item in band]
        if all(item is not None for item in normalized_band):
            result["capacityRealizationBand"] = normalized_band
    idle_gap = _modeled_number(value.get("maxIdleGapSeconds"))
    if idle_gap is not None and idle_gap >= 0:
        result["maxIdleGapSeconds"] = idle_gap
    if isinstance(value.get("acknowledgedAssumptions"), bool):
        result["acknowledgedAssumptions"] = value["acknowledgedAssumptions"]
    if isinstance(value.get("manualTimeModelSource"), str):
        result["manualTimeModelSource"] = value["manualTimeModelSource"]
    sources = _sanitized_calibration_sources(value.get("calibrationSources"))
    if sources is not None:
        result["calibrationSources"] = sources
    patterns = _sanitized_phase_patterns(value.get("phaseToolPatterns"))
    if patterns is not None:
        result["phaseToolPatterns"] = patterns
    for key in ("presetScenarios", "scenarios"):
        scenarios = _sanitized_config_scenarios(value.get(key))
        if scenarios is not None:
            result[key] = scenarios
    return result or None


def _union_duration_seconds(sessions: Iterable[dict[str, Any]]) -> float:
    intervals = [
        interval
        for session in sessions
        for interval in [_session_interval(session)]
        if interval is not None
    ]
    return union_interval_duration(intervals)


def _session_interval(session: dict[str, Any]) -> tuple[float, float] | None:
    started = _parse_iso(session.get("startedAt"))
    completed = _parse_iso(session.get("completedAt"))
    if started is None or completed is None or completed <= started:
        return None
    return started.timestamp(), completed.timestamp()


def _engaged_seconds(session: dict[str, Any]) -> float:
    usage = session.get("usage")
    if isinstance(usage, dict) and "engagedSeconds" in usage:
        return max(0.0, _number(usage["engagedSeconds"]))
    return 0.0


def _valid_modeled_timing(session: dict[str, Any]) -> bool:
    started = _parse_iso(session.get("startedAt"))
    completed = _parse_iso(session.get("completedAt"))
    duration = _modeled_number(session.get("durationSeconds"))
    if started is None or completed is None or completed <= started or duration is None or duration < 0:
        return False
    usage = session.get("usage")
    if not isinstance(usage, dict) or "engagedSeconds" not in usage:
        return False
    engaged = usage["engagedSeconds"]
    phases = usage.get("phases")
    if (
        not isinstance(engaged, (int, float)) or isinstance(engaged, bool)
        or not isfinite(float(engaged)) or not 0 <= float(engaged) <= duration
        or not isinstance(phases, dict) or set(phases) != set(BENCHMARK_PHASES)
    ):
        return False
    allocated_values = []
    for name in BENCHMARK_PHASES:
        phase = phases[name]
        if not isinstance(phase, dict):
            return False
        allocated = _modeled_number(phase.get("allocatedSeconds"))
        if allocated is None or allocated < 0:
            return False
        allocated_values.append(allocated)
    allocated_total = _finite_sum(allocated_values)
    interval_duration = (completed - started).total_seconds()
    return (
        allocated_total is not None
        and _close(duration, interval_duration)
        and _close(allocated_total, float(engaged))
    )


def _assisted_seconds(sessions: list[dict[str, Any]]) -> float:
    """Engaged time drives the model, but it can never exceed the union of session windows."""
    positioned = [session for session in sessions if _session_interval(session) is not None]
    return min(
        sum(_engaged_seconds(session) for session in positioned),
        _union_duration_seconds(positioned),
    )


class ValueStore:
    def __init__(
        self,
        database_path: Path,
        session_directory: Path,
        trace_archive: Path,
        config_path: Path,
        chat_log_root: Path | None = None,
        *, isolate_log_io: bool = False,
    ) -> None:
        self.database_path = database_path
        self.session_directory = session_directory
        self.trace_archive = trace_archive
        self.config_path = config_path
        self.chat_log_root = chat_log_root
        self.isolate_log_io = isolate_log_io
        self._log_discovery_error: str | None = None
        self._log_retry_at = 0.0
        self._lock = threading.RLock()
        self._trace_signature: object | None = None
        self._session_signature: tuple[tuple[str, int, int], ...] | None = None
        self._config_signature: tuple[int, int] | None = None
        self._chat_log_signature: tuple[tuple[str, int, int], ...] | None = None
        self._chat_session_ids: set[str] = set()
        self._chat_log_index: dict[str, tuple[Path, int, int]] = {}
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            value = _json_loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _config_section(config: dict[str, Any], key: str) -> dict[str, Any]:
        value = config.get(key)
        return value if isinstance(value, dict) else {}

    def _model_config_values(self) -> dict[str, Any] | None:
        config = self._config()
        loaded_hourly_rate = _positive_config_number(config.get("loadedHourlyRateUsd"))
        benchmark = _sanitized_benchmark_config(config.get("benchmark"))
        if (
            loaded_hourly_rate is None or benchmark is None
            or benchmark.get("acknowledgedAssumptions") is not True
            or "capacityRealization" not in benchmark
            or "typingWordsPerMinute" not in benchmark
            or "charactersPerWord" not in benchmark
            or "scenarios" not in benchmark
        ):
            return None
        capacity_band = benchmark.get("capacityRealizationBand", [0.25, 0.5, 0.75])
        if not isinstance(capacity_band, list) or not capacity_band:
            return None
        return {
            "loadedHourlyRateUsd": loaded_hourly_rate,
            "capacityRealization": benchmark["capacityRealization"],
            "capacityRealizationBand": capacity_band,
            "typingWordsPerMinute": benchmark["typingWordsPerMinute"],
            "charactersPerWord": benchmark["charactersPerWord"],
            "scenarios": benchmark["scenarios"],
        }

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    experiment TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    ai_cost_usd REAL NOT NULL,
                    ai_credits REAL NOT NULL,
                    chat_spans INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER NOT NULL,
                    uncached_input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    retained_source_characters INTEGER NOT NULL,
                    source_evidence_complete INTEGER NOT NULL,
                    usage_json TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    benchmark_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompts (
                    prompt_id TEXT PRIMARY KEY,
                    experiment TEXT NOT NULL REFERENCES sessions(experiment) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    captured_content_length INTEGER NOT NULL,
                    model_requests INTEGER NOT NULL,
                    tool_calls INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    ai_cost_usd REAL NOT NULL,
                    models_json TEXT NOT NULL,
                    UNIQUE(experiment, ordinal)
                );

                CREATE TABLE IF NOT EXISTS session_time_index (
                    experiment TEXT PRIMARY KEY REFERENCES sessions(experiment) ON DELETE CASCADE,
                    completed_at_epoch REAL
                );

                CREATE TABLE IF NOT EXISTS otel_records (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_key TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    started_at_milliseconds INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_completed_at ON sessions(completed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_session_time_epoch ON session_time_index(completed_at_epoch);
                CREATE INDEX IF NOT EXISTS idx_prompts_experiment ON prompts(experiment, ordinal);
                CREATE INDEX IF NOT EXISTS idx_otel_records_session_cursor
                ON otel_records(session_id, cursor);

                CREATE TRIGGER IF NOT EXISTS trg_session_time_insert
                AFTER INSERT ON sessions
                BEGIN
                    INSERT OR IGNORE INTO session_time_index VALUES (NEW.experiment, NULL);
                END;

                CREATE TRIGGER IF NOT EXISTS trg_session_time_update
                AFTER UPDATE OF completed_at ON sessions
                BEGIN
                    INSERT INTO session_time_index VALUES (NEW.experiment, NULL)
                    ON CONFLICT(experiment) DO UPDATE SET completed_at_epoch=NULL;
                END;
                """
            )
            prompt_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(prompts)")
            }
            if "ai_credits" not in prompt_columns:
                connection.execute(
                    "ALTER TABLE prompts ADD COLUMN ai_credits REAL NOT NULL DEFAULT 0"
                )
            if "usage_source" not in prompt_columns:
                connection.execute(
                    "ALTER TABLE prompts ADD COLUMN usage_source TEXT NOT NULL DEFAULT 'otel_trace'"
                )
            missing_time_rows = connection.execute(
                """
                SELECT sessions.experiment, sessions.completed_at
                FROM sessions
                LEFT JOIN session_time_index USING (experiment)
                WHERE session_time_index.experiment IS NULL
                """
            ).fetchall()
            connection.executemany(
                "INSERT INTO session_time_index VALUES (?, ?)",
                (
                    (row["experiment"], _iso_epoch(row["completed_at"]))
                    for row in missing_time_rows
                ),
            )
            self._remove_wrong_source_prompts(connection)
            initialize_indexing(connection)
            connection.commit()

    @staticmethod
    def _otel_trace_rows(payload: Any, ingested_at: str) -> list[tuple[str, str, int, str, str]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("resourceSpans"), list):
            raise ValueError("OTLP trace payload must contain resourceSpans.")
        records: list[tuple[str, str, int, str, str]] = []
        for resource_span in payload["resourceSpans"]:
            if not isinstance(resource_span, dict):
                continue
            resource = resource_span.get("resource")
            resource_attributes = _attributes(
                resource.get("attributes") if isinstance(resource, dict) else None
            )
            if resource_attributes.get("service.name") != "copilot-chat":
                continue
            session_id = resource_attributes.get("session.id")
            if not isinstance(session_id, str) or not session_id:
                continue
            scope_spans = resource_span.get("scopeSpans")
            if not isinstance(scope_spans, list):
                continue
            for scope_span in scope_spans:
                if not isinstance(scope_span, dict) or not isinstance(scope_span.get("spans"), list):
                    continue
                for span in scope_span["spans"]:
                    if not isinstance(span, dict):
                        continue
                    started = _nanoseconds_to_milliseconds(span.get("startTimeUnixNano"))
                    if started <= 0:
                        continue
                    trace_id = span.get("traceId")
                    span_id = span.get("spanId")
                    identity = "|".join((
                        session_id,
                        str(trace_id or ""),
                        str(span_id or ""),
                    )) if trace_id and span_id else "|".join((
                        session_id,
                        str(span.get("name") or ""),
                        str(span.get("startTimeUnixNano") or ""),
                    ))
                    record_key = sha256(identity.encode("utf-8")).hexdigest()
                    record = {
                        "resourceSpans": [{
                            **{
                                key: resource_span[key]
                                for key in ("resource", "schemaUrl")
                                if key in resource_span
                            },
                            "scopeSpans": [{
                                **{
                                    key: scope_span[key]
                                    for key in ("scope", "schemaUrl")
                                    if key in scope_span
                                },
                                "spans": [span],
                            }],
                        }],
                    }
                    records.append((
                        record_key,
                        session_id,
                        started,
                        json.dumps(record, separators=(",", ":")),
                        ingested_at,
                    ))
        return records

    def _insert_otel_records(self, records: list[tuple[str, str, int, str, str]]) -> int:
        if not records:
            return 0
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            accepted = 0
            changed_sessions: dict[str, int] = {}
            for record in records:
                inserted = connection.execute("""
                INSERT INTO otel_records (
                    record_key, session_id, started_at_milliseconds, record_json, ingested_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(record_key) DO NOTHING
                RETURNING cursor
                """, record).fetchone()
                if inserted:
                    accepted += 1
                    changed_sessions[record[1]] = inserted["cursor"]
                    record_conversations(connection, record[1], record[3])
            for session_id, cursor in changed_sessions.items():
                enqueue_session(connection, session_id, cursor)
            connection.commit()
        return accepted

    def ingest_otlp_traces(self, payload: Any) -> dict[str, int]:
        require_storage_capacity(self.database_path.parent)
        records = self._otel_trace_rows(
            payload, datetime.now(timezone.utc).isoformat()
        )
        accepted = self._insert_otel_records(records)
        return {"received": len(records), "accepted": accepted}

    def _import_legacy_trace_archive_once(self) -> int:
        with closing(self._connect()) as connection:
            completed = connection.execute(
                "SELECT 1 FROM store_metadata WHERE key = ?",
                (LEGACY_TRACE_IMPORT_KEY,),
            ).fetchone()
        if completed:
            return 0

        before = _trace_archive_snapshot(self.trace_archive)
        ingested_at = datetime.now(timezone.utc).isoformat()
        accepted = 0
        batch: list[tuple[str, str, int, str, str]] = []
        for archive_path, _modified, _size in before:
            for payload in _iter_json_lines(archive_path):
                batch.extend(self._otel_trace_rows(payload, ingested_at))
                if len(batch) >= OTEL_IMPORT_BATCH_SIZE:
                    accepted += self._insert_otel_records(batch)
                    batch.clear()
        accepted += self._insert_otel_records(batch)

        if before == _trace_archive_snapshot(self.trace_archive):
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR REPLACE INTO store_metadata (key, value) VALUES (?, ?)",
                    (LEGACY_TRACE_IMPORT_KEY, datetime.now(timezone.utc).isoformat()),
                )
                connection.commit()
        return accepted

    def otel_records(self, after: int = 0, limit: int = 1_000) -> dict[str, Any]:
        bounded_limit = min(10_000, max(1, limit))
        selected: list[sqlite3.Row] = []
        size = 0
        has_more = False
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT cursor, record_json
                FROM otel_records
                WHERE cursor > ?
                ORDER BY cursor
                LIMIT ?
                """,
                (max(0, after), bounded_limit + 1),
            )
            for row in rows:
                record_size = len(row["record_json"].encode("utf-8"))
                if len(selected) >= bounded_limit or selected and size + record_size > OTEL_INBOX_PAGE_BYTES:
                    has_more = True
                    break
                selected.append(row)
                size += record_size
        return {
            "records": [_json_loads(row["record_json"]) for row in selected],
            "nextCursor": selected[-1]["cursor"] if selected else max(0, after),
            "hasMore": has_more,
        }

    @staticmethod
    def _remove_wrong_source_prompts(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM prompts
            WHERE (
                usage_source = 'otel_trace'
                AND experiment IN (
                    SELECT experiment
                    FROM sessions
                    WHERE json_extract(usage_json, '$.source') = 'copilot_turn_log'
                )
            ) OR (
                usage_source = 'copilot_turn_log'
                AND experiment IN (
                    SELECT experiment
                    FROM sessions
                    WHERE COALESCE(json_extract(usage_json, '$.source'), 'otel_traces')
                          != 'copilot_turn_log'
                )
            )
            """
        )

    def _refresh_prompt_source_invariant(self) -> None:
        with closing(self._connect()) as connection:
            self._remove_wrong_source_prompts(connection)
            connection.commit()

    def index_once(self) -> None:
        with self._lock:
            self._import_legacy_trace_archive_once()
            with closing(self._connect()) as connection:
                bootstrap_indexing(connection)
            self.session_directory.mkdir(parents=True, exist_ok=True)
            self._index_sessions()
            if monotonic() >= self._log_retry_at:
                try:
                    self._refresh_chat_log_index()
                    self._log_discovery_error = None
                except (OSError, IndexingBlocked):
                    self._log_discovery_error = "log_discovery_unavailable"
                    self._log_retry_at = monotonic() + 30
            with closing(self._connect()) as connection:
                if self._log_discovery_error is None:
                    stage_log_changes(connection, self._chat_log_index)
                self._chat_session_ids.update(row[0] for row in connection.execute(
                    "SELECT DISTINCT conversation_id FROM indexing_conversations"
                ))
                config_signature = sha256(json.dumps(self._config(), sort_keys=True).encode("utf-8")).hexdigest()
                previous = connection.execute(
                    "SELECT value FROM store_metadata WHERE key = 'indexing_config'"
                ).fetchone()
                if previous is None or previous[0] != config_signature:
                    with connection:
                        for row in connection.execute("SELECT work_key FROM indexing_work").fetchall():
                            enqueue_work(connection, row[0])
                        connection.execute("""
                            INSERT INTO store_metadata VALUES ('indexing_config', ?)
                            ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """, (config_signature,))
                with connection:
                    connection.execute("""
                        INSERT INTO store_metadata VALUES ('indexing_last_discovery', ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """, (str(time()),))
                    connection.execute("""
                        INSERT INTO store_metadata VALUES ('indexing_discovery_error', ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """, (self._log_discovery_error or "",))
            self._index_prompts()
            self._chat_log_signature = self._current_chat_log_signature()
            self._prune_prompts()

    def indexing_status(self, experiment: str | None = None) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return indexing_status(connection, experiment)

    def _refresh_chat_log_index(self) -> None:
        if self.isolate_log_io and self.chat_log_root is not None:
            self._chat_log_index = {
                identifier: (Path(entry[0]), entry[1], entry[2])
                for identifier, entry in isolated_log_io("discover", self.chat_log_root).items()
            }
        else:
            self._chat_log_index = discover_logs(self.chat_log_root)

    def _chat_log_path(self, chat_session_id: str) -> Path | None:
        entry = self._chat_log_index.get(chat_session_id)
        return entry[0] if entry else None

    def _current_chat_log_signature(self) -> tuple[tuple[str, int, int], ...]:
        signature: list[tuple[str, int, int]] = []
        for chat_session_id in sorted(self._chat_session_ids):
            entry = self._chat_log_index.get(chat_session_id)
            if entry:
                path, modified, size = entry
                signature.append((str(path), modified, size))
        return tuple(signature)

    def _prompt_storage_config(self) -> tuple[bool, int]:
        config = self._config()
        prompt_storage = self._config_section(config, "promptStorage")
        raw_enabled = prompt_storage.get("enabled", True)
        enabled = raw_enabled if isinstance(raw_enabled, bool) else True
        raw_retention_days = prompt_storage.get("retentionDays", 30)
        retention_days = (
            raw_retention_days
            if isinstance(raw_retention_days, int) and not isinstance(raw_retention_days, bool)
            else 30
        )
        return enabled, min(3_650, max(1, retention_days))

    def _prune_prompts(self) -> None:
        _, retention_days = self._prompt_storage_config()
        retention_cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM prompts WHERE started_at < ?", (retention_cutoff.isoformat(),))
            connection.commit()

    def _index_sessions(
        self, connection: sqlite3.Connection | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> None:
        if connection is None:
            self.session_directory.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                stage_artifacts(connection, self.session_directory, _json_loads)
            return
        if artifacts:
            for artifact in artifacts:
                raw_usage = artifact.get("usage")
                usage = raw_usage if isinstance(raw_usage, dict) else {}
                raw_source = artifact.get("source")
                source = raw_source if isinstance(raw_source, dict) else {}
                raw_benchmark = artifact.get("benchmark")
                benchmark = raw_benchmark if isinstance(raw_benchmark, dict) else None
                started_at = str(artifact.get("startedAt") or "")
                raw_completed_at = artifact.get("completedAt")
                completed_at = "" if raw_completed_at is None else str(raw_completed_at)
                parsed_started_at = _parse_iso(started_at)
                parsed_completed_at = _parse_iso(completed_at)
                duration_seconds = (
                    max(0, (parsed_completed_at - parsed_started_at).total_seconds())
                    if parsed_started_at is not None and parsed_completed_at is not None
                    else 0
                )
                connection.execute(
                    """
                    INSERT INTO sessions VALUES (
                        :experiment, :started_at, :completed_at, :status, :duration_seconds,
                        :ai_cost_usd, :ai_credits, :chat_spans, :input_tokens,
                        :cache_read_tokens, :uncached_input_tokens, :output_tokens,
                        :reasoning_tokens, :retained_source_characters,
                        :source_evidence_complete, :usage_json, :source_json,
                        :benchmark_json, :updated_at
                    )
                    ON CONFLICT(experiment) DO UPDATE SET
                        started_at=excluded.started_at,
                        completed_at=excluded.completed_at,
                        status=excluded.status,
                        duration_seconds=excluded.duration_seconds,
                        ai_cost_usd=excluded.ai_cost_usd,
                        ai_credits=excluded.ai_credits,
                        chat_spans=excluded.chat_spans,
                        input_tokens=excluded.input_tokens,
                        cache_read_tokens=excluded.cache_read_tokens,
                        uncached_input_tokens=excluded.uncached_input_tokens,
                        output_tokens=excluded.output_tokens,
                        reasoning_tokens=excluded.reasoning_tokens,
                        retained_source_characters=excluded.retained_source_characters,
                        source_evidence_complete=excluded.source_evidence_complete,
                        usage_json=excluded.usage_json,
                        source_json=excluded.source_json,
                        benchmark_json=excluded.benchmark_json,
                        updated_at=excluded.updated_at
                    """,
                    {
                        "experiment": artifact["experiment"],
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "status": str(artifact.get("status") or "unknown"),
                        "duration_seconds": duration_seconds,
                        "ai_cost_usd": _number(usage.get("aiCostUsd")),
                        "ai_credits": _number(usage.get("aiCredits")),
                        "chat_spans": _integer(usage.get("chatSpans")),
                        "input_tokens": _integer(usage.get("inputTokens")),
                        "cache_read_tokens": _integer(usage.get("cacheReadTokens")),
                        "uncached_input_tokens": _integer(usage.get("uncachedInputTokens")),
                        "output_tokens": _integer(usage.get("outputTokens")),
                        "reasoning_tokens": _integer(usage.get("reasoningTokens")),
                        "retained_source_characters": _integer(source.get("charactersAdded")),
                        "source_evidence_complete": 1 if source.get("source") == "otel_source_delta" else 0,
                        "usage_json": json.dumps(raw_usage, separators=(",", ":")),
                        "source_json": json.dumps(raw_source, separators=(",", ":")),
                        "benchmark_json": (
                            json.dumps(raw_benchmark, separators=(",", ":"))
                            if raw_benchmark is not None else None
                        ),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                connection.execute(
                    """
                    INSERT INTO session_time_index VALUES (?, ?)
                    ON CONFLICT(experiment) DO UPDATE SET
                        completed_at_epoch=excluded.completed_at_epoch
                    """,
                    (artifact["experiment"], _iso_epoch(completed_at)),
                )
    def _trace_sessions(
        self,
        trace_paths: Iterable[Path] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if trace_paths is not None:
            return collect_trace_sessions(trace_paths)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_json FROM otel_records ORDER BY cursor"
            ).fetchall()
        if rows:
            return collect_trace_sessions_from_records(
                _json_loads(row["record_json"]) for row in rows
            )
        return collect_trace_sessions(_trace_archive_paths(self.trace_archive))

    def _current_trace_signature(self) -> object:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "SELECT COALESCE(MAX(cursor), 0) AS cursor FROM otel_records"
            ).fetchone()["cursor"]
        return ("inbox", cursor) if cursor else _trace_archive_signature(
            _trace_archive_snapshot(self.trace_archive)
        )

    def _stable_trace_sessions(
        self,
        max_attempts: int = 3,
    ) -> tuple[dict[str, dict[str, Any]], object]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT cursor, record_json FROM otel_records ORDER BY cursor"
            ).fetchall()
        if rows:
            return (
                collect_trace_sessions_from_records(
                    _json_loads(row["record_json"]) for row in rows
                ),
                ("inbox", rows[-1]["cursor"]),
            )
        for _attempt in range(max_attempts):
            before = _trace_archive_snapshot(self.trace_archive)
            trace_sessions = self._trace_sessions(
                [candidate for candidate, _modified, _size in before]
            )
            after = _trace_archive_snapshot(self.trace_archive)
            if before == after:
                return trace_sessions, _trace_archive_signature(after)
        raise RuntimeError(
            f"Trace archive changed during {max_attempts} consecutive reads."
        )

    def _scrub_prompt_content(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("UPDATE prompts SET content = '' WHERE content <> ''")
            connection.commit()

    def _index_prompts(self) -> object:
        content_enabled, _ = self._prompt_storage_config()
        if not content_enabled:
            self._scrub_prompt_content()
        with closing(self._connect()) as connection:
            pending = connection.execute(
                """SELECT * FROM indexing_work
                WHERE requested_version > indexed_version AND next_attempt_at <= ?
                ORDER BY last_attempt_at, pending_since, work_key LIMIT 16""", (time(),)
            ).fetchall()
        deadline = monotonic() + 5
        completed = 0
        for work in pending:
            if monotonic() >= deadline:
                break
            try:
                artifact, groups = (
                    prepare_in_process(
                        self.database_path, dict(work), content_enabled,
                        self._chat_log_index, self._log_discovery_error,
                    ) if self.isolate_log_io else self._prepare_index_work(work, content_enabled)
                )
                with closing(self._connect()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self._index_sessions(connection, [artifact])
                    if groups is not None:
                        write_prompts(connection, artifact["experiment"], groups)
                    else:
                        usage = artifact.get("usage")
                        source = "copilot_turn_log" if isinstance(usage, dict) and usage.get("source") == "copilot_turn_log" else "otel_trace"
                        connection.execute(
                            "DELETE FROM prompts WHERE experiment = ? AND usage_source != ?",
                            (artifact["experiment"], source),
                        )
                    connection.execute(
                        "UPDATE indexing_work SET experiment = ? WHERE work_key = ?",
                        (artifact["experiment"], work["work_key"]),
                    )
                    complete_work(connection, work)
                completed += 1
            except Exception as failure:
                code = failure.code if isinstance(failure, IndexingBlocked) else "indexing_error"
                with closing(self._connect()) as connection, connection:
                    retry_work(connection, work, code)
        return completed

    def _prepare_index_work(
        self, work: sqlite3.Row, content_enabled: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            candidates = connection.execute("SELECT file_name, experiment, error_code FROM indexing_artifacts").fetchall()
            matches = [row for row in candidates if (
                row["experiment"] == work["experiment"] if work["experiment"] else
                public_session_digest(row["experiment"]) == work["session_digest"]
            )]
            if len(matches) != 1:
                raise IndexingBlocked("waiting_for_artifact" if not matches else "ambiguous_artifact")
            staged = matches[0]
            if staged["error_code"]:
                raise IndexingBlocked(staged["error_code"])
            payload = connection.execute(
                "SELECT payload_json FROM indexing_artifacts WHERE file_name = ?", (staged["file_name"],)
            ).fetchone()[0]
            artifact = _json_loads(payload)
            if not isinstance(artifact, dict):
                raise IndexingBlocked("invalid_artifact")
            input_cursor = artifact.get("inboxCursor")
            if work["session_id"] and input_cursor is None and isinstance(artifact.get("calculationVersion"), int):
                raise IndexingBlocked("waiting_for_worker")
            if input_cursor is not None and (
                not isinstance(input_cursor, int) or isinstance(input_cursor, bool)
                or input_cursor < work["latest_cursor"]
            ):
                raise IndexingBlocked("waiting_for_worker")
            if not work["session_id"]:
                return artifact, None
            records = connection.execute("""
                SELECT record_json FROM otel_records
                WHERE session_id = ? AND cursor <= ? ORDER BY cursor
            """, (work["session_id"], work["latest_cursor"]))
            traces = collect_trace_sessions_from_records(_json_loads(row[0]) for row in records)
        trace = traces.get(work["session_id"])
        if trace is None:
            raise IndexingBlocked("waiting_for_telemetry")
        started = _iso_milliseconds(str(artifact.get("startedAt") or ""))
        if started > 0 and trace["start"] > started:
            raise IndexingBlocked("incomplete_retained_history")
        usage = artifact.get("usage") or {}
        direct_turns: list[dict[str, Any]] = []
        if isinstance(usage, dict) and usage.get("source") == "copilot_turn_log":
            if self._log_discovery_error:
                raise IndexingBlocked(self._log_discovery_error)
            ended = _iso_milliseconds(str(artifact.get("completedAt") or ""))
            identifiers = conversation_ids(trace)
            if not identifiers:
                raise IndexingBlocked("waiting_for_logs")
            for identifier in identifiers:
                path = self._chat_log_path(identifier)
                if path is None:
                    raise IndexingBlocked("waiting_for_logs")
                turns = (
                    isolated_log_io("turns", path, started, ended)
                    if self.isolate_log_io else _read_copilot_turns(path, started, ended)
                )
                direct_turns.extend(
                    {**turn, "conversation_id": identifier}
                    for turn in turns
                )
        groups = group_prompts(trace, direct_turns, content_enabled)
        if not all(prompt_group_counters_valid(group) for group in groups):
            raise IndexingBlocked("invalid_prompt_counters")
        if isinstance(usage, dict) and usage.get("source") == "copilot_turn_log" and input_cursor is not None:
            counters = {
                "model_requests": "chatSpans", "input_tokens": "inputTokens",
                "cache_read_tokens": "cacheReadTokens", "output_tokens": "outputTokens",
                "reasoning_tokens": "reasoningTokens",
            }
            if any(sum(group[field] for group in groups) != usage.get(key) for field, key in counters.items()):
                raise IndexingBlocked("waiting_for_worker")
            if not isclose(sum(group["ai_credits"] for group in groups), _number(usage.get("aiCredits")), rel_tol=1e-9, abs_tol=1e-9):
                raise IndexingBlocked("waiting_for_worker")
        return artifact, groups

    def _session_payload(
        self,
        row: sqlite3.Row,
        scenario: str,
        *,
        raw_usage: bool = False,
        model_config_values: dict[str, Any] | None | object = _UNSET,
    ) -> dict[str, Any]:
        stored_usage = _json_loads(row["usage_json"])
        stored_source = _json_loads(row["source_json"])
        stored_benchmark = _json_loads(row["benchmark_json"]) if row["benchmark_json"] else None
        usage = stored_usage if raw_usage else _public_object(stored_usage)
        source = _public_object(stored_source)
        raw_formula_version = stored_benchmark.get("formulaVersion") if isinstance(stored_benchmark, dict) else None
        formula_version = (
            raw_formula_version
            if isinstance(raw_formula_version, int)
            and not isinstance(raw_formula_version, bool)
            and 0 <= raw_formula_version <= MAX_FORMULA_VERSION
            else None
        )
        current_formula = formula_version == CURRENT_FORMULA_VERSION
        config_values = (
            self._model_config_values()
            if model_config_values is _UNSET else model_config_values
        )
        if current_formula:
            benchmark = (
                _validated_current_benchmark(
                    stored_benchmark,
                    usage=stored_usage,
                    source=stored_source,
                    model_config_values=config_values,
                )
                if config_values is not None
                and _complete_usage(stored_usage, row["duration_seconds"])
                else None
            )
        else:
            benchmark = (
                _public_object(stored_benchmark)
                if stored_benchmark is not None and formula_version is not None
                else None
            )
        timing_payload = {
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
            "durationSeconds": _number(row["duration_seconds"]),
            "usage": usage,
        }
        if current_formula and benchmark is not None and not _valid_modeled_timing(timing_payload):
            benchmark = None
        scenarios = benchmark.get("scenarios") if benchmark else None
        selected = scenarios.get(scenario) if current_formula and isinstance(scenarios, dict) else None
        if not isinstance(selected, dict):
            selected = None
        return {
            "experiment": row["experiment"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
            "status": row["status"],
            "durationSeconds": _number(row["duration_seconds"]),
            "aiCostUsd": _number(row["ai_cost_usd"]),
            "aiCredits": _number(row["ai_credits"]),
            "chatSpans": _integer(row["chat_spans"]),
            "tokens": {
                "input": _integer(row["input_tokens"]),
                "cacheRead": _integer(row["cache_read_tokens"]),
                "uncachedInput": _integer(row["uncached_input_tokens"]),
                "output": _integer(row["output_tokens"]),
                "reasoning": _integer(row["reasoning_tokens"]),
            },
            "retainedSourceCharacters": _integer(row["retained_source_characters"]),
            "sourceEvidenceComplete": bool(row["source_evidence_complete"]),
            "promptCount": 0,
            "latestMessageAt": None,
            "scenario": scenario,
            "scenarioResult": selected,
            "formulaVersion": formula_version,
            "currentFormula": current_formula,
            "modelingStatus": (
                "available" if selected is not None
                else "invalid" if current_formula
                else "unavailable"
            ),
            "usage": usage,
            "source": source,
            "benchmark": benchmark,
        }

    def overview(self, scenario: str = "base", days: int = 30) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario '{scenario}'.")
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM sessions WHERE julianday(completed_at) >= julianday(?) ORDER BY completed_at DESC",
                (cutoff.isoformat(),),
            ).fetchall()
            prompt_summaries = {
                row["experiment"]: row
                for row in connection.execute(
                    """
                    SELECT experiment, COUNT(*) AS count, MAX(started_at) AS latest_message_at
                    FROM prompts
                    GROUP BY experiment
                    """
                )
            }
        model_config_values = self._model_config_values()
        sessions = []
        benchmarked_sessions = []
        totals = {
            "sessions": 0,
            "prompts": 0,
            "durationSeconds": 0.0,
            "aiCostUsd": 0.0,
            "taskTimeReduction": None,
            "estimatedManualMinutes": 0.0,
            "estimatedMinutesSaved": 0.0,
            "estimatedManualLaborCostUsd": 0.0,
            "estimatedAiAssistedLaborCostUsd": 0.0,
            "estimatedAiAssistedTotalCostUsd": 0.0,
            "estimatedGrossCostSavingsUsd": 0.0,
            "modeledDeliveryCostReduction": None,
            "estimatedBenefitUsd": 0.0,
            "netValueUsd": 0.0,
            "roi": None,
            "formulaVersion": CURRENT_FORMULA_VERSION,
            "supersededFormulaSessions": 0,
        }
        aggregate_valid = True
        for row in rows:
            payload = self._session_payload(
                row, scenario, model_config_values=model_config_values,
            )
            prompt_summary = prompt_summaries.get(row["experiment"])
            payload["promptCount"] = prompt_summary["count"] if prompt_summary else 0
            payload["latestMessageAt"] = prompt_summary["latest_message_at"] if prompt_summary else None
            sessions.append(payload)
            totals["sessions"] += 1
            totals["prompts"] += payload["promptCount"]
            for key, value in (
                ("durationSeconds", payload["durationSeconds"]),
                ("aiCostUsd", payload["aiCostUsd"]),
            ):
                result = _checked_add(totals[key], value)
                if result is None:
                    aggregate_valid = False
                else:
                    totals[key] = result
            if payload["benchmark"] and not payload["currentFormula"]:
                totals["supersededFormulaSessions"] += 1
            scenario_result = payload["scenarioResult"] or {}
            if payload["currentFormula"] and not scenario_result:
                aggregate_valid = False
            elif scenario_result:
                if not _valid_modeled_timing(payload):
                    aggregate_valid = False
                    continue
                for key in MODELED_TOTAL_KEYS:
                    value = _modeled_number(scenario_result.get(key))
                    result = _checked_add(totals[key], value) if value is not None else None
                    if result is None:
                        aggregate_valid = False
                    else:
                        totals[key] = result
                benchmarked_sessions.append(payload)

        totals["durationSeconds"] = _union_duration_seconds(sessions)
        modeled_calculation_valid = aggregate_valid
        if benchmarked_sessions:
            if model_config_values is None:
                modeled_calculation_valid = False
            else:
                loaded_hourly_rate = model_config_values["loadedHourlyRateUsd"]
                capacity_realization = model_config_values["capacityRealization"]
                labor_cost_per_minute = loaded_hourly_rate / 60
                assisted_minutes = _assisted_seconds(benchmarked_sessions) / 60
                recalculated = {
                    "estimatedMinutesSaved": _checked_subtract(
                        totals["estimatedManualMinutes"], assisted_minutes,
                    ),
                    "estimatedAiAssistedLaborCostUsd": _checked_product(
                        assisted_minutes, labor_cost_per_minute,
                    ),
                }
                if all(value is not None for value in recalculated.values()):
                    recalculated["estimatedAiAssistedTotalCostUsd"] = _checked_add(
                        recalculated["estimatedAiAssistedLaborCostUsd"], totals["aiCostUsd"],
                    )
                    recalculated["estimatedGrossCostSavingsUsd"] = _checked_subtract(
                        totals["estimatedManualLaborCostUsd"],
                        recalculated["estimatedAiAssistedTotalCostUsd"],
                    ) if recalculated["estimatedAiAssistedTotalCostUsd"] is not None else None
                    recalculated["estimatedBenefitUsd"] = _checked_product(
                        recalculated["estimatedMinutesSaved"], labor_cost_per_minute,
                        capacity_realization,
                    )
                    recalculated["netValueUsd"] = _checked_subtract(
                        recalculated["estimatedBenefitUsd"], totals["aiCostUsd"],
                    ) if recalculated["estimatedBenefitUsd"] is not None else None
                if all(value is not None for value in recalculated.values()):
                    totals.update(recalculated)
                else:
                    modeled_calculation_valid = False
        if not modeled_calculation_valid:
            _reset_modeled_totals(totals)
        if modeled_calculation_valid and totals["aiCostUsd"] > 0:
            totals["roi"] = _checked_divide(totals["netValueUsd"], totals["aiCostUsd"])
        if modeled_calculation_valid and totals["estimatedManualLaborCostUsd"] > 0:
            totals["modeledDeliveryCostReduction"] = _checked_divide(
                totals["estimatedGrossCostSavingsUsd"], totals["estimatedManualLaborCostUsd"],
            )
        if modeled_calculation_valid and totals["estimatedManualMinutes"] != 0:
            totals["taskTimeReduction"] = _checked_divide(
                totals["estimatedMinutesSaved"], totals["estimatedManualMinutes"],
            )
        return {
            "scenario": scenario,
            "days": days,
            "modelingStatus": (
                "invalid" if not modeled_calculation_valid
                else "available" if benchmarked_sessions
                else "unavailable"
            ),
            "totals": totals,
            "sessions": sessions,
        }

    def session(self, experiment: str, scenario: str = "base") -> dict[str, Any] | None:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario '{scenario}'.")
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM sessions WHERE experiment = ?", (experiment,)).fetchone()
            if not row:
                return None
            prompt_summary = connection.execute(
                """
                SELECT COUNT(*) AS count, MAX(started_at) AS latest_message_at
                FROM prompts
                WHERE experiment = ?
                """,
                (experiment,),
            ).fetchone()
        payload = self._session_payload(
            row,
            scenario,
            model_config_values=self._model_config_values(),
        )
        payload["promptCount"] = prompt_summary["count"]
        payload["latestMessageAt"] = prompt_summary["latest_message_at"]
        return payload

    def prompts(self, experiment: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM prompts WHERE experiment = ? ORDER BY ordinal", (experiment,)
            ).fetchall()
        return [self._prompt_payload(row) for row in rows]

    def export_data(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        with closing(self._connect()) as connection:
            session_rows = connection.execute(
                "SELECT * FROM sessions ORDER BY completed_at DESC"
            ).fetchall()
            prompt_rows = connection.execute(
                "SELECT * FROM prompts ORDER BY experiment, ordinal"
            ).fetchall()

        prompts_by_session: dict[str, list[dict[str, Any]]] = {
            row["experiment"]: [] for row in session_rows
        }
        for row in prompt_rows:
            prompts_by_session.setdefault(row["experiment"], []).append(self._prompt_payload(row))

        model_config_values = self._model_config_values()
        sessions = []
        for row in session_rows:
            payload = self._session_payload(
                row, "base", model_config_values=model_config_values,
            )
            prompts = prompts_by_session.get(row["experiment"], [])
            payload["promptCount"] = len(prompts)
            payload["latestMessageAt"] = max(
                (prompt["startedAt"] for prompt in prompts),
                default=None,
            )
            sessions.append(payload)
        return sessions, prompts_by_session

    def prompt(self, prompt_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM prompts WHERE prompt_id = ?", (prompt_id,)).fetchone()
        return self._prompt_payload(row) if row else None

    def insights(self, days: int = 30) -> dict[str, Any]:
        window = timedelta(days=max(1, days))
        now = datetime.now(timezone.utc)
        cutoff = now - window
        previous_cutoff = cutoff - window
        with closing(self._connect()) as connection:
            session_rows = connection.execute(
                INSIGHTS_SESSION_QUERY,
                (_iso_epoch(previous_cutoff.isoformat()),),
            ).fetchall()
        sessions: list[dict[str, Any]] = []
        previous_sessions: list[dict[str, Any]] = []
        for row in session_rows:
            completed_at = _parse_iso(row["completed_at"])
            payload = self._session_payload(row, "base", raw_usage=True)
            if completed_at is None or completed_at >= cutoff:
                sessions.append(payload)
            elif completed_at >= previous_cutoff:
                previous_sessions.append(payload)
        return build_insights(sessions, [], days, previous_sessions, [], now)

    def _prompt_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        input_tokens = row["input_tokens"]
        cache_read_tokens = row["cache_read_tokens"]
        exact_fields = {
            "ordinal": row["ordinal"],
            "capturedContentLength": row["captured_content_length"],
            "modelRequests": row["model_requests"],
            "toolCalls": row["tool_calls"],
            "inputTokens": input_tokens,
            "cacheReadTokens": cache_read_tokens,
            "outputTokens": row["output_tokens"],
            "reasoningTokens": row["reasoning_tokens"],
        }
        stored_models = _json_loads(row["models_json"])
        model_values = stored_models if isinstance(stored_models, dict) else {}
        models_exact = {
            str(model): str(_nonnegative_integer(requests))
            for model, requests in model_values.items()
        }
        return {
            "promptId": row["prompt_id"],
            "experiment": row["experiment"],
            **{
                key: _safe_json_integer(value)
                for key, value in exact_fields.items()
            },
            **{
                f"{key}Exact": str(value)
                for key, value in exact_fields.items()
            },
            "startedAt": row["started_at"],
            "content": row["content"],
            "contentAvailable": bool(row["content"]),
            "cacheReadRatio": cache_read_tokens / input_tokens if input_tokens else 0,
            "aiCredits": row["ai_credits"],
            "aiCostUsd": row["ai_cost_usd"],
            "usageSource": row["usage_source"],
            "models": {
                model: _safe_json_integer(int(requests))
                for model, requests in models_exact.items()
            },
            "modelsExact": models_exact,
            "roi": None,
            "roiStatus": "prompt_level_attribution_required",
        }

    def methodology(self) -> dict[str, Any]:
        config = self._config()
        benchmark_config = _sanitized_benchmark_config(config.get("benchmark"))
        loaded_hourly_rate = _positive_config_number(config.get("loadedHourlyRateUsd"))
        return {
            "claimKey": "methodology.claim.modeledAiUsageRoi",
            "claim": "Modeled AI Usage ROI",
            "scenarios": list(SCENARIOS),
            "formulaVersion": CURRENT_FORMULA_VERSION,
            "config": {
                "loadedHourlyRateUsd": loaded_hourly_rate,
                "benchmark": benchmark_config or None,
            },
            "formulas": {
                "engagedTime": "W_engaged = measure(union of activity intervals bridging gaps <= G)",
                "phaseAllocation": "T_AI,p = (W_engaged / 60) * (a_p / A_active)",
                "phaseSavings": "T_saved,p,s = T_manual,p,s - T_AI,p",
                "totalSavings": "T_saved,s = sum_p(T_saved,p,s)",
                "portfolioAiTime": "T_AI,portfolio = min(sum_i(W_engaged,i), measure(union_i([t_start,i, t_end,i]))) / 60",
                "portfolioSavings": "T_saved,portfolio,s = sum_i(T_manual,i,s) - T_AI,portfolio",
                "manualLaborCost": "C_manual,s = (T_manual,s / 60) * H",
                "aiAssistedTotalCost": "C_assisted = (T_AI / 60) * H + C_AI",
                "portfolioAssistedCost": "C_assisted,portfolio = (T_AI,portfolio / 60) * H + sum_i(C_AI,i)",
                "grossCostSavings": "Delta_C_gross,s = C_manual,s - C_assisted",
                "deliveryCostReduction": "R_s = Delta_C_gross,s / C_manual,s",
                "benefit": "B_s = (T_saved,s / 60) * H * rho",
                "breakEven": "T_manual,break-even = T_AI + C_AI / ((H / 60) * rho)",
                "aiCost": "C_AI = sum(copilot_usage_nano_aiu) / 1e11",
                "roi": "ROI_s = (B_s - C_AI) / C_AI",
            },
        }