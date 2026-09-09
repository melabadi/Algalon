from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.prompt_index import group_prompts
from backend.app.store import (
    CURRENT_FORMULA_VERSION,
    INSIGHTS_SESSION_QUERY,
    MAX_JSON_NODES,
    SCENARIOS,
    ValueStore,
    _assisted_seconds,
    _engaged_seconds,
    _extract_prompt_content,
    _integer,
    _is_json_safe,
    _public_session_id,
    _sanitized_calibration_sources,
    _union_duration_seconds,
    _valid_modeled_timing,
    _validated_current_benchmark,
)


def attribute(key: str, value: str, value_type: str = "stringValue") -> dict:
    return {"key": key, "value": {value_type: str(value)}}


def complete_scenario(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "estimatedManualMinutes": 0,
        "estimatedMinutesSaved": 0,
        "estimatedManualLaborCostUsd": 0,
        "estimatedAiAssistedLaborCostUsd": 0,
        "estimatedAiAssistedTotalCostUsd": 0,
        "estimatedGrossCostSavingsUsd": 0,
        "modeledDeliveryCostReduction": None,
        "estimatedBenefitUsd": 0,
        "netValueUsd": 0,
    }
    result.update(overrides)
    manual_minutes = float(result["estimatedManualMinutes"])
    saved_minutes = float(result["estimatedMinutesSaved"])
    measured_minutes = manual_minutes - saved_minutes
    assisted_labor_cost = float(result["estimatedAiAssistedLaborCostUsd"])
    assisted_total_cost = float(result["estimatedAiAssistedTotalCostUsd"])
    ai_cost = assisted_total_cost - assisted_labor_cost
    manual_cost = float(result["estimatedManualLaborCostUsd"])
    labor_cost_per_minute = (
        assisted_labor_cost / measured_minutes
        if measured_minutes > 0 and assisted_labor_cost > 0
        else manual_cost / manual_minutes if manual_minutes > 0 else 1
    )
    benefit = float(result["estimatedBenefitUsd"])
    capacity = (
        benefit / saved_minutes / labor_cost_per_minute
        if saved_minutes != 0 and labor_cost_per_minute > 0 else 0.5
    )
    if not 0 < capacity <= 1:
        capacity = 0.5
    break_even = (
        measured_minutes + ai_cost / labor_cost_per_minute / capacity
        if labor_cost_per_minute > 0 else measured_minutes
    )
    if "roi" not in overrides:
        result["roi"] = float(result["netValueUsd"]) / ai_cost if ai_cost > 0 else None
    if "taskTimeReduction" not in overrides:
        result["taskTimeReduction"] = (
            saved_minutes / manual_minutes
            if manual_minutes else 0
        )
    if "modeledDeliveryCostReduction" not in overrides:
        manual_cost = float(result["estimatedManualLaborCostUsd"])
        result["modeledDeliveryCostReduction"] = (
            float(result["estimatedGrossCostSavingsUsd"]) / manual_cost
            if manual_cost else None
        )
    if "breakEvenManualMinutes" not in overrides:
        result["breakEvenManualMinutes"] = break_even
    if "capacityBand" not in overrides:
        result["capacityBand"] = [{
            "capacityRealization": capacity,
            "estimatedBenefitUsd": benefit,
            "netValueUsd": result["netValueUsd"],
            "roi": result["roi"],
            "breakEvenManualMinutes": break_even,
        }]
    if "phases" not in overrides:
        result["phases"] = {
            name: {
                "measuredAiMinutes": measured_minutes if name == "unclassified" else 0,
                "measuredAiSeconds": measured_minutes * 60 if name == "unclassified" else 0,
                "estimatedManualMinutes": manual_minutes if name == "unclassified" else 0,
                "estimatedMinutesSaved": saved_minutes if name == "unclassified" else 0,
                "timeReduction": result["taskTimeReduction"] if name == "unclassified" else 0,
            }
            for name in ("planning", "research", "coding", "validation", "unclassified")
        }
    return result


def complete_benchmark(
    scenarios: dict[str, dict[str, object]],
    *,
    retained_source_characters: int = 0,
    ai_cost_usd: float | None = None,
) -> dict[str, object]:
    first = scenarios["base"]
    scenario_phases = first["phases"]
    measurements = {
        name: {
            "measuredAiSeconds": scenario_phases[name]["measuredAiSeconds"],
            "measuredAiMinutes": scenario_phases[name]["measuredAiMinutes"],
        }
        for name in ("planning", "research", "coding", "validation", "unclassified")
    }
    measured_minutes = sum(
        float(measurement["measuredAiMinutes"]) for measurement in measurements.values()
    )
    ai_cost = ai_cost_usd if ai_cost_usd is not None else (
        float(first["estimatedAiAssistedTotalCostUsd"])
        - float(first["estimatedAiAssistedLaborCostUsd"])
    )
    return {
        "formulaVersion": CURRENT_FORMULA_VERSION,
        "measuredAiMinutes": measured_minutes,
        "aiCostUsd": ai_cost,
        "qualityFactor": 1,
        "retainedSourceCharacters": retained_source_characters,
        "typingEquivalentMinutes": 1 if retained_source_characters > 0 else 0,
        "phases": measurements,
        "scenarios": scenarios,
    }


def phase_evidence(allocated_seconds: float) -> dict[str, dict[str, float | int]]:
    return {
        name: {
            "activeSeconds": allocated_seconds if name == "unclassified" else 0,
            "allocatedSeconds": allocated_seconds if name == "unclassified" else 0,
            "toolActiveSeconds": 0,
            "toolCalls": 0,
            "modelSpans": 1 if name == "unclassified" and allocated_seconds > 0 else 0,
            "uncachedInputTokens": 0,
            "outputTokens": 0,
            "reasoningTokens": 0,
        }
        for name in ("planning", "research", "coding", "validation", "unclassified")
    }


def complete_usage(
    duration_seconds: float,
    engaged_seconds: float,
    ai_cost_usd: float,
    *,
    allocated_seconds: float | None = None,
) -> dict[str, object]:
    ai_credits = ai_cost_usd * 100
    return {
        "source": "otel_traces",
        "chatSpans": 1,
        "inputTokens": 1,
        "cacheReadTokens": 0,
        "uncachedInputTokens": 1,
        "outputTokens": 1,
        "reasoningTokens": 0,
        "aiCredits": ai_credits,
        "aiCostUsd": ai_cost_usd,
        "models": [{
            "model": "model",
            "requests": 1,
            "inputTokens": 1,
            "cacheReadTokens": 0,
            "uncachedInputTokens": 1,
            "outputTokens": 1,
            "reasoningTokens": 0,
            "aiCredits": ai_credits,
            "aiCostUsd": ai_cost_usd,
        }],
        "elapsedSeconds": duration_seconds,
        "engagedSeconds": engaged_seconds,
        "activeSeconds": engaged_seconds,
        "activityDensity": engaged_seconds / duration_seconds,
        "phases": phase_evidence(
            engaged_seconds if allocated_seconds is None else allocated_seconds
        ),
    }


def model_config(
    loaded_hourly_rate: float,
    scenario_values: dict[str, dict[str, object]],
    *,
    capacity_realization: float = 0.5,
) -> dict[str, object]:
    return {
        "loadedHourlyRateUsd": loaded_hourly_rate,
        "benchmark": {
            "acknowledgedAssumptions": True,
            "manualTimeModelSource": "test",
            "capacityRealization": capacity_realization,
            "capacityRealizationBand": [capacity_realization],
            "typingWordsPerMinute": 120,
            "charactersPerWord": 1,
            "scenarios": scenario_values,
        },
    }


def scenario_config(
    *,
    coding_fraction: float = 0,
    coding_words_per_minute: float = 60,
    unclassified_multiplier: float = 1,
) -> dict[str, object]:
    token = {
        "relevantTokenFraction": 0,
        "tokensPerMinute": 1,
        "interactionMinutesPerTool": 0,
        "reasoningTokenWeight": 0,
    }
    return {
        "planning": dict(token),
        "research": dict(token),
        "coding": {
            "manualEntryFraction": coding_fraction,
            "wordsPerMinute": coding_words_per_minute,
        },
        "validation": dict(token),
        "unclassifiedManualMultiplier": unclassified_multiplier,
    }


