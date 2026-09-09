from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import fsum, isclose, isfinite
import re
from typing import Any, Literal


Zone = Literal["typical", "elevated", "high", "unavailable", "descriptive"]
Status = Literal["normal", "watch", "action", "unavailable", "insufficient", "descriptive"]
ReferenceKind = Literal["best_practice", "local_measurement"]
ReferenceSupport = Literal["direct", "proxy", "none"]
SignalDirection = Literal["minimum", "maximum"]

DORA_VALIDATION_URL = "https://dora.dev/capabilities/continuous-integration/"
PROMPT_CACHE_URL = "https://developers.openai.com/api/docs/guides/prompt-caching"
LONG_CONTEXT_URL = "https://arxiv.org/abs/2404.06654"
REASONING_URL = "https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning#reasoning-effort"
AGENT_EVAL_URL = "https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/agent-evaluators"
AZURE_MONITOR_URL = "https://learn.microsoft.com/azure/foundry/openai/monitor-openai-reference"
AZURE_LATENCY_URL = "https://learn.microsoft.com/azure/foundry/openai/how-to/latency"
FINOPS_URL = "https://www.finops.org/framework/capabilities/unit-economics/"
SPACE_URL = "https://queue.acm.org/detail.cfm?id=3454124"

# An OTel session spans an editor-window lifetime rather than a task, so session-scoped values are
# reported descriptively and only after the window settles.
SESSION_SETTLE_SECONDS = 1_800
MAX_FUTURE_CLOCK_SKEW_SECONDS = 300
MINIMUM_SAMPLES = 5
FLAT_TREND_BAND = 0.02
REQUIRED_SESSION_USAGE_COVERAGE = 1.0
ABSOLUTE_ISO_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    category: str
    scope: str
    unit: str
    evidence: str
    description: str
    action: str
    group: str = "behavior"


@dataclass(frozen=True)
class SignalPolicy:
    direction: SignalDirection
    attention: float
    danger: float
    minimum_samples: int = MINIMUM_SAMPLES


INSIGHT_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "cache_read_ratio", "Session cache reuse", "Context", "session", "ratio", "derived",
        "Share of authoritative session input tokens served from the prompt cache.",
        "If cache reuse is unexpectedly low, keep stable instructions, tools, and reusable context at the beginning.",
    ),
    MetricDefinition(
        "uncached_input_per_request", "Uncached input per request", "Context", "request", "tokens", "derived",
        "Authoritative uncached session input tokens divided by model requests.",
        "If cost or latency is rising, narrow file reads and tool output so each request pulls less fresh context.",
    ),
    MetricDefinition(
        "context_length", "Context length per request", "Context", "request", "tokens", "derived",
        "Authoritative session input tokens divided by model requests: the context each request reads.",
        "If retrieval quality or latency degrades, summarize decisions and continue with a smaller working context.",
    ),
    MetricDefinition(
        "reasoning_share", "Session reasoning share", "Generation", "session", "ratio", "derived",
        "Authoritative reasoning tokens divided by reasoning plus output tokens.",
        "If cost or latency rises without a quality gain, test lower reasoning effort on well-defined work.",
    ),
    MetricDefinition(
        "output_tokens", "Output per request", "Generation", "request", "tokens", "derived",
        "Model output tokens divided by model requests; the dominant driver of response latency.",
        "If response latency matters, ask for bounded deliverables and defer optional explanation.",
    ),
    MetricDefinition(
        "model_requests", "Model requests per session", "Interaction", "session", "requests", "observed",
        "Authoritative model requests observed in one settled session.",
        "Read this with task complexity, latency, and cost; request count alone is not a quality signal.",
    ),
    MetricDefinition(
        "tool_calls", "Tool calls per request", "Interaction", "request", "calls", "derived",
        "Authoritative session tool calls divided by model requests.",
        "Inspect tool accuracy and redundant calls rather than trying to minimize the count itself.",
    ),
    MetricDefinition(
        "ai_cost_usd", "AI usage per request", "Cost", "request", "usd", "derived",
        "Authoritative session AI-credit dollar equivalent divided by model requests.",
        "Compare this with an outcome or value measure; cost alone does not establish efficiency.",
    ),
    MetricDefinition(
        "duration_seconds", "Session duration", "Flow", "session", "seconds", "observed",
        "Wall-clock duration of one authoritative Copilot session, which spans an editor window rather than a task.",
        "Use this to understand collection windows, not to judge task speed or developer productivity.",
    ),
    MetricDefinition(
        "activity_density", "Observed activity density", "Flow", "session", "ratio", "derived",
        "Overlap-safe active OTel span time divided by the authoritative session wall clock.",
        "Use this as observability context only; low density is not a productivity judgment.",
    ),
    MetricDefinition(
        "validation_coverage", "Coding sessions where validation was detected", "Workflow", "portfolio", "ratio", "derived",
        "Share of settled sessions with coding activity that also contain a validation-tool proxy.",
        "Treat a missing signal as a review cue: first check phaseToolPatterns, then run the relevant validation if absent.",
        "evidence",
    ),
    MetricDefinition(
        "session_usage_coverage", "Sessions with complete usage", "Evidence", "portfolio", "ratio", "derived",
        "Share of settled sessions carrying the complete authoritative usage contract required by Insights.",
        "Inspect worker artifacts and retained OTel if any settled session lacks complete usage.",
        "evidence",
    ),
)


# Product-owned triage defaults. They are displayed as heuristics, not external norms.
SIGNAL_POLICIES: dict[str, SignalPolicy] = {
    "cache_read_ratio": SignalPolicy("minimum", 0.50, 0.20),
    "uncached_input_per_request": SignalPolicy("maximum", 25_000, 100_000),
    "context_length": SignalPolicy("maximum", 32_000, 64_000),
    "reasoning_share": SignalPolicy("maximum", 0.50, 0.75),
    "output_tokens": SignalPolicy("maximum", 2_000, 8_000),
    "validation_coverage": SignalPolicy("minimum", 1.0, 0.80, 20),
    "session_usage_coverage": SignalPolicy("minimum", 1.0, 0.95, 1),
}


def _reference(
    kind: ReferenceKind,
    support: ReferenceSupport,
    label: str,
    url: str,
    note: str,
) -> dict[str, str]:
    return {"kind": kind, "support": support, "label": label, "url": url, "note": note}


METRIC_REFERENCES: dict[str, dict[str, str]] = {
    "cache_read_ratio": _reference(
        "best_practice", "direct", "OpenAI prompt caching", PROMPT_CACHE_URL,
        "Directly defines cached-token reuse and stable-prefix behavior used to calculate this metric.",
    ),
    "uncached_input_per_request": _reference(
        "best_practice", "direct", "Azure OpenAI monitoring reference", AZURE_MONITOR_URL,
        "Directly defines active tokens as total tokens minus cached tokens, matching this metric's numerator.",
    ),
    "context_length": _reference(
        "best_practice", "proxy", "RULER long-context benchmark", LONG_CONTEXT_URL,
        "Relevant to long-context retrieval risk, but its synthetic tasks and evaluated models differ from this workload.",
    ),
    "reasoning_share": _reference(
        "best_practice", "proxy", "Microsoft reasoning guidance", REASONING_URL,
        "Relevant to reasoning-token cost and effort tradeoffs; it does not define this derived share.",
    ),
    "output_tokens": _reference(
        "best_practice", "direct", "Azure OpenAI latency guidance", AZURE_LATENCY_URL,
        "Directly identifies generated tokens per request as the main response-latency driver.",
    ),
    "validation_coverage": _reference(
        "best_practice", "proxy", "DORA continuous integration", DORA_VALIDATION_URL,
        "Relevant to validating changes, but Algalon observes a configured tool-pattern proxy rather than test outcomes.",
    ),
    "session_usage_coverage": _reference(
        "local_measurement", "direct", "Authoritative session usage contract", "",
        "Checks required worker usage fields for every settled session before publishing Insights.",
    ),
    "model_requests": _reference(
        "best_practice", "direct",
        "Azure OpenAI monitoring reference", AZURE_MONITOR_URL,
        "Directly defines model request counts; it does not imply that fewer requests are better for agentic work.",
    ),
    "tool_calls": _reference(
        "best_practice", "proxy",
        "Microsoft Foundry agent evaluation", AGENT_EVAL_URL,
        "Recommends evaluating tool-call accuracy, redundancy, and navigation efficiency rather than count alone, "
        "which is the relevant limit on interpreting this count.",
    ),
    "ai_cost_usd": _reference(
        "best_practice", "none",
        "FinOps unit economics", FINOPS_URL,
        "Unit economics requires a value denominator. Algalon has no per-prompt outcome measure while prompt ROI is "
        "unavailable, so cost alone cannot establish efficiency.",
    ),
    "duration_seconds": _reference(
        "best_practice", "none",
        "SPACE measurement guidance", SPACE_URL,
        "States that activity metrics should never be used in isolation, and counts focused editor time as productive. "
        "An OTel session spans an editor window rather than a task.",
    ),
    "activity_density": _reference(
        "local_measurement", "direct", "Local OTel session evidence", "",
        "Calculated from overlap-safe active span time and authoritative session wall-clock duration.",
    ),
}