class ValueStoreTests(unittest.TestCase):
    def test_otlp_inbox_deduplicates_spans_and_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "value-model.local.json"
            config.write_text("{}\n", encoding="utf-8")
            store = ValueStore(
                root / "value.db", root / "sessions", root / "traces.json", config
            )
            span = {
                "traceId": "trace-a",
                "spanId": "span-a",
                "name": "chat model-a",
                "startTimeUnixNano": "1785924000000000000",
                "endTimeUnixNano": "1785924001000000000",
                "attributes": [],
            }
            payload = {
                "resourceSpans": [{
                    "resource": {"attributes": [
                        attribute("service.name", "copilot-chat"),
                        attribute("session.id", "session-a"),
                    ]},
                    "scopeSpans": [{"spans": [span]}],
                }],
            }

            first = store.ingest_otlp_traces(payload)
            replay_payload = json.loads(json.dumps(payload))
            replay_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] = (
                "chat renamed-during-replay"
            )
            replay = store.ingest_otlp_traces(replay_payload)
            page = store.otel_records()

            self.assertEqual(first, {"received": 1, "accepted": 1})
            self.assertEqual(replay, {"received": 1, "accepted": 0})
            self.assertEqual(len(page["records"]), 1)
            self.assertGreater(page["nextCursor"], 0)
            self.assertFalse(page["hasMore"])
            self.assertEqual(store.otel_records(page["nextCursor"])["records"], [])
            trace_sessions, signature = store._stable_trace_sessions()
            self.assertEqual(signature, ("inbox", page["nextCursor"]))
            self.assertEqual(len(trace_sessions["session-a"]["spans"]), 1)

    def test_legacy_trace_archives_import_into_inbox_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "value-model.local.json"
            config.write_text("{}\n", encoding="utf-8")
            trace_archive = root / "traces.json"
            span = {
                "traceId": "trace-a",
                "spanId": "span-a",
                "name": "chat model-a",
                "startTimeUnixNano": "1785924000000000000",
                "endTimeUnixNano": "1785924001000000000",
                "attributes": [],
            }
            payload = {
                "resourceSpans": [{
                    "resource": {"attributes": [
                        attribute("service.name", "copilot-chat"),
                        attribute("session.id", "session-a"),
                    ]},
                    "scopeSpans": [{"spans": [span]}],
                }],
            }
            rotated = root / "traces-rotated.json"
            rotated.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            trace_archive.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            store = ValueStore(
                root / "value.db", root / "sessions", trace_archive, config
            )

            store.index_once()
            first_page = store.otel_records()
            trace_archive.write_text(
                trace_archive.read_text(encoding="utf-8")
                + json.dumps({
                    "resourceSpans": [{
                        "resource": {"attributes": [
                            attribute("service.name", "copilot-chat"),
                            attribute("session.id", "session-b"),
                        ]},
                        "scopeSpans": [{"spans": [{
                            **span, "traceId": "trace-b", "spanId": "span-b",
                        }]}],
                    }],
                }) + "\n",
                encoding="utf-8",
            )
            store.index_once()

            self.assertEqual(len(first_page["records"]), 1)
            self.assertEqual(len(store.otel_records()["records"]), 1)
            with closing(store._connect()) as connection:
                self.assertIsNotNone(connection.execute(
                    "SELECT value FROM store_metadata WHERE key = ?",
                    ("legacy_trace_inbox_import_v1",),
                ).fetchone())

    def test_explicit_zero_engagement_does_not_fall_back_to_duration(self) -> None:
        self.assertEqual(_engaged_seconds({
            "durationSeconds": 60,
            "usage": {"engagedSeconds": 0},
        }), 0)

    def test_calibration_sources_are_projected_and_enum_validated(self) -> None:
        source = {
            "title": "Study",
            "publisher": "Publisher",
            "publishedAt": "2026-01-01",
            "url": "https://example.com",
            "evidenceClass": "controlled_experiment",
            "appliesTo": ["scenario envelope"],
            "supportLevels": {"scenario envelope": "context"},
            "finding": "Finding",
            "limitation": "Limit",
            "private": "must not escape",
        }
        projected = _sanitized_calibration_sources([source])
        self.assertNotIn("private", projected[0])
        self.assertIsNone(_sanitized_calibration_sources([
            {**source, "evidenceClass": "invented"},
        ]))
        self.assertIsNone(_sanitized_calibration_sources([
            {**source, "supportLevels": {"scenario envelope": "invented"}},
        ]))

    def test_invalid_model_config_withholds_session_and_export_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text(json.dumps({
                "loadedHourlyRateUsd": "92",
                "benchmark": {"capacityRealization": "0.5"},
            }), encoding="utf-8")
            started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            scenario = complete_scenario(
                estimatedManualMinutes=10,
                estimatedMinutesSaved=5,
                estimatedManualLaborCostUsd=10,
                estimatedBenefitUsd=5,
                netValueUsd=4,
                roi=4,
            )
            artifact = {
                "experiment": "invalid-model-config",
                "startedAt": started_at.isoformat(),
                "completedAt": (started_at + timedelta(minutes=1)).isoformat(),
                "status": "published",
                "usage": {"engagedSeconds": 60},
                "source": {},
                "benchmark": {
                    "formulaVersion": CURRENT_FORMULA_VERSION,
                    "scenarios": {name: dict(scenario) for name in SCENARIOS},
                },
            }
            (sessions / "session-invalid-model-config.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            session = store.session("invalid-model-config")
            exported, _ = store.export_data()

            for payload in (session, exported[0]):
                self.assertEqual(payload["modelingStatus"], "invalid")
                self.assertIsNone(payload["scenarioResult"])
                self.assertIsNone(payload["benchmark"])

    def test_invalid_session_bounds_atomically_invalidate_portfolio_modeling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            active_scenarios = {
                name: scenario_config(unclassified_multiplier=6) for name in SCENARIOS
            }
            config_path.write_text(
                json.dumps(model_config(120, active_scenarios)), encoding="utf-8"
            )
            now = datetime.now(timezone.utc) - timedelta(hours=2)
            scenario = complete_scenario(
                estimatedManualMinutes=6,
                estimatedMinutesSaved=5,
                estimatedManualLaborCostUsd=12,
                estimatedAiAssistedLaborCostUsd=2,
                estimatedAiAssistedTotalCostUsd=3,
                estimatedGrossCostSavingsUsd=9,
                estimatedBenefitUsd=5,
                netValueUsd=4,
                roi=4,
            )
            cases = (
                (now.isoformat(), (now + timedelta(minutes=1)).isoformat(), 60),
                (now.replace(tzinfo=None).isoformat(), (now + timedelta(minutes=2)).isoformat(), 60),
                (
                    (now + timedelta(minutes=4)).isoformat(),
                    (now + timedelta(minutes=5)).isoformat(),
                    59,
                ),
            )
            for index, (started_at, completed_at, allocated_seconds) in enumerate(cases):
                artifact = {
                    "experiment": f"timing-{index}",
                    "startedAt": started_at,
                    "completedAt": completed_at,
                    "status": "published",
                    "usage": complete_usage(
                        60, 60, 1, allocated_seconds=allocated_seconds,
                    ),
                    "source": {},
                    "benchmark": complete_benchmark({
                        name: dict(scenario) for name in SCENARIOS
                    }),
                }
                (sessions / f"session-timing-{index}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            overview = store.overview("base", 30)
            exported, _ = store.export_data()
            exported_by_id = {payload["experiment"]: payload for payload in exported}

            self.assertEqual(store.session("timing-0")["modelingStatus"], "available")
            self.assertEqual(store.session("timing-1")["modelingStatus"], "invalid")
            self.assertEqual(store.session("timing-2")["modelingStatus"], "invalid")
            self.assertIsNone(exported_by_id["timing-2"]["benchmark"])
            self.assertIsNone(exported_by_id["timing-2"]["scenarioResult"])
            self.assertEqual(overview["modelingStatus"], "invalid")
            self.assertEqual(overview["totals"]["estimatedManualMinutes"], 0)
            self.assertIsNone(overview["totals"]["roi"])

    def test_modeled_timing_preserves_subsecond_precision_at_current_epoch(self) -> None:
        started_at = datetime(2026, 8, 25, 20, 13, 18, 761000, tzinfo=timezone.utc)
        self.assertTrue(_valid_modeled_timing({
            "startedAt": started_at.isoformat(),
            "completedAt": (started_at + timedelta(milliseconds=400)).isoformat(),
            "durationSeconds": 0.4,
            "usage": complete_usage(0.4, 0.4, 0.01),
        }))

    def test_prompt_index_preserves_exact_integers_and_bounds_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            started = datetime.now(timezone.utc) - timedelta(hours=2)
            started_ms = int(started.timestamp() * 1_000)
            raw_session_id = "prompt-integer-boundary"
            experiment = _public_session_id(raw_session_id, started_ms)
            artifact = {
                "experiment": experiment,
                "startedAt": started.isoformat(),
                "completedAt": (started + timedelta(minutes=1)).isoformat(),
                "status": "published",
                "usage": {"source": "otel_traces"},
                "source": {},
                "benchmark": None,
            }
            (sessions / f"session-{experiment}.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store._index_sessions()
            group = {
                "ordinal": 1,
                "started_at": started.isoformat(),
                "content": "",
                "captured_content_length": 2**63 - 1,
                "model_requests": 2**53 + 1,
                "tool_calls": 0,
                "input_tokens": 2**63 - 1,
                "cache_read_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "ai_cost_usd": 0.01,
                "models": {"model-a": 2**53 + 1},
                "ai_credits": 1,
                "usage_source": "otel_trace",
            }
            trace_sessions = {
                raw_session_id: {"start": started_ms, "end": started_ms + 60_000, "spans": []}
            }
            with (
                patch.object(store, "_stable_trace_sessions", return_value=(trace_sessions, ())),
                patch("backend.app.store.group_prompts", return_value=[group]),
            ):
                store._index_prompts()

            prompt = store.prompts(experiment)[0]
            self.assertIsNone(prompt["modelRequests"])
            self.assertEqual(prompt["modelRequestsExact"], str(2**53 + 1))
            self.assertIsNone(prompt["inputTokens"])
            self.assertEqual(prompt["inputTokensExact"], str(2**63 - 1))
            self.assertEqual(prompt["toolCalls"], 0)
            self.assertEqual(prompt["toolCallsExact"], "0")
            self.assertEqual(prompt["outputTokens"], 0)
            self.assertEqual(prompt["reasoningTokens"], 0)
            self.assertIsNone(prompt["capturedContentLength"])
            self.assertEqual(prompt["capturedContentLengthExact"], str(2**63 - 1))
            self.assertIsNone(prompt["models"]["model-a"])
            self.assertEqual(prompt["modelsExact"]["model-a"], str(2**53 + 1))

            original_prompts = store.prompts(experiment)
            with (
                patch.object(store, "_stable_trace_sessions", return_value=(trace_sessions, ())),
                patch(
                    "backend.app.store.group_prompts",
                    return_value=[{**group, "input_tokens": None}],
                ),
            ):
                store._index_prompts()

            self.assertEqual(store.prompts(experiment), original_prompts)

    def test_non_object_config_fails_closed_across_store_reads(self) -> None:
        configs = (
            [],
            {
                "loadedHourlyRateUsd": "92",
                "benchmark": "invalid",
                "promptStorage": ["invalid"],
            },
            {
                "benchmark": ["invalid"],
                "promptStorage": {"enabled": "false", "retentionDays": "30"},
            },
            {
                "loadedHourlyRateUsd": True,
                "benchmark": {
                    "scenarios": "invalid",
                    "phaseToolPatterns": ["invalid"],
                    "calibrationSources": [{}],
                },
                "promptStorage": {"enabled": [], "retentionDays": "10000"},
            },
        )
        for index, config in enumerate(configs):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                sessions = root / "sessions"
                sessions.mkdir()
                config_path = root / "value-model.local.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                started_at = datetime.now(timezone.utc) - timedelta(hours=2)
                scenario = complete_scenario(
                    estimatedManualMinutes=10,
                    estimatedMinutesSaved=5,
                    estimatedManualLaborCostUsd=10,
                    estimatedAiAssistedLaborCostUsd=2,
                    estimatedAiAssistedTotalCostUsd=3,
                    estimatedGrossCostSavingsUsd=7,
                    estimatedBenefitUsd=5,
                    netValueUsd=4,
                    roi=4,
                )
                artifact = {
                    "experiment": "config-invalid-model",
                    "startedAt": started_at.isoformat(),
                    "completedAt": (started_at + timedelta(minutes=1)).isoformat(),
                    "status": "published",
                    "usage": {"aiCredits": 100, "aiCostUsd": 1, "engagedSeconds": 60},
                    "source": {},
                    "benchmark": {
                        "formulaVersion": CURRENT_FORMULA_VERSION,
                        "scenarios": {name: dict(scenario) for name in SCENARIOS},
                    },
                }
                (sessions / "session-config-invalid-model.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
                store = ValueStore(root / f"value-{index}.db", sessions, root / "traces.json", config_path)

                store.index_once()

                methodology = store.methodology()
                self.assertIsNone(methodology["config"]["loadedHourlyRateUsd"])
                self.assertIsNone(methodology["config"]["benchmark"])
                self.assertEqual(store._prompt_storage_config(), (True, 30))
                overview = store.overview("base", 30)
                self.assertEqual(overview["modelingStatus"], "invalid")
                self.assertEqual(overview["totals"]["estimatedManualMinutes"], 0)

    def test_insights_query_excludes_valid_rows_before_comparison_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            now = datetime.now(timezone.utc)
            old_date = now - timedelta(days=100)
            artifacts = {
                "old-valid": old_date.isoformat(),
                "old-naive": old_date.replace(tzinfo=None).isoformat(),
                "old-lowercase-zone": old_date.isoformat().replace("+00:00", "z"),
                "old-invalid-calendar": "2025-02-29T12:00:00Z",
            }
            for experiment, completed_at in artifacts.items():
                artifact = {
                    "experiment": experiment,
                    "startedAt": completed_at,
                    "completedAt": completed_at,
                    "status": "published",
                    "usage": {},
                    "source": {},
                }
                (sessions / f"session-{experiment}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
            store.index_once()

            with patch.object(store, "_session_payload", wraps=store._session_payload) as payload:
                insights = store.insights(30)

            with closing(store._connect()) as connection:
                plan = connection.execute(
                    f"EXPLAIN QUERY PLAN {INSIGHTS_SESSION_QUERY}",
                    ((now - timedelta(days=60)).timestamp(),),
                ).fetchall()

            loaded_experiments = {call.args[0]["experiment"] for call in payload.call_args_list}
            index_searches = [
                row["detail"]
                for row in plan
                if row["detail"].startswith("SEARCH ")
                and "idx_session_time_epoch" in row["detail"]
            ]
            self.assertEqual(len(index_searches), 2)
            self.assertEqual(
                loaded_experiments,
                {"old-naive", "old-lowercase-zone", "old-invalid-calendar"},
            )
            self.assertEqual(insights["evidenceHealth"]["eligibleSessions"], 3)
            self.assertEqual(insights["evidenceHealth"]["completeSessions"], 0)

    def test_integer_projection_preserves_exact_signed_64_bit_values(self) -> None:
        for value in (-(2**63), 2**53 + 1, 2**63 - 1):
            self.assertEqual(_integer(value), value)
        for value in (-(2**63) - 1, 2**63, True, 1.0, "1"):
            self.assertEqual(_integer(value), 0)

    def test_json_tree_rejects_root_width_before_queueing_children(self) -> None:
        self.assertFalse(_is_json_safe([0] * MAX_JSON_NODES))

    def test_current_benchmark_validation_precedes_exposure_and_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            active_scenarios = {
                name: scenario_config(unclassified_multiplier=6) for name in SCENARIOS
            }
            config_path.write_text(
                json.dumps(model_config(120, active_scenarios)), encoding="utf-8"
            )
            started_at = datetime.now(timezone.utc) - timedelta(hours=2)

            def modeled_scenario(
                *, measured_minutes: float = 1, labor_rate: float = 2,
                capacity: float = 0.5, ai_cost: float = 1,
            ) -> dict[str, object]:
                manual_minutes = 6
                saved_minutes = manual_minutes - measured_minutes
                manual_cost = manual_minutes * labor_rate
                assisted_labor_cost = measured_minutes * labor_rate
                assisted_total_cost = assisted_labor_cost + ai_cost
                gross_savings = manual_cost - assisted_total_cost
                benefit = saved_minutes * labor_rate * capacity
                net_value = benefit - ai_cost
                return complete_scenario(
                    estimatedManualMinutes=manual_minutes,
                    estimatedMinutesSaved=saved_minutes,
                    estimatedManualLaborCostUsd=manual_cost,
                    estimatedAiAssistedLaborCostUsd=assisted_labor_cost,
                    estimatedAiAssistedTotalCostUsd=assisted_total_cost,
                    estimatedGrossCostSavingsUsd=gross_savings,
                    estimatedBenefitUsd=benefit,
                    netValueUsd=net_value,
                    roi=net_value / ai_cost if ai_cost > 0 else None,
                )

            valid_scenario = modeled_scenario()
            valid_benchmark = complete_benchmark({
                name: complete_scenario(**valid_scenario) for name in SCENARIOS
            })

            def benchmark_copy() -> dict[str, object]:
                return json.loads(json.dumps(valid_benchmark))

            nested: dict[str, object] = {"value": 1}
            for _ in range(500):
                nested = {"nested": nested}
            nan_benchmark = benchmark_copy()
            nan_benchmark["scenarios"]["base"]["netValueUsd"] = float("nan")
            partial_benchmark = benchmark_copy()
            partial_benchmark["scenarios"]["base"] = {"estimatedManualMinutes": "10"}
            deep_benchmark = benchmark_copy()
            deep_benchmark["nested"] = nested
            missing_roi_benchmark = benchmark_copy()
            missing_roi_benchmark["scenarios"]["base"].pop("roi")
            empty_capacity_band_benchmark = benchmark_copy()
            empty_capacity_band_benchmark["scenarios"]["base"]["capacityBand"] = []
            negative_cost_benchmark = benchmark_copy()
            negative_cost_benchmark["scenarios"]["base"]["estimatedManualLaborCostUsd"] = -1
            phase_mismatch_benchmark = benchmark_copy()
            phase_mismatch_benchmark["scenarios"]["base"]["phases"]["unclassified"][
                "estimatedMinutesSaved"
            ] += 1
            capacity_mismatch_benchmark = benchmark_copy()
            capacity_mismatch_benchmark["scenarios"]["base"]["capacityBand"][0][
                "netValueUsd"
            ] += 1
            cost_evidence_mismatch = complete_benchmark({
                name: modeled_scenario(ai_cost=2) for name in SCENARIOS
            })
            phase_evidence_mismatch = complete_benchmark({
                name: modeled_scenario(measured_minutes=59 / 60) for name in SCENARIOS
            })
            source_evidence_mismatch = benchmark_copy()
            source_evidence_mismatch["retainedSourceCharacters"] = 1
            source_evidence_mismatch["typingEquivalentMinutes"] = 1
            configured_rate_mismatch = complete_benchmark({
                name: modeled_scenario(labor_rate=1) for name in SCENARIOS
            })
            configured_capacity_mismatch = complete_benchmark({
                name: modeled_scenario(capacity=0.25) for name in SCENARIOS
            })
            zero_cost_benchmark = complete_benchmark({
                name: modeled_scenario(ai_cost=0) for name in SCENARIOS
            })
            invented_coding_scenario = modeled_scenario()
            invented_coding_scenario["phases"]["coding"].update({
                "estimatedManualMinutes": 1_000,
                "estimatedMinutesSaved": 1_000,
                "timeReduction": 1,
            })
            invented_coding_scenario["phases"]["unclassified"].update({
                "estimatedManualMinutes": 1,
                "estimatedMinutesSaved": 0,
                "timeReduction": 0,
            })
            invented_coding_scenario.update({
                "estimatedManualMinutes": 1_001,
                "estimatedMinutesSaved": 1_000,
                "estimatedManualLaborCostUsd": 2_002,
                "estimatedGrossCostSavingsUsd": 1_999,
                "modeledDeliveryCostReduction": 1_999 / 2_002,
                "estimatedBenefitUsd": 1_000,
                "netValueUsd": 999,
                "roi": 999,
                "taskTimeReduction": 1_000 / 1_001,
            })
            invented_coding_scenario["capacityBand"] = [{
                "capacityRealization": 0.5,
                "estimatedBenefitUsd": 1_000,
                "netValueUsd": 999,
                "roi": 999,
                "breakEvenManualMinutes": 2,
            }]
            invented_coding_benchmark = complete_benchmark({
                name: json.loads(json.dumps(invented_coding_scenario)) for name in SCENARIOS
            })
            oversized_formula_version = benchmark_copy()
            oversized_formula_version["formulaVersion"] = int("9" * 1_000)
            benchmarks = {
                "valid-benchmark": valid_benchmark,
                "nan-benchmark": nan_benchmark,
                "partial-benchmark": partial_benchmark,
                "deep-benchmark": deep_benchmark,
                "missing-roi-benchmark": missing_roi_benchmark,
                "empty-capacity-band-benchmark": empty_capacity_band_benchmark,
                "negative-cost-benchmark": negative_cost_benchmark,
                "phase-mismatch-benchmark": phase_mismatch_benchmark,
                "capacity-mismatch-benchmark": capacity_mismatch_benchmark,
                "cost-evidence-mismatch": cost_evidence_mismatch,
                "phase-evidence-mismatch": phase_evidence_mismatch,
                "source-evidence-mismatch": source_evidence_mismatch,
                "configured-rate-mismatch": configured_rate_mismatch,
                "configured-capacity-mismatch": configured_capacity_mismatch,
                "zero-cost-positive-usage": zero_cost_benchmark,
                "invented-coding-minutes": invented_coding_benchmark,
                "oversized-formula-version": oversized_formula_version,
            }
            for index, (experiment, benchmark) in enumerate(benchmarks.items()):
                artifact = {
                    "experiment": experiment,
                    "startedAt": (started_at + timedelta(minutes=index * 2)).isoformat(),
                    "completedAt": (started_at + timedelta(minutes=index * 2 + 1)).isoformat(),
                    "status": "published",
                    "usage": complete_usage(
                        60,
                        60,
                        1e-12 if experiment == "zero-cost-positive-usage" else 1,
                    ),
                    "source": {"source": "otel_only", "charactersAdded": 0},
                    "benchmark": benchmark,
                }
                (sessions / f"session-{experiment}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            valid_session = store.session("valid-benchmark")
            self.assertIsNotNone(_validated_current_benchmark(
                valid_benchmark,
                usage={
                    "aiCostUsd": 1,
                    "phases": phase_evidence(60),
                },
                source={"source": "otel_only", "charactersAdded": 0},
                model_config_values=store._model_config_values(),
            ))
            self.assertEqual(valid_session["modelingStatus"], "available")
            self.assertIsNotNone(valid_session["benchmark"])

            for experiment in (
                "nan-benchmark", "partial-benchmark", "deep-benchmark",
                "missing-roi-benchmark", "empty-capacity-band-benchmark",
                "negative-cost-benchmark", "phase-mismatch-benchmark",
                "capacity-mismatch-benchmark",
                "cost-evidence-mismatch", "phase-evidence-mismatch",
                "source-evidence-mismatch", "configured-rate-mismatch",
                "configured-capacity-mismatch", "zero-cost-positive-usage",
                "invented-coding-minutes",
            ):
                session = store.session(experiment)
                self.assertTrue(session["currentFormula"])
                self.assertIsNone(session["benchmark"])
                self.assertIsNone(session["scenarioResult"])
                self.assertEqual(session["modelingStatus"], "invalid")

            oversized_version = store.session("oversized-formula-version")
            self.assertIsNone(oversized_version["formulaVersion"])
            self.assertIsNone(oversized_version["benchmark"])
            self.assertFalse(oversized_version["currentFormula"])
            self.assertEqual(oversized_version["modelingStatus"], "unavailable")

            overview = store.overview("base", 30)
            self.assertEqual(overview["modelingStatus"], "invalid")
            totals = overview["totals"]
            for key in (
                "estimatedManualMinutes", "estimatedMinutesSaved", "estimatedManualLaborCostUsd",
                "estimatedAiAssistedLaborCostUsd", "estimatedAiAssistedTotalCostUsd",
                "estimatedGrossCostSavingsUsd", "estimatedBenefitUsd", "netValueUsd",
            ):
                self.assertEqual(totals[key], 0, key)
            self.assertIsNone(totals["roi"])

    def test_portfolio_duration_requires_absolute_session_bounds(self) -> None:
        sessions = [
            {
                "startedAt": "2026-08-20T12:00:00Z",
                "completedAt": "2026-08-20T12:01:00Z",
                "durationSeconds": 60,
            },
            {
                "startedAt": "2026-08-20T13:00:00",
                "completedAt": "2026-08-20T14:00:00Z",
                "durationSeconds": 3_600,
            },
            {
                "startedAt": "2026-08-20 15:00:00+00:00",
                "completedAt": "2026-08-20T16:00:00Z",
                "durationSeconds": 3_600,
            },
            {
                "startedAt": "2026-08-20T17:00:00z",
                "completedAt": "2026-08-20T18:00:00Z",
                "durationSeconds": 3_600,
            },
        ]

        self.assertEqual(_union_duration_seconds(sessions), 60)

    def test_assisted_time_excludes_engagement_without_absolute_bounds(self) -> None:
        sessions = [
            {
                "startedAt": "2026-08-20T12:00:00Z",
                "completedAt": "2026-08-20T12:01:00Z",
                "durationSeconds": 60,
                "usage": {"engagedSeconds": 10},
            },
            {
                "startedAt": "2026-08-20T13:00:00",
                "completedAt": "2026-08-20T14:00:00Z",
                "durationSeconds": 3_600,
                "usage": {"engagedSeconds": 3_600},
            },
        ]

        self.assertEqual(_assisted_seconds(sessions), 10)

    def test_overview_fails_closed_when_finite_modeled_inputs_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text(
                json.dumps({
                    "loadedHourlyRateUsd": 1e288,
                    "benchmark": {"capacityRealization": 0.5},
                }),
                encoding="utf-8",
            )
            started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            overflowing_scenario = complete_scenario(
                estimatedManualMinutes=1e288,
                estimatedMinutesSaved=1e288,
                estimatedManualLaborCostUsd=1e288,
                estimatedAiAssistedLaborCostUsd=1,
                estimatedAiAssistedTotalCostUsd=2,
                estimatedGrossCostSavingsUsd=1e288,
                estimatedBenefitUsd=1e288,
                netValueUsd=1e288,
            )
            artifact = {
                "experiment": "finite-modeled-overflow",
                "startedAt": started_at.isoformat(),
                "completedAt": (started_at + timedelta(minutes=1)).isoformat(),
                "status": "published",
                "usage": {"aiCredits": 100, "aiCostUsd": 1, "engagedSeconds": 60},
                "source": {},
                "benchmark": {
                    "formulaVersion": CURRENT_FORMULA_VERSION,
                    "scenarios": {
                        name: dict(overflowing_scenario) for name in SCENARIOS
                    },
                },
            }
            (sessions / "session-finite-modeled-overflow.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )

            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            overview = store.overview("base", 30)
            totals = overview["totals"]
            self.assertEqual(totals["estimatedManualMinutes"], 0)
            self.assertEqual(totals["estimatedManualLaborCostUsd"], 0)
            self.assertEqual(totals["estimatedBenefitUsd"], 0)
            self.assertEqual(totals["netValueUsd"], 0)
            self.assertIsNone(totals["roi"])
            self.assertIsNone(totals["modeledDeliveryCostReduction"])
            self.assertIsNone(totals["taskTimeReduction"])
            json.dumps(overview, allow_nan=False)

    def test_overview_rejects_oversized_modeled_fields_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            scenario = {
                "estimatedManualMinutes": 10,
                "estimatedMinutesSaved": 5,
                "estimatedManualLaborCostUsd": 10,
                "estimatedAiAssistedLaborCostUsd": 2,
                "estimatedAiAssistedTotalCostUsd": 3,
                "estimatedGrossCostSavingsUsd": 7,
                "estimatedBenefitUsd": 1e308,
                "netValueUsd": 4,
            }
            artifact = {
                "experiment": "oversized-modeled-field",
                "startedAt": started_at.isoformat(),
                "completedAt": (started_at + timedelta(minutes=1)).isoformat(),
                "status": "published",
                "usage": {},
                "source": {},
                "benchmark": {
                    "formulaVersion": CURRENT_FORMULA_VERSION,
                    "scenarios": {"base": scenario},
                },
            }
            (sessions / "session-oversized-modeled-field.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            totals = store.overview("base", 30)["totals"]

            for key in (
                "estimatedManualMinutes", "estimatedMinutesSaved", "estimatedManualLaborCostUsd",
                "estimatedAiAssistedLaborCostUsd", "estimatedAiAssistedTotalCostUsd",
                "estimatedGrossCostSavingsUsd", "estimatedBenefitUsd", "netValueUsd",
            ):
                self.assertEqual(totals[key], 0, key)
            self.assertIsNone(totals["taskTimeReduction"])
            self.assertIsNone(totals["modeledDeliveryCostReduction"])
            self.assertIsNone(totals["roi"])

    def test_overview_rejects_missing_current_formula_branch_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            complete = complete_scenario(**{
                "estimatedManualMinutes": 10,
                "estimatedMinutesSaved": 5,
                "estimatedManualLaborCostUsd": 10,
                "estimatedAiAssistedLaborCostUsd": 2,
                "estimatedAiAssistedTotalCostUsd": 3,
                "estimatedGrossCostSavingsUsd": 7,
                "estimatedBenefitUsd": 5,
                "netValueUsd": 4,
            })
            artifacts = (
                {
                    "experiment": "complete-branch",
                    "benchmark": {
                        "formulaVersion": CURRENT_FORMULA_VERSION,
                        "scenarios": {"base": complete},
                    },
                },
                {
                    "experiment": "missing-branch",
                    "benchmark": {
                        "formulaVersion": CURRENT_FORMULA_VERSION,
                        "scenarios": {"pessimistic": complete},
                    },
                },
            )
            for index, artifact in enumerate(artifacts):
                payload = {
                    **artifact,
                    "startedAt": (started_at + timedelta(minutes=index * 2)).isoformat(),
                    "completedAt": (started_at + timedelta(minutes=index * 2 + 1)).isoformat(),
                    "status": "published",
                    "usage": {},
                    "source": {},
                }
                (sessions / f"session-{artifact['experiment']}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            totals = store.overview("base", 30)["totals"]

            for key in (
                "estimatedManualMinutes", "estimatedMinutesSaved", "estimatedManualLaborCostUsd",
                "estimatedAiAssistedLaborCostUsd", "estimatedAiAssistedTotalCostUsd",
                "estimatedGrossCostSavingsUsd", "estimatedBenefitUsd", "netValueUsd",
            ):
                self.assertEqual(totals[key], 0, key)
            self.assertIsNone(totals["taskTimeReduction"])
            self.assertIsNone(totals["modeledDeliveryCostReduction"])
            self.assertIsNone(totals["roi"])

    def test_overview_keeps_overflowed_task_ratio_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            scenario = complete_scenario(**{
                "estimatedManualMinutes": 1e-100,
                "estimatedMinutesSaved": 1e288,
                "estimatedManualLaborCostUsd": 0,
                "estimatedAiAssistedLaborCostUsd": 0,
                "estimatedAiAssistedTotalCostUsd": 0,
                "estimatedGrossCostSavingsUsd": 0,
                "estimatedBenefitUsd": 0,
                "netValueUsd": 0,
            })
            artifact = {
                "experiment": "overflowed-task-ratio",
                "startedAt": started_at.isoformat(),
                "completedAt": (started_at + timedelta(minutes=1)).isoformat(),
                "status": "published",
                "usage": {},
                "source": {},
                "benchmark": {
                    "formulaVersion": CURRENT_FORMULA_VERSION,
                    "scenarios": {name: dict(scenario) for name in SCENARIOS},
                },
            }
            (sessions / "session-overflowed-task-ratio.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            overview = store.overview("base", 30)

            self.assertIsNone(overview["totals"]["taskTimeReduction"])
            json.dumps(overview, allow_nan=False)

    def test_reindex_repairs_started_at_without_losing_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            corrected_start = datetime.now(timezone.utc) - timedelta(hours=2)
            usage = {
                "source": "otel_traces",
                "chatSpans": 1,
                "inputTokens": 100,
                "cacheReadTokens": 50,
                "uncachedInputTokens": 50,
                "outputTokens": 10,
                "reasoningTokens": 0,
                "aiCredits": 1,
                "aiCostUsd": 0.01,
                "models": [{
                    "model": "model",
                    "requests": 1,
                    "inputTokens": 100,
                    "cacheReadTokens": 50,
                    "uncachedInputTokens": 50,
                    "outputTokens": 10,
                    "reasoningTokens": 0,
                    "aiCredits": 1,
                    "aiCostUsd": 0.01,
                }],
                "elapsedSeconds": 60,
                "engagedSeconds": 30,
                "activeSeconds": 30,
                "activityDensity": 0.5,
                "phases": {
                    name: {
                        "activeSeconds": 30 if name == "unclassified" else 0,
                        "allocatedSeconds": 30 if name == "unclassified" else 0,
                        "toolActiveSeconds": 0,
                        "toolCalls": 0,
                        "modelSpans": 1 if name == "unclassified" else 0,
                        "uncachedInputTokens": 0,
                        "outputTokens": 0,
                        "reasoningTokens": 0,
                    }
                    for name in ("planning", "research", "coding", "validation", "unclassified")
                },
            }
            artifact_path = sessions / "session-repaired-start.json"
            artifact = {
                "experiment": "repaired-start",
                "startedAt": (corrected_start - timedelta(hours=1)).isoformat(),
                "completedAt": (corrected_start - timedelta(hours=1) + timedelta(minutes=1)).isoformat(),
                "status": "published",
                "usage": usage,
                "source": {},
            }
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            artifact["startedAt"] = corrected_start.isoformat()
            artifact["completedAt"] = (corrected_start + timedelta(minutes=1)).isoformat()
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            store.index_once()

            self.assertEqual(store.session("repaired-start")["startedAt"], artifact["startedAt"])
            health = store.insights(30)["evidenceHealth"]
            self.assertEqual(health["completeSessions"], 1)
            self.assertEqual(health["eligibleSessions"], 1)
            self.assertTrue(health["integrityPassed"])

    def test_insights_count_missing_and_invalid_completion_times_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            completed_at = (
                datetime.fromisoformat(started_at) + timedelta(minutes=1)
            ).isoformat()
            usage = {
                "source": "otel_traces",
                "chatSpans": 0,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
                "models": [],
                "elapsedSeconds": 0,
                "engagedSeconds": 0,
                "activeSeconds": 0,
                "activityDensity": 0,
                "phases": {
                    name: {
                        "activeSeconds": 0,
                        "allocatedSeconds": 0,
                        "toolActiveSeconds": 0,
                        "toolCalls": 0,
                        "modelSpans": 0,
                        "uncachedInputTokens": 0,
                        "outputTokens": 0,
                        "reasoningTokens": 0,
                    }
                    for name in ("planning", "research", "coding", "validation", "unclassified")
                },
            }
            artifacts = [
                {
                    "experiment": "missing-start",
                    "completedAt": started_at,
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "missing-completion",
                    "startedAt": started_at,
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "invalid-completion",
                    "startedAt": started_at,
                    "completedAt": "not-a-time",
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "sqlite-time-only",
                    "startedAt": started_at,
                    "completedAt": "12:00:00",
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "timezone-less",
                    "startedAt": started_at,
                    "completedAt": "2026-08-20T12:00:00",
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "date-only",
                    "startedAt": started_at,
                    "completedAt": "2026-08-20",
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "utc-normalization-overflow",
                    "startedAt": started_at,
                    "completedAt": "0001-01-01T00:00:00+23:59",
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
                {
                    "experiment": "non-object-usage",
                    "startedAt": started_at,
                    "completedAt": completed_at,
                    "status": "published",
                    "usage": [10**400],
                    "source": [],
                },
                {
                    "experiment": "oversized-usage",
                    "startedAt": started_at,
                    "completedAt": completed_at,
                    "status": "published",
                    "usage": {
                        **usage,
                        "chatSpans": 10**400,
                        "aiCredits": 10**400,
                        "aiCostUsd": 10**400,
                        "elapsedSeconds": 60,
                    },
                    "source": {"charactersAdded": 10**400},
                },
                *(
                    {
                        "experiment": f"aggregate-overflow-{suffix}",
                        "startedAt": started_at,
                        "completedAt": completed_at,
                        "status": "published",
                        "usage": {
                            "aiCredits": 1e308,
                            "aiCostUsd": 1e308,
                        },
                        "source": {},
                    }
                    for suffix in ("a", "b")
                ),
                {
                    "experiment": "nonfinite-evidence",
                    "startedAt": started_at,
                    "completedAt": completed_at,
                    "status": "published",
                    "usage": {"source": "otel_traces", "aiCostUsd": float("nan")},
                    "source": {"charactersAdded": float("inf")},
                },
                {
                    "experiment": "outside-window",
                    "startedAt": "2025-01-01T00:00:00Z",
                    "completedAt": "2025-01-01T01:00:00Z",
                    "status": "published",
                    "usage": usage,
                    "source": {},
                },
            ]
            for artifact in artifacts:
                (sessions / f"session-{artifact['experiment']}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
            huge_counter = "9" * 5_000
            (sessions / "session-huge-integer.json").write_text(
                "{" +
                '"experiment":"huge-integer",' +
                f'"startedAt":"{started_at}",' +
                f'"completedAt":"{completed_at}",' +
                '"status":"published","usage":{"chatSpans":' + huge_counter + '},"source":{}' +
                "}",
                encoding="utf-8",
            )

            store = ValueStore(root / "value.db", sessions, root / "traces.json", config_path)
            store.index_once()

            insights = store.insights(30)
            health = insights["evidenceHealth"]
            self.assertEqual(health["eligibleSessions"], 13)
            self.assertEqual(health["completeSessions"], 0)
            self.assertTrue(health["degraded"])

            overview = store.overview("base", 30)
            self.assertEqual(overview["totals"]["aiCostUsd"], 0)
            public_sessions = {
                session["experiment"]: session for session in overview["sessions"]
            }
            for experiment in ("non-object-usage", "oversized-usage", "nonfinite-evidence"):
                self.assertEqual(public_sessions[experiment]["usage"], {})
            self.assertEqual(public_sessions["nonfinite-evidence"]["source"], {})
            json.dumps(overview, allow_nan=False)
            exported_sessions, _ = store.export_data()
            json.dumps(exported_sessions, allow_nan=False)

    def test_initialization_removes_fallback_groups_from_exact_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            database_path = root / "value.db"
            store = ValueStore(database_path, sessions, root / "traces.json", config_path)
            timestamp = datetime.now(timezone.utc).isoformat()
            with closing(store._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO sessions VALUES (
                        ?, ?, ?, 'published', 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0,
                        '{"source":"copilot_turn_log"}', '{}', NULL, ?
                    )
                    """,
                    ("session", timestamp, timestamp, timestamp),
                )
                for ordinal, source in enumerate(("copilot_turn_log", "otel_trace"), start=1):
                    connection.execute(
                        """
                        INSERT INTO prompts VALUES (
                            ?, 'session', ?, ?, '', 0, 1, 0, 1, 0, 1, 0, 0, '{}', 0, ?
                        )
                        """,
                        (f"prompt-{ordinal}", ordinal, timestamp, source),
                    )
                connection.commit()
            self.assertEqual(len(store.prompts("session")), 2)

            migrated = ValueStore(database_path, sessions, root / "traces.json", config_path)

            prompts = migrated.prompts("session")
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0]["usageSource"], "copilot_turn_log")

            artifact = {
                "experiment": "session",
                "startedAt": timestamp,
                "completedAt": timestamp,
                "status": "published",
                "usage": {"source": "otel_traces"},
                "source": {},
                "benchmark": None,
            }
            (sessions / "session-updated.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )
            migrated.index_once()
            self.assertEqual(migrated.prompts("session"), [])

    def test_exact_turns_are_authoritative_for_the_resource_session(self) -> None:
        trace_session = {
            "spans": [
                {
                    "_started": 1_000,
                    "traceId": "first-trace",
                    "name": "chat model-a",
                    "attributes": [
                        attribute("copilot_chat.user_request", "Internal OTel request"),
                        attribute("gen_ai.conversation.id", "conversation"),
                        attribute("gen_ai.usage.output_tokens", 10, "intValue"),
                    ],
                },
                {
                    "_started": 2_000,
                    "traceId": "continuation-trace",
                    "name": "chat model-a",
                    "attributes": [
                        attribute("copilot_chat.user_request", "Different internal continuation"),
                        attribute("gen_ai.conversation.id", "conversation"),
                        attribute("gen_ai.usage.output_tokens", 20, "intValue"),
                    ],
                },
                {
                    "_started": 3_000,
                    "traceId": "other-conversation-trace",
                    "name": "chat model-a",
                    "attributes": [
                        attribute("copilot_chat.user_request", "One visible prompt"),
                        attribute("gen_ai.conversation.id", "other-conversation"),
                        attribute("gen_ai.usage.output_tokens", 40, "intValue"),
                    ],
                },
            ],
        }
        direct_turns = [{
            "conversation_id": "conversation",
            "started_at": "2026-08-21T12:00:00+00:00",
            "content": "One visible prompt",
            "captured_content_length": 18,
            "model_requests": 2,
            "tool_calls": 0,
            "input_tokens": 100,
            "cache_read_tokens": 50,
            "output_tokens": 30,
            "reasoning_tokens": 0,
            "ai_credits": 1.0,
            "ai_cost_usd": 0.01,
            "models": {"model-a": 2},
            "usage_source": "copilot_turn_log",
        }]

        groups = group_prompts(trace_session, direct_turns, content_enabled=False)

        self.assertEqual(len(groups), 1)
        exact = next(group for group in groups if group["usage_source"] == "copilot_turn_log")
        self.assertEqual(exact["model_requests"], 2)

    def test_extracts_user_visible_prompt_content(self) -> None:
        attachment_payload = json.dumps([
            {"type": "input_text", "text": "Review this screenshot"},
            {"type": "input_image", "image_url": "private"},
        ])
        self.assertEqual(_extract_prompt_content(attachment_payload), "Review this screenshot")
        self.assertEqual(_extract_prompt_content("\\nBuild the app\\n"), "Build the app")
        wrapped = json.dumps("<context>hidden</context>\n<userRequest>Ship the app</userRequest>")
        self.assertEqual(_extract_prompt_content(wrapped), "Ship the app")
        nested = json.dumps([{
            "type": "input_text",
            "text": "<context>hidden</context>\n<userRequest>Only this text</userRequest>",
        }])
        self.assertEqual(_extract_prompt_content(nested), "Only this text")

    def test_indexes_zero_cost_telemetry_only_sessions_without_roi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            now = datetime.now(timezone.utc).replace(microsecond=0)
            artifact = {
                "experiment": "session-telemetry-only",
                "startedAt": now.isoformat().replace("+00:00", "Z"),
                "completedAt": datetime.fromtimestamp(now.timestamp() + 1, timezone.utc).isoformat().replace("+00:00", "Z"),
                "status": "no_ai_usage",
                "usage": {
                    "chatSpans": 0,
                    "inputTokens": 0,
                    "cacheReadTokens": 0,
                    "uncachedInputTokens": 0,
                    "outputTokens": 0,
                    "reasoningTokens": 0,
                    "aiCredits": 0,
                    "aiCostUsd": 0,
                    "models": [],
                    "phases": {
                        "planning": {"toolCalls": 0},
                        "research": {"toolCalls": 0},
                        "coding": {"toolCalls": 0},
                        "validation": {"toolCalls": 0},
                        "unclassified": {"toolCalls": 0},
                    },
                },
                "source": {"source": "otel_only", "charactersAdded": 0},
                "benchmark": None,
            }
            (sessions / "session-telemetry-only.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )

            store = ValueStore(root / "value.db", sessions, root / "missing-traces.json", config_path)
            store.index_once()

            overview = store.overview("base", 30)
            self.assertEqual(overview["totals"]["sessions"], 1)
            self.assertEqual(overview["totals"]["aiCostUsd"], 0)
            self.assertIsNone(overview["totals"]["roi"])
            session = store.session("session-telemetry-only", "base")
            self.assertFalse(session["sourceEvidenceComplete"])
            self.assertEqual(session["retainedSourceCharacters"], 0)
            self.assertIsNone(session["scenarioResult"])

    def test_overview_counts_concurrent_sessions_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            active_scenarios = {
                name: scenario_config(unclassified_multiplier=2) for name in SCENARIOS
            }
            config_path.write_text(
                json.dumps(model_config(60, active_scenarios)),
                encoding="utf-8",
            )
            started = datetime.now(timezone.utc).replace(microsecond=0)

            def timestamp(offset_minutes: int) -> str:
                return datetime.fromtimestamp(
                    started.timestamp() + offset_minutes * 60,
                    timezone.utc,
                ).isoformat().replace("+00:00", "Z")

            def artifact(experiment: str, started_at: str, completed_at: str) -> dict:
                engaged_seconds = (
                    datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                    - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                ).total_seconds()
                result = complete_scenario(**{
                    "estimatedManualMinutes": 60,
                    "estimatedMinutesSaved": 30,
                    "estimatedManualLaborCostUsd": 60,
                    "estimatedAiAssistedLaborCostUsd": 30,
                    "estimatedAiAssistedTotalCostUsd": 31,
                    "estimatedGrossCostSavingsUsd": 29,
                    "estimatedBenefitUsd": 15,
                    "netValueUsd": 14,
                    "roi": 14,
                })
                return {
                    "experiment": experiment,
                    "startedAt": started_at,
                    "completedAt": completed_at,
                    "status": "published",
                    "usage": complete_usage(engaged_seconds, engaged_seconds, 1),
                    "source": {"source": "otel_only", "charactersAdded": 0},
                    "benchmark": complete_benchmark({
                        name: result for name in ("pessimistic", "base", "optimistic")
                    }),
                }

            artifacts = [
                artifact("session-a", timestamp(0), timestamp(30)),
                artifact("session-b", timestamp(10), timestamp(40)),
            ]
            for value in artifacts:
                (sessions / f"{value['experiment']}.json").write_text(json.dumps(value), encoding="utf-8")

            store = ValueStore(root / "value.db", sessions, root / "missing-traces.json", config_path)
            store.index_once()

            totals = store.overview("base", 30)["totals"]
            self.assertEqual(totals["durationSeconds"], 40 * 60)
            self.assertEqual(totals["estimatedManualMinutes"], 120)
            self.assertEqual(totals["estimatedMinutesSaved"], 80)
            self.assertEqual(totals["estimatedAiAssistedLaborCostUsd"], 40)
            self.assertEqual(totals["estimatedAiAssistedTotalCostUsd"], 42)
            self.assertEqual(totals["estimatedGrossCostSavingsUsd"], 78)
            self.assertEqual(totals["estimatedBenefitUsd"], 40)
            self.assertEqual(totals["netValueUsd"], 38)
            self.assertEqual(totals["roi"], 19)
            self.assertAlmostEqual(totals["taskTimeReduction"], 2 / 3)
            self.assertAlmostEqual(totals["modeledDeliveryCostReduction"], 78 / 120)
            self.assertEqual(totals["supersededFormulaSessions"], 0)
            self.assertEqual(store.overview("base", 30)["modelingStatus"], "available")

    def test_overview_excludes_superseded_formula_results_from_modeled_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            config_path = root / "value-model.local.json"
            config_path.write_text(
                json.dumps({"loadedHourlyRateUsd": 60, "benchmark": {"capacityRealization": 0.5}}),
                encoding="utf-8",
            )
            started = datetime.now(timezone.utc).replace(microsecond=0)
            completed = datetime.fromtimestamp(started.timestamp() + 600, timezone.utc)
            artifact = {
                "experiment": "session-stale-formula",
                "startedAt": started.isoformat().replace("+00:00", "Z"),
                "completedAt": completed.isoformat().replace("+00:00", "Z"),
                "status": "published",
                "usage": {
                    "source": "otel_traces",
                    "chatSpans": 1,
                    "inputTokens": 1,
                    "cacheReadTokens": 0,
                    "uncachedInputTokens": 1,
                    "outputTokens": 1,
                    "reasoningTokens": 0,
                    "aiCredits": 100,
                    "aiCostUsd": 1,
                    "models": [],
                    "phases": {},
                },
                "source": {"source": "otel_only", "charactersAdded": 0},
                "benchmark": {
                    "formulaVersion": CURRENT_FORMULA_VERSION - 1,
                    "scenarios": {
                        name: {"estimatedManualMinutes": 999, "netValueUsd": 999, "roi": 999}
                        for name in ("pessimistic", "base", "optimistic")
                    },
                },
            }
            (sessions / "session-stale-formula.json").write_text(json.dumps(artifact), encoding="utf-8")

            store = ValueStore(root / "value.db", sessions, root / "missing-traces.json", config_path)
            store.index_once()

            overview = store.overview("base", 30)
            totals = overview["totals"]
            self.assertEqual(totals["sessions"], 1)
            self.assertEqual(totals["supersededFormulaSessions"], 1)
            self.assertEqual(totals["estimatedManualMinutes"], 0)
            self.assertEqual(totals["netValueUsd"], 0)
            self.assertIsNone(overview["sessions"][0]["scenarioResult"])
            self.assertFalse(overview["sessions"][0]["currentFormula"])

    def test_indexes_prompt_from_rotated_trace_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            trace_path = root / "traces.json"
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            started = datetime.now(timezone.utc).replace(microsecond=0)
            started_ms = int(started.timestamp() * 1000)
            raw_session_id = "private-rotated-trace-session"
            experiment = _public_session_id(raw_session_id, started_ms)
            artifact = {
                "experiment": experiment,
                "startedAt": started.isoformat().replace("+00:00", "Z"),
                "completedAt": datetime.fromtimestamp(
                    (started_ms + 61_000) / 1000, timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "status": "published",
                "usage": {
                    "source": "otel_traces",
                    "chatSpans": 1,
                    "inputTokens": 20,
                    "cacheReadTokens": 0,
                    "uncachedInputTokens": 20,
                    "outputTokens": 5,
                    "reasoningTokens": 0,
                    "aiCredits": 1,
                    "aiCostUsd": 0.01,
                    "models": [],
                    "phases": {},
                },
                "source": {"source": "otel_only", "charactersAdded": 0},
                "benchmark": None,
            }
            (sessions / f"{experiment}.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )

            def nanos(offset_ms: int) -> str:
                return str((started_ms + offset_ms) * 1_000_000)

            def record(span: dict) -> dict:
                return {
                    "resourceSpans": [{
                        "resource": {"attributes": [
                            attribute("service.name", "copilot-chat"),
                            attribute("session.id", raw_session_id),
                        ]},
                        "scopeSpans": [{"spans": [span]}],
                    }],
                }

            rotated_trace = root / "traces-2026-08-05T10-01-00-size.json"
            rotated_trace.write_text(json.dumps(record({
                "traceId": "prompt-trace",
                "spanId": "prompt-span",
                "name": "chat model-a",
                "startTimeUnixNano": nanos(0),
                "endTimeUnixNano": nanos(1_000),
                "attributes": [
                    attribute("copilot_chat.user_request", "Retained prompt"),
                    attribute("gen_ai.conversation.id", "conversation"),
                    attribute("gen_ai.request.model", "model-a"),
                    attribute("copilot_chat.copilot_usage_nano_aiu", 1_000_000_000, "intValue"),
                ],
            })) + "\n", encoding="utf-8")
            trace_path.write_text(json.dumps(record({
                "traceId": "later-trace",
                "spanId": "later-span",
                "name": "execute_tool read_file",
                "startTimeUnixNano": nanos(60_000),
                "endTimeUnixNano": nanos(61_000),
                "attributes": [attribute("gen_ai.tool.name", "read_file")],
            })) + "\n", encoding="utf-8")
            second_rotated_trace = root / "traces-2026-08-05T10-02-00-size.json"
            second_rotated_trace.write_text(json.dumps(record({
                "traceId": "second-prompt-trace",
                "spanId": "second-prompt-span",
                "name": "chat model-a",
                "startTimeUnixNano": nanos(30_000),
                "endTimeUnixNano": nanos(31_000),
                "attributes": [
                    attribute("copilot_chat.user_request", "Second retained prompt"),
                    attribute("gen_ai.conversation.id", "conversation"),
                    attribute("gen_ai.request.model", "model-a"),
                    attribute("copilot_chat.copilot_usage_nano_aiu", 1_000_000_000, "intValue"),
                ],
            })) + "\n", encoding="utf-8")

            store = ValueStore(root / "value.db", sessions, trace_path, config_path)
            store.index_once()

            self.assertEqual(
                [prompt["content"] for prompt in store.prompts(experiment)],
                ["Retained prompt", "Second retained prompt"],
            )

            rotated_trace.unlink()
            trace_path.write_text("", encoding="utf-8")
            store.index_once()

            self.assertEqual(
                [prompt["content"] for prompt in store.prompts(experiment)],
                ["Retained prompt", "Second retained prompt"],
            )

    def test_indexes_direct_turn_when_request_and_conversation_are_on_separate_traces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            trace_path = root / "traces.json"
            config_path = root / "value-model.local.json"
            config_path.write_text("{}\n", encoding="utf-8")
            workspace_storage = root / "workspace-storage"
            started = datetime.now(timezone.utc).replace(microsecond=0)
            started_ms = int(started.timestamp() * 1000)
            raw_session_id = "private-split-trace-session"
            experiment = _public_session_id(raw_session_id, started_ms)
            artifact = {
                "experiment": experiment,
                "startedAt": started.isoformat().replace("+00:00", "Z"),
                "completedAt": datetime.fromtimestamp(
                    (started_ms + 1_000) / 1000, timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "status": "published",
                "usage": {
                    "source": "copilot_turn_log",
                    "chatSpans": 1,
                    "inputTokens": 30,
                    "cacheReadTokens": 10,
                    "uncachedInputTokens": 20,
                    "outputTokens": 5,
                    "reasoningTokens": 0,
                    "aiCredits": 2,
                    "aiCostUsd": 0.02,
                    "models": [],
                    "phases": {},
                },
                "source": {"source": "otel_only", "charactersAdded": 0},
                "benchmark": None,
            }
            (sessions / f"{experiment}.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )

            def nanos(offset_ms: int) -> str:
                return str((started_ms + offset_ms) * 1_000_000)

            trace_path.write_text(json.dumps({
                "resourceSpans": [{
                    "resource": {"attributes": [
                        attribute("service.name", "copilot-chat"),
                        attribute("session.id", raw_session_id),
                    ]},
                    "scopeSpans": [{"spans": [
                        {
                            "traceId": "request-trace",
                            "spanId": "request-span",
                            "name": "chat model-a",
                            "startTimeUnixNano": nanos(0),
                            "attributes": [
                                attribute("copilot_chat.user_request", "Direct prompt"),
                            ],
                        },
                        {
                            "traceId": "conversation-trace",
                            "spanId": "conversation-span",
                            "name": "chat model-a",
                            "startTimeUnixNano": nanos(100),
                            "attributes": [
                                attribute("gen_ai.conversation.id", "conversation"),
                                attribute("gen_ai.request.model", "model-a"),
                            ],
                        },
                    ]}],
                }],
            }) + "\n", encoding="utf-8")

            direct_log = (
                workspace_storage
                / "workspace"
                / "GitHub.copilot-chat"
                / "debug-logs"
                / "conversation"
                / "main.jsonl"
            )
            direct_log.parent.mkdir(parents=True)
            direct_log.write_text("\n".join(json.dumps(event) for event in [
                {
                    "ts": started_ms,
                    "type": "user_message",
                    "attrs": {"content": "Direct prompt"},
                },
                {
                    "ts": started_ms + 100,
                    "type": "llm_request",
                    "attrs": {
                        "model": "model-a",
                        "inputTokens": str(2**63 - 1),
                        "cachedTokens": 0,
                        "outputTokens": str(2**53 + 1),
                        "copilotUsageNanoAiu": 2_000_000_000,
                    },
                },
            ]) + "\n", encoding="utf-8")

            store = ValueStore(
                root / "value.db", sessions, trace_path, config_path, workspace_storage
            )
            store.index_once()

            prompts = store.prompts(experiment)
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0]["content"], "Direct prompt")
            self.assertEqual(prompts[0]["modelRequests"], 1)
            self.assertIsNone(prompts[0]["inputTokens"])
            self.assertEqual(prompts[0]["inputTokensExact"], str(2**63 - 1))
            self.assertIsNone(prompts[0]["outputTokens"])
            self.assertEqual(prompts[0]["outputTokensExact"], str(2**53 + 1))
            self.assertEqual(prompts[0]["usageSource"], "copilot_turn_log")

            artifact["usage"]["source"] = "otel_traces"
            (sessions / f"{experiment}.json").write_text(
                json.dumps(artifact), encoding="utf-8"
            )
            store.index_once()

            self.assertEqual(store.prompts(experiment), [])

    def test_indexes_sessions_and_ordered_prompt_groups_after_earlier_span_arrives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            sessions.mkdir()
            trace_path = root / "traces.json"
            config_path = root / "value-model.local.json"
            workspace_storage = root / "workspace-storage"
            started = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
            started_ms = int(started.timestamp() * 1000)
            raw_session_id = "private-test-session"
            experiment = _public_session_id(raw_session_id, started_ms)
            completed = datetime.fromtimestamp((started_ms + 60_000) / 1000, timezone.utc)

            def source_scenario(coding_minutes: float, ai_cost: float) -> dict[str, object]:
                manual_minutes = 1 + coding_minutes
                saved_minutes = coding_minutes
                result = complete_scenario(
                    estimatedManualMinutes=manual_minutes,
                    estimatedMinutesSaved=saved_minutes,
                    estimatedManualLaborCostUsd=manual_minutes * 2,
                    estimatedAiAssistedLaborCostUsd=2,
                    estimatedAiAssistedTotalCostUsd=2 + ai_cost,
                    estimatedGrossCostSavingsUsd=manual_minutes * 2 - 2 - ai_cost,
                    estimatedBenefitUsd=saved_minutes,
                    netValueUsd=saved_minutes - ai_cost,
                    roi=(saved_minutes - ai_cost) / ai_cost,
                )
                result["phases"]["coding"].update({
                    "estimatedManualMinutes": coding_minutes,
                    "estimatedMinutesSaved": coding_minutes,
                    "timeReduction": 1 if coding_minutes > 0 else 0,
                })
                result["phases"]["unclassified"].update({
                    "estimatedManualMinutes": 1,
                    "estimatedMinutesSaved": 0,
                    "timeReduction": 0,
                })
                return result

            active_scenarios = {
                "pessimistic": scenario_config(coding_fraction=0, coding_words_per_minute=60),
                "base": scenario_config(coding_fraction=1, coding_words_per_minute=24),
                "optimistic": scenario_config(coding_fraction=1, coding_words_per_minute=8),
            }

            artifact = {
                "experiment": experiment,
                "startedAt": started.isoformat().replace("+00:00", "Z"),
                "completedAt": completed.isoformat().replace("+00:00", "Z"),
                "status": "published",
                "usage": {
                    "source": "copilot_turn_log",
                    "chatSpans": 3,
                    "inputTokens": 180,
                    "cacheReadTokens": 60,
                    "uncachedInputTokens": 120,
                    "outputTokens": 30,
                    "reasoningTokens": 12,
                    "aiCredits": 9,
                    "aiCostUsd": 0.09,
                    "activeSeconds": 30,
                    "activityDensity": 0.5,
                    "elapsedSeconds": 60,
                    "engagedSeconds": 60,
                    "models": [{
                        "model": "model-a",
                        "requests": 3,
                        "inputTokens": 180,
                        "cacheReadTokens": 60,
                        "uncachedInputTokens": 120,
                        "outputTokens": 30,
                        "reasoningTokens": 12,
                        "aiCredits": 9,
                        "aiCostUsd": 0.09,
                    }],
                    "phases": {
                        "planning": {"activeSeconds": 0, "allocatedSeconds": 0, "toolActiveSeconds": 0, "toolCalls": 0, "modelSpans": 0, "uncachedInputTokens": 0, "outputTokens": 0, "reasoningTokens": 0},
                        "research": {"activeSeconds": 0, "allocatedSeconds": 0, "toolActiveSeconds": 0, "toolCalls": 0, "modelSpans": 0, "uncachedInputTokens": 0, "outputTokens": 0, "reasoningTokens": 0},
                        "coding": {"activeSeconds": 0, "allocatedSeconds": 0, "toolActiveSeconds": 0, "toolCalls": 0, "modelSpans": 0, "uncachedInputTokens": 0, "outputTokens": 0, "reasoningTokens": 0},
                        "validation": {"activeSeconds": 0, "allocatedSeconds": 0, "toolActiveSeconds": 0, "toolCalls": 0, "modelSpans": 0, "uncachedInputTokens": 0, "outputTokens": 0, "reasoningTokens": 0},
                        "unclassified": {"activeSeconds": 30, "allocatedSeconds": 60, "toolActiveSeconds": 0, "toolCalls": 0, "modelSpans": 1, "uncachedInputTokens": 0, "outputTokens": 0, "reasoningTokens": 0},
                    },
                },
                "source": {"source": "otel_source_delta", "charactersAdded": 120},
                "benchmark": complete_benchmark({
                        "pessimistic": source_scenario(0, 0.09),
                        "base": source_scenario(5, 0.09),
                        "optimistic": source_scenario(15, 0.09),
                }, retained_source_characters=120, ai_cost_usd=0.09),
            }
            config_path.write_text(
                json.dumps(model_config(120, active_scenarios)),
                encoding="utf-8",
            )

            def nanos(offset_ms: int) -> str:
                return str((started_ms + offset_ms) * 1_000_000)

            spans = [
                {
                    "traceId": "trace-early",
                    "spanId": "00",
                    "name": "execute_tool read_file",
                    "startTimeUnixNano": nanos(-28_000),
                    "endTimeUnixNano": nanos(-27_900),
                    "attributes": [attribute("gen_ai.tool.name", "read_file")],
                },
                {
                    "traceId": "trace-first",
                    "spanId": "01",
                    "name": "chat model-a",
                    "startTimeUnixNano": nanos(0),
                    "endTimeUnixNano": nanos(500),
                    "attributes": [
                        attribute("copilot_chat.user_request", "First prompt"),
                        attribute("gen_ai.conversation.id", "conversation"),
                        attribute("gen_ai.request.model", "model-a"),
                        attribute("gen_ai.usage.input_tokens", 100, "intValue"),
                        attribute("gen_ai.usage.cache_read.input_tokens", 40, "intValue"),
                        attribute("gen_ai.usage.output_tokens", 10, "intValue"),
                        attribute("gen_ai.usage.reasoning_tokens", 5, "intValue"),
                        attribute("copilot_chat.copilot_usage_nano_aiu", 1_000_000_000, "intValue"),
                    ],
                },
                {
                    "traceId": "trace-first",
                    "spanId": "02",
                    "name": "execute_tool read_file",
                    "startTimeUnixNano": nanos(600),
                    "endTimeUnixNano": nanos(700),
                    "attributes": [attribute("gen_ai.tool.name", "read_file")],
                },
                {
                    "traceId": "trace-first",
                    "spanId": "03",
                    "name": "chat model-a",
                    "startTimeUnixNano": nanos(800),
                    "endTimeUnixNano": nanos(1000),
                    "attributes": [
                        attribute("gen_ai.request.model", "model-a"),
                        attribute("gen_ai.usage.input_tokens", 50, "intValue"),
                        attribute("gen_ai.usage.cache_read.input_tokens", 20, "intValue"),
                        attribute("gen_ai.usage.output_tokens", 8, "intValue"),
                        attribute("gen_ai.usage.reasoning_tokens", 3, "intValue"),
                        attribute("copilot_chat.copilot_usage_nano_aiu", 2_000_000_000, "intValue"),
                    ],
                },
                {
                    "traceId": "trace-first",
                    "spanId": "03b",
                    "name": "chat model-a",
                    "startTimeUnixNano": nanos(1500),
                    "endTimeUnixNano": nanos(1600),
                    "attributes": [
                        attribute("copilot_chat.user_request", "Image associated with the above tool call:"),
                        attribute("gen_ai.conversation.id", "conversation"),
                        attribute("gen_ai.request.model", "model-a"),
                    ],
                },
                {
                    "traceId": "trace-second",
                    "spanId": "04",
                    "name": "chat model-b",
                    "startTimeUnixNano": nanos(2000),
                    "endTimeUnixNano": nanos(2500),
                    "attributes": [
                        attribute("copilot_chat.user_request", "Second prompt"),
                        attribute("gen_ai.conversation.id", "conversation-second"),
                        attribute("gen_ai.request.model", "model-b"),
                        attribute("gen_ai.usage.input_tokens", 30, "intValue"),
                        attribute("gen_ai.usage.output_tokens", 12, "intValue"),
                        attribute("gen_ai.usage.reasoning_tokens", 4, "intValue"),
                        attribute("copilot_chat.copilot_usage_nano_aiu", 1_000_000_000, "intValue"),
                    ],
                },
            ]
            record = {
                "resourceSpans": [{
                    "resource": {"attributes": [
                        attribute("service.name", "copilot-chat"),
                        attribute("session.id", raw_session_id),
                    ]},
                    "scopeSpans": [{"spans": spans}],
                }]
            }
            trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            direct_log = (
                workspace_storage
                / "workspace"
                / "GitHub.copilot-chat"
                / "debug-logs"
                / "conversation"
                / "main.jsonl"
            )
            direct_log.parent.mkdir(parents=True)
            direct_events = [
                {"ts": started_ms, "type": "user_message", "attrs": {"content": "First prompt"}},
                {"ts": started_ms + 100, "type": "llm_request", "attrs": {"model": "model-a", "inputTokens": 100, "cachedTokens": 40, "outputTokens": 10, "reasoningTokens": 5, "copilotUsageNanoAiu": 2_000_000_000}},
                {"ts": started_ms + 200, "type": "tool_call", "name": "read_file", "attrs": {}},
                {"ts": started_ms + 300, "type": "llm_request", "attrs": {"model": "model-a", "inputTokens": 50, "cachedTokens": 20, "outputTokens": 8, "reasoningTokens": 3, "copilotUsageNanoAiu": 3_000_000_000}},
            ]
            direct_log.write_text(
                "\n".join([
                    json.dumps(direct_events[0]),
                    "non-json diagnostic line",
                    *(json.dumps(event) for event in direct_events[1:]),
                ]) + "\n",
                encoding="utf-8",
            )
            second_direct_log = (
                workspace_storage
                / "workspace"
                / "GitHub.copilot-chat"
                / "debug-logs"
                / "conversation-second"
                / "main.jsonl"
            )
            second_direct_log.parent.mkdir(parents=True)
            second_direct_log.write_text(
                "\n".join(json.dumps(event) for event in [
                    {"ts": started_ms + 2_000, "type": "user_message", "attrs": {"content": "Second prompt"}},
                    {"ts": started_ms + 2_100, "type": "llm_request", "attrs": {"model": "model-b", "inputTokens": 30, "cachedTokens": 0, "outputTokens": 12, "reasoningTokens": 4, "copilotUsageNanoAiu": 4_000_000_000}},
                ]) + "\n",
                encoding="utf-8",
            )

            store = ValueStore(root / "value.db", sessions, trace_path, config_path, workspace_storage)
            store.index_once()
            self.assertEqual(store.overview("base", 30)["totals"]["sessions"], 0)
            (sessions / f"{experiment}.json").write_text(json.dumps(artifact), encoding="utf-8")
            store.index_once()

            overview = store.overview("base", 30)
            self.assertEqual(overview["totals"]["sessions"], 1)
            self.assertEqual(overview["totals"]["prompts"], 2)
            self.assertEqual(
                overview["sessions"][0]["latestMessageAt"],
                datetime.fromtimestamp((started_ms + 2_000) / 1000, timezone.utc).isoformat(),
            )
            self.assertAlmostEqual(overview["totals"]["taskTimeReduction"], 5 / 6)
            self.assertEqual(overview["totals"]["estimatedManualMinutes"], 6)
            self.assertEqual(overview["totals"]["estimatedManualLaborCostUsd"], 12)
            self.assertEqual(overview["totals"]["estimatedAiAssistedTotalCostUsd"], 2.09)
            self.assertEqual(overview["totals"]["estimatedGrossCostSavingsUsd"], 9.91)
            self.assertAlmostEqual(overview["totals"]["roi"], 4.91 / 0.09)
            session = store.session(experiment, "optimistic")
            self.assertEqual(session["promptCount"], 2)
            self.assertEqual(session["latestMessageAt"], overview["sessions"][0]["latestMessageAt"])
            self.assertEqual(session["scenarioResult"]["estimatedMinutesSaved"], 15)
            prompts = store.prompts(experiment)
            self.assertEqual([prompt["content"] for prompt in prompts], ["First prompt", "Second prompt"])
            self.assertEqual(prompts[0]["modelRequests"], 2)
            self.assertEqual(prompts[0]["toolCalls"], 1)
            self.assertEqual(prompts[0]["aiCredits"], 5)
            self.assertAlmostEqual(prompts[0]["aiCostUsd"], 0.05)
            self.assertEqual(prompts[0]["usageSource"], "copilot_turn_log")
            self.assertEqual(prompts[1]["aiCredits"], 4)
            self.assertEqual(prompts[1]["usageSource"], "copilot_turn_log")
            self.assertEqual(store.prompt(prompts[1]["promptId"])["models"], {"model-b": 1})
            self.assertEqual(store.methodology()["claim"], "Modeled AI Usage ROI")
            self.assertIn("manualLaborCost", store.methodology()["formulas"])
            self.assertIn("grossCostSavings", store.methodology()["formulas"])
            self.assertIn("portfolioAiTime", store.methodology()["formulas"])
            insights = store.insights(30)
            self.assertEqual(insights["summary"]["metrics"], 12)
            self.assertEqual(
                next(metric for metric in insights["metrics"] if metric["key"] == "session_usage_coverage")["current"],
                1,
            )

            direct_log.unlink()
            second_direct_log.unlink()
            artifact["usage"].update({
                "source": "otel_traces",
                "aiCredits": 4,
                "aiCostUsd": 0.04,
            })
            artifact["benchmark"] = complete_benchmark({
                "pessimistic": source_scenario(0, 0.04),
                "base": source_scenario(5, 0.04),
                "optimistic": source_scenario(15, 0.04),
            }, retained_source_characters=120, ai_cost_usd=0.04)
            (sessions / f"{experiment}.json").write_text(json.dumps(artifact), encoding="utf-8")
            store.index_once()
            fallback_prompts = store.prompts(experiment)
            self.assertEqual([prompt["content"] for prompt in fallback_prompts], ["First prompt", "Second prompt"])
            self.assertEqual(fallback_prompts[0]["aiCredits"], 3)
            self.assertEqual(fallback_prompts[0]["usageSource"], "otel_trace")

            config_path.write_text(
                json.dumps({
                    "loadedHourlyRateUsd": 120,
                    "benchmark": {"capacityRealization": 0.5},
                    "promptStorage": {"enabled": False, "retentionDays": 30},
                }),
                encoding="utf-8",
            )
            with patch.object(
                store,
                "_stable_trace_sessions",
                side_effect=RuntimeError("unstable trace archive"),
            ):
                with self.assertRaisesRegex(RuntimeError, "unstable trace archive"):
                    store._index_prompts()
            disabled_prompts = store.prompts(experiment)
            self.assertFalse(disabled_prompts[0]["contentAvailable"])
            self.assertEqual(disabled_prompts[0]["content"], "")


if __name__ == "__main__":
    unittest.main()