def _parse_iso(value: Any) -> datetime | None:
    match = ABSOLUTE_ISO_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if (
        match is None
        or int(match.group("hour")) > 23
        or int(match.group("minute")) > 59
        or int(match.group("second") or 0) > 59
    ):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _aggregate(samples: list[float]) -> tuple[float | None, int]:
    values = [value for value in samples if isfinite(value)]
    if not values:
        return None, 0
    latest = sorted(values[-min(MINIMUM_SAMPLES, len(values)):])
    middle = len(latest) // 2
    if len(latest) % 2:
        current = latest[middle]
    elif latest[middle - 1] >= 0:
        current = latest[middle - 1] + (latest[middle] - latest[middle - 1]) / 2
    elif latest[middle] <= 0:
        current = latest[middle] - (latest[middle] - latest[middle - 1]) / 2
    else:
        current = latest[middle - 1] / 2 + latest[middle] / 2
    return current, len(values)


def _trend(current: float | None, previous: float | None) -> str | None:
    if current is None or previous is None:
        return None
    change = current - previous
    if abs(change) <= abs(previous) * FLAT_TREND_BAND:
        return "flat"
    return "up" if change > 0 else "down"


def _signal(key: str, current: float | None, aggregate_size: int) -> dict[str, Any]:
    policy = SIGNAL_POLICIES.get(key)
    if policy is None:
        return {
            "level": "not_rated", "direction": None, "attentionBoundary": None,
            "dangerBoundary": None, "minimumSamples": 0,
        }
    if current is None:
        level = "unavailable"
    elif aggregate_size < policy.minimum_samples:
        level = "insufficient"
    elif policy.direction == "minimum":
        level = "danger" if current < policy.danger else "low" if current < policy.attention else "none"
    else:
        level = "danger" if current > policy.danger else "high" if current > policy.attention else "none"
    return {
        "level": level,
        "direction": policy.direction,
        "attentionBoundary": policy.attention,
        "dangerBoundary": policy.danger,
        "minimumSamples": policy.minimum_samples,
    }


def _metric_payload(
    definition: MetricDefinition,
    *,
    current: float | None,
    previous: float | None,
    normal_zone: dict[str, Any] | None,
    aggregate_size: int,
    minimum_baseline: int,
    zone: Zone,
    status: Status,
    action: str,
    reference: dict[str, str],
    severity: float | None = None,
    severity_label: str | None = None,
    trend: str | None = None,
) -> dict[str, Any]:
    return {
        "key": definition.key,
        "messageKey": f"insights.metrics.{definition.key}",
        "actionKey": f"insights.actions.{definition.key}",
        "label": definition.label,
        "category": definition.category,
        "group": definition.group,
        "scope": definition.scope,
        "unit": definition.unit,
        "evidence": definition.evidence,
        "description": definition.description,
        "current": current,
        "previous": previous,
        "trend": trend,
        "normalZone": normal_zone,
        "zone": zone,
        "status": status,
        "action": action,
        "severity": severity,
        "severityLabel": severity_label,
        "aggregateSize": aggregate_size,
        "minimumBaseline": minimum_baseline,
        "relatedMetrics": [],
        "signal": _signal(definition.key, current, aggregate_size),
        "reference": {
            **reference,
            "messageKey": f"insights.references.{definition.key}",
        },
    }


def descriptive_metric(
    definition: MetricDefinition,
    samples: list[float],
    previous_samples: list[float],
    reference: dict[str, str],
) -> dict[str, Any]:
    current, aggregate_size = _aggregate(samples)
    previous, _ = _aggregate(previous_samples)
    return _metric_payload(
        definition,
        current=current,
        previous=previous,
        normal_zone=None,
        aggregate_size=aggregate_size,
        minimum_baseline=0,
        zone="unavailable" if current is None else "descriptive",
        status="unavailable" if current is None else "descriptive",
        action=(
            "No eligible observations are available in the selected period."
            if current is None else definition.action
        ),
        reference=reference,
        trend=_trend(current, previous),
    )


def descriptive_coverage_metric(
    definition: MetricDefinition,
    coverage: tuple[int, int],
    previous_coverage: tuple[int, int],
    reference: dict[str, str],
) -> dict[str, Any]:
    numerator, denominator = coverage
    previous_numerator, previous_denominator = previous_coverage
    current = numerator / denominator if denominator else None
    previous = previous_numerator / previous_denominator if previous_denominator else None
    return _metric_payload(
        definition,
        current=current,
        previous=previous,
        normal_zone=None,
        aggregate_size=denominator,
        minimum_baseline=0,
        zone="unavailable" if current is None else "descriptive",
        status="unavailable" if current is None else "descriptive",
        action=(
            "No eligible observations are available in the selected period."
            if current is None else definition.action
        ),
        reference=reference,
        trend=_trend(current, previous),
    )


@dataclass(frozen=True)
class _Window:
    samples: dict[str, list[float]]
    validation: tuple[int, int]
    usage_coverage: tuple[int, int]
    direct_usage_sessions: int
    otel_usage_sessions: int


USAGE_FIELDS = (
    "source", "chatSpans", "inputTokens", "cacheReadTokens", "uncachedInputTokens",
    "outputTokens", "reasoningTokens", "aiCredits", "aiCostUsd", "models",
    "elapsedSeconds", "engagedSeconds", "activeSeconds", "activityDensity", "phases",
)
INTEGER_USAGE_FIELDS = (
    "chatSpans", "inputTokens", "cacheReadTokens", "uncachedInputTokens",
    "outputTokens", "reasoningTokens",
)
MODEL_COUNTER_FIELDS = (
    ("chatSpans", "requests"),
    ("inputTokens", "inputTokens"),
    ("cacheReadTokens", "cacheReadTokens"),
    ("uncachedInputTokens", "uncachedInputTokens"),
    ("outputTokens", "outputTokens"),
    ("reasoningTokens", "reasoningTokens"),
)
PHASE_NAMES = ("planning", "research", "coding", "validation", "unclassified")
PHASE_INTEGER_FIELDS = (
    "toolCalls", "modelSpans", "uncachedInputTokens", "outputTokens", "reasoningTokens",
)
PHASE_TIME_FIELDS = ("activeSeconds", "allocatedSeconds", "toolActiveSeconds")
MAX_MODEL_ROWS = 10_000


def _complete_usage(usage: Any, duration_seconds: Any) -> bool:
    if not isinstance(usage, dict) or any(field not in usage for field in USAGE_FIELDS):
        return False
    if usage.get("source") not in {"otel_traces", "copilot_turn_log"}:
        return False
    if any(
        not isinstance(usage[field], int) or isinstance(usage[field], bool) or usage[field] < 0
        for field in INTEGER_USAGE_FIELDS
    ):
        return False
    phases = usage.get("phases")
    if not isinstance(phases, dict) or set(phases) != set(PHASE_NAMES):
        return False
    phase_integer_values: list[int] = []
    phase_active_values: list[float] = []
    phase_allocated_values: list[float] = []
    phase_tool_active_values: list[float] = []
    for name in PHASE_NAMES:
        phase = phases[name]
        if not isinstance(phase, dict) or any(
            field not in phase for field in (*PHASE_INTEGER_FIELDS, *PHASE_TIME_FIELDS)
        ):
            return False
        if any(
            not isinstance(phase[field], int)
            or isinstance(phase[field], bool)
            or phase[field] < 0
            for field in PHASE_INTEGER_FIELDS
        ):
            return False
        if any(
            not isinstance(phase[field], (int, float))
            or isinstance(phase[field], bool)
            for field in PHASE_TIME_FIELDS
        ):
            return False
        try:
            phase_times = [float(phase[field]) for field in PHASE_TIME_FIELDS]
        except OverflowError:
            return False
        if any(not isfinite(value) or value < 0 for value in phase_times):
            return False
        if phase["modelSpans"] == 0 and any(
            phase[field] != 0
            for field in ("uncachedInputTokens", "outputTokens", "reasoningTokens")
        ):
            return False
        if phase["toolCalls"] == 0 and phase["toolActiveSeconds"] != 0:
            return False
        if phase["modelSpans"] == 0 and phase["toolCalls"] == 0 and (
            phase["activeSeconds"] != 0 or phase["allocatedSeconds"] != 0
        ):
            return False
        phase_integer_values.extend(phase[field] for field in PHASE_INTEGER_FIELDS)
        phase_active_values.append(phase_times[0])
        phase_allocated_values.append(phase_times[1])
        phase_tool_active_values.append(phase_times[2])
    models = usage.get("models")
    if not isinstance(models, list) or len(models) > MAX_MODEL_ROWS:
        return False
    model_integer_totals = {field: 0 for field in INTEGER_USAGE_FIELDS}
    model_ai_credits: list[float] = []
    model_ai_costs: list[float] = []
    model_names: set[str] = set()
    for model in models:
        if not isinstance(model, dict) or any(
            field not in model
            for field in (
                "model", *(model_field for _, model_field in MODEL_COUNTER_FIELDS),
                "aiCredits", "aiCostUsd",
            )
        ):
            return False
        model_name = model["model"]
        if not isinstance(model_name, str) or not model_name or model_name in model_names:
            return False
        model_names.add(model_name)
        if any(
            not isinstance(model[model_field], int)
            or isinstance(model[model_field], bool)
            or model[model_field] < 0
            for _, model_field in MODEL_COUNTER_FIELDS
        ):
            return False
        if model["requests"] == 0:
            return False
        if any(
            not isinstance(model[field], (int, float))
            or isinstance(model[field], bool)
            for field in ("aiCredits", "aiCostUsd")
        ):
            return False
        try:
            model_credits = float(model["aiCredits"])
            model_cost = float(model["aiCostUsd"])
        except OverflowError:
            return False
        if not isfinite(model_credits) or model_credits < 0 or not isfinite(model_cost) or model_cost < 0:
            return False
        if model_cost != model_credits / 100:
            return False
        if (
            model["cacheReadTokens"] > model["inputTokens"]
            or model["uncachedInputTokens"]
            != max(0, model["inputTokens"] - model["cacheReadTokens"])
        ):
            return False
        for session_field, model_field in MODEL_COUNTER_FIELDS:
            model_integer_totals[session_field] += model[model_field]
        model_ai_credits.append(model_credits)
        model_ai_costs.append(model_cost)
    if any(model_integer_totals[field] != usage[field] for field in INTEGER_USAGE_FIELDS):
        return False
    float_fields = (
        "aiCredits", "aiCostUsd", "elapsedSeconds", "engagedSeconds",
        "activeSeconds", "activityDensity",
    )
    if any(
        not isinstance(usage[field], (int, float)) or isinstance(usage[field], bool)
        for field in float_fields
    ):
        return False
    if (
        not isinstance(duration_seconds, (int, float))
        or isinstance(duration_seconds, bool)
    ):
        return False
    phase_counter_totals = {
        field: sum(phases[name][field] for name in PHASE_NAMES)
        for field in PHASE_INTEGER_FIELDS
    }
    if usage["source"] == "otel_traces" and any(
        phase_counter_totals[phase_field] > usage[usage_field]
        for phase_field, usage_field in (
            ("modelSpans", "chatSpans"),
            ("uncachedInputTokens", "uncachedInputTokens"),
            ("outputTokens", "outputTokens"),
            ("reasoningTokens", "reasoningTokens"),
        )
    ):
        return False
    tool_calls = phase_counter_totals["toolCalls"]
    try:
        converted_counters = [float(usage[field]) for field in INTEGER_USAGE_FIELDS]
        converted_phase_counters = [float(value) for value in phase_integer_values]
        converted_phase_totals = [float(value) for value in phase_counter_totals.values()]
        ai_credits = float(usage["aiCredits"])
        ai_cost = float(usage["aiCostUsd"])
        elapsed_seconds = float(usage["elapsedSeconds"])
        engaged_seconds = float(usage["engagedSeconds"])
        active_seconds = float(usage["activeSeconds"])
        activity_density = float(usage["activityDensity"])
        duration = float(duration_seconds)
        models_ai_credits = fsum(model_ai_credits)
        models_ai_cost = fsum(model_ai_costs)
        phases_active_seconds = fsum(phase_active_values)
        phases_allocated_seconds = fsum(phase_allocated_values)
    except OverflowError:
        return False
    if any(
        not isfinite(value) or value < 0
        for value in (
            *converted_counters, *converted_phase_counters, *converted_phase_totals,
            ai_credits, ai_cost,
            elapsed_seconds, engaged_seconds, active_seconds, activity_density, duration,
            models_ai_credits, models_ai_cost,
            phases_active_seconds, phases_allocated_seconds,
        )
    ):
        return False
    expected_density = active_seconds / elapsed_seconds if elapsed_seconds > 0 else 0
    requests = float(usage["chatSpans"])
    reasoning_total = float(usage["reasoningTokens"])
    output_total = float(usage["outputTokens"])
    generation_total = reasoning_total + output_total
    if not isfinite(generation_total):
        return False
    if usage["source"] == "copilot_turn_log" and (requests <= 0 or ai_credits <= 0):
        return False
    if requests == 0 and any(
        usage[field] != 0
        for field in (
            "inputTokens", "cacheReadTokens", "uncachedInputTokens",
            "outputTokens", "reasoningTokens",
        )
    ):
        return False
    if requests == 0 and (ai_credits != 0 or ai_cost != 0):
        return False
    if requests == 0 and any(
        phases[name][field] != 0
        for name in PHASE_NAMES
        for field in ("modelSpans", "uncachedInputTokens", "outputTokens", "reasoningTokens")
    ):
        return False
    return (
        usage["cacheReadTokens"] <= usage["inputTokens"]
        and usage["uncachedInputTokens"] == max(
            0, usage["inputTokens"] - usage["cacheReadTokens"]
        )
        and 0 <= activity_density <= 1
        and ai_cost == ai_credits / 100
        and elapsed_seconds > 0
        and isclose(elapsed_seconds, duration, rel_tol=1e-9, abs_tol=1e-9)
        and active_seconds <= engaged_seconds <= elapsed_seconds
        and all(value <= active_seconds for value in phase_active_values)
        and all(value <= engaged_seconds for value in phase_allocated_values)
        and all(value <= elapsed_seconds for value in phase_tool_active_values)
        and isclose(phases_active_seconds, active_seconds, rel_tol=1e-9, abs_tol=1e-9)
        and isclose(phases_allocated_seconds, engaged_seconds, rel_tol=1e-9, abs_tol=1e-9)
        and all(
            isclose(
                phase_allocated,
                engaged_seconds * phase_active / active_seconds if active_seconds > 0 else 0,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            for phase_active, phase_allocated in zip(
                phase_active_values, phase_allocated_values, strict=True,
            )
        )
        and isclose(activity_density, expected_density, rel_tol=1e-9, abs_tol=1e-9)
        and isclose(ai_credits, models_ai_credits, rel_tol=1e-9, abs_tol=1e-12)
        and isclose(ai_cost, models_ai_cost, rel_tol=1e-9, abs_tol=1e-12)
    )


def _collect(
    sessions: list[dict[str, Any]],
    settled_before: datetime,
    latest_valid_completion: datetime,
) -> _Window:
    samples: dict[str, list[float]] = {definition.key: [] for definition in INSIGHT_METRICS}
    coding_sessions = 0
    coding_sessions_with_validation = 0
    complete_sessions = 0
    eligible_sessions = 0
    direct_usage_sessions = 0
    otel_usage_sessions = 0
    parsed_sessions = [
        (session, _parse_iso(session.get("completedAt"))) for session in sessions
    ]
    for session, completed_at in sorted(
        parsed_sessions,
        key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc),
    ):
        if completed_at is None:
            eligible_sessions += 1
            continue
        if completed_at > latest_valid_completion:
            eligible_sessions += 1
            continue
        if completed_at > settled_before:
            continue
        eligible_sessions += 1
        usage = session.get("usage") or {}
        duration_seconds = session.get("durationSeconds")
        if not _complete_usage(usage, duration_seconds):
            continue
        complete_sessions += 1
        direct_usage_sessions += int(usage["source"] == "copilot_turn_log")
        otel_usage_sessions += int(usage["source"] == "otel_traces")
        requests = float(usage["chatSpans"])
        input_tokens = float(usage["inputTokens"])
        cache_tokens = float(usage["cacheReadTokens"])
        uncached_tokens = float(usage["uncachedInputTokens"])
        output_tokens = float(usage["outputTokens"])
        reasoning_tokens = float(usage["reasoningTokens"])
        phases = usage["phases"]
        tool_calls = float(sum(phases[name]["toolCalls"] for name in PHASE_NAMES))
        samples["model_requests"].append(requests)
        samples["duration_seconds"].append(float(duration_seconds))
        samples["activity_density"].append(float(usage["activityDensity"]))
        if input_tokens > 0:
            samples["cache_read_ratio"].append(cache_tokens / input_tokens)
        if reasoning_tokens + output_tokens > 0:
            samples["reasoning_share"].append(reasoning_tokens / (reasoning_tokens + output_tokens))
        if requests > 0:
            samples["uncached_input_per_request"].append(uncached_tokens / requests)
            samples["context_length"].append(input_tokens / requests)
            samples["output_tokens"].append(output_tokens / requests)
            samples["tool_calls"].append(tool_calls / requests)
            samples["ai_cost_usd"].append(float(usage["aiCostUsd"]) / requests)
        if float((phases.get("coding") or {}).get("toolCalls") or 0) > 0:
            coding_sessions += 1
            if float((phases.get("validation") or {}).get("toolCalls") or 0) > 0:
                coding_sessions_with_validation += 1
    return _Window(
        samples=samples,
        validation=(coding_sessions_with_validation, coding_sessions),
        usage_coverage=(complete_sessions, eligible_sessions),
        direct_usage_sessions=direct_usage_sessions,
        otel_usage_sessions=otel_usage_sessions,
    )


def build_insights(
    sessions: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
    days: int,
    previous_sessions: list[dict[str, Any]] | None = None,
    previous_prompts: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc)
    settled_before = observed_at - timedelta(seconds=SESSION_SETTLE_SECONDS)
    latest_valid_completion = observed_at + timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_SECONDS)
    window = _collect(sessions, settled_before, latest_valid_completion)
    baseline = _collect(previous_sessions or [], settled_before, latest_valid_completion)

    metrics: list[dict[str, Any]] = []
    for definition in INSIGHT_METRICS:
        if definition.key == "validation_coverage":
            metrics.append(descriptive_coverage_metric(
                definition, window.validation, baseline.validation, METRIC_REFERENCES[definition.key],
            ))
        elif definition.key == "session_usage_coverage":
            metrics.append(descriptive_coverage_metric(
                definition, window.usage_coverage, baseline.usage_coverage, METRIC_REFERENCES[definition.key],
            ))
        else:
            metrics.append(descriptive_metric(
                definition,
                window.samples[definition.key],
                baseline.samples[definition.key],
                METRIC_REFERENCES[definition.key],
            ))

    coverage = next(metric for metric in metrics if metric["key"] == "session_usage_coverage")
    complete_sessions, eligible_sessions = window.usage_coverage
    integrity_passed = eligible_sessions > 0 and complete_sessions == eligible_sessions
    integrity_failed = eligible_sessions > 0 and not integrity_passed
    previous_complete, previous_eligible = baseline.usage_coverage
    previous_integrity_passed = previous_eligible == 0 or previous_complete == previous_eligible
    for metric in metrics:
        if metric["key"] == "session_usage_coverage":
            continue
        if integrity_failed:
            metric.update({
                "current": None,
                "zone": "unavailable",
                "status": "unavailable",
                "action": "Withheld: every settled session must carry complete authoritative usage.",
                "severity": None,
                "severityLabel": None,
                "signal": {**metric["signal"], "level": "unavailable"},
            })
        if integrity_failed or not previous_integrity_passed:
            metric["previous"] = None
            metric["trend"] = None
    return {
        "days": days,
        "zoneMethod": {
            "typical": "Legacy status is unused; operational cues are reported through signal.",
            "elevated": "Legacy status is unused; operational cues are reported through signal.",
            "high": "Legacy status is unused; operational cues are reported through signal.",
            "descriptive": "Metric values remain descriptive; signals are triage cues rather than grades.",
            "insufficient": "Signals requiring stable coverage show not enough data below their sample minimum.",
            "low": "Signal boundaries are explicit Algalon operational defaults, not universal norms.",
            "baselineRule": (
                f"Current values are medians of up to {MINIMUM_SAMPLES} eligible observations, taking the latest "
                "within the selected period, and each metric is compared with the immediately preceding period of "
                "equal length."
            ),
            "coverageRule": (
                "Coverage metrics use every settled session in the selected period. A session is "
                f"an editor-window lifetime rather than a task, so it becomes eligible only "
                f"{SESSION_SETTLE_SECONDS // 60} minutes after its last observed span and carries no behavioural "
                "threshold."
            ),
        },
        "evidenceHealth": {
            "sessionUsageCoverage": coverage["current"],
            "completeSessions": complete_sessions,
            "eligibleSessions": eligible_sessions,
            "directUsageSessions": window.direct_usage_sessions,
            "otelUsageSessions": window.otel_usage_sessions,
            "requiredCoverage": REQUIRED_SESSION_USAGE_COVERAGE,
            "integrityPassed": integrity_passed,
            "degraded": integrity_failed,
            "message": (
                f"Integrity gate failed: {complete_sessions} of {eligible_sessions} settled sessions carry the "
                "complete authoritative usage contract. Insights are withheld until session usage coverage returns "
                "to 100%."
                if integrity_failed else None
            ),
        },
        "summary": {
            "metrics": len(metrics),
            "guardrails": 0,
            "actions": sum(1 for metric in metrics if metric["status"] == "action"),
            "watch": sum(1 for metric in metrics if metric["status"] == "watch"),
            "insufficient": sum(1 for metric in metrics if metric["status"] == "insufficient"),
        },
        "priorities": [],
        "metrics": metrics,
    }
