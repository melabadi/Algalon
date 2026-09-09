from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from backend.app.insights import build_insights


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _phases(
    coding_calls: int = 80,
    validation_calls: int = 0,
    unclassified_calls: int = 1_519,
    active_seconds: int = 5_400,
    engaged_seconds: int = 5_400,
    model_spans: int = 1,
) -> dict[str, dict[str, int]]:
    result = {
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
    }
    result["coding"]["toolCalls"] = coding_calls
    result["validation"]["toolCalls"] = validation_calls
    result["unclassified"].update({
        "activeSeconds": active_seconds,
        "allocatedSeconds": engaged_seconds,
        "toolCalls": unclassified_calls,
        "modelSpans": model_spans,
    })
    return result


def _session(index: int, *, validation: bool, **usage_overrides: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "source": "otel_traces",
        "chatSpans": 160,
        "inputTokens": 1_600_000,
        "cacheReadTokens": 0,
        "uncachedInputTokens": 1_600_000,
        "outputTokens": 1_600_000,
        "reasoningTokens": 4_000,
        "aiCredits": 32_000,
        "aiCostUsd": 320,
        "elapsedSeconds": 10_800,
        "engagedSeconds": 5_400,
        "activeSeconds": 5_400,
        "activityDensity": 0.5,
        "phases": _phases(validation_calls=1 if validation else 0),
    }
    usage.update(usage_overrides)
    if "models" not in usage_overrides:
        usage["models"] = [] if usage["chatSpans"] == 0 else [{
            "model": "model",
            "requests": usage["chatSpans"],
            "inputTokens": usage["inputTokens"],
            "cacheReadTokens": usage["cacheReadTokens"],
            "uncachedInputTokens": usage["uncachedInputTokens"],
            "outputTokens": usage["outputTokens"],
            "reasoningTokens": usage["reasoningTokens"],
            "aiCredits": usage["aiCredits"],
            "aiCostUsd": usage["aiCostUsd"],
        }]
    return {
        "experiment": f"session-{index}",
        "completedAt": f"2026-08-{index + 1:02d}T12:00:00Z",
        "durationSeconds": 10_800,
        "usage": usage,
    }


class BuildInsightsTests(unittest.TestCase):
    def _result(self) -> dict[str, object]:
        sessions = [_session(index, validation=index == 0) for index in range(5)]
        return build_insights(sessions, [], 30, now=NOW)

    def test_builds_descriptive_measurements_without_guardrails(self) -> None:
        result = self._result()
        by_key = {metric["key"]: metric for metric in result["metrics"]}

        self.assertEqual(result["summary"]["metrics"], 12)
        self.assertEqual(result["summary"]["guardrails"], 0)
        self.assertNotIn("unclassified_phase_share", by_key)
        self.assertNotIn("context_growth", by_key)
        self.assertNotIn("uncached_input_tokens", by_key)
        self.assertEqual(by_key["cache_read_ratio"]["messageKey"], "insights.metrics.cache_read_ratio")
        self.assertEqual(by_key["cache_read_ratio"]["actionKey"], "insights.actions.cache_read_ratio")
        self.assertEqual(
            by_key["cache_read_ratio"]["reference"]["messageKey"], "insights.references.cache_read_ratio",
        )

    def test_no_metric_declares_an_unsupported_threshold(self) -> None:
        by_key = {metric["key"]: metric for metric in self._result()["metrics"]}
        self.assertEqual(len(by_key), 12)
        for key, metric in by_key.items():
            self.assertIsNone(metric["normalZone"], key)
            self.assertEqual(metric["status"], "descriptive", key)
            self.assertEqual(metric["zone"], "descriptive", key)

    def test_reference_support_and_notes_never_claim_an_external_cutoff(self) -> None:
        result = self._result()
        expected_support = {
            "cache_read_ratio": "direct",
            "uncached_input_per_request": "direct",
            "context_length": "proxy",
            "reasoning_share": "proxy",
            "output_tokens": "direct",
            "model_requests": "direct",
            "tool_calls": "proxy",
            "ai_cost_usd": "none",
            "duration_seconds": "none",
            "activity_density": "direct",
            "validation_coverage": "proxy",
            "session_usage_coverage": "direct",
        }
        self.assertEqual(
            {metric["key"]: metric["reference"]["support"] for metric in result["metrics"]},
            expected_support,
        )
        coverage = next(metric for metric in result["metrics"] if metric["key"] == "session_usage_coverage")
        self.assertEqual(coverage["reference"]["kind"], "local_measurement")
        self.assertEqual(coverage["reference"]["url"], "")

    def test_scopes_match_authoritative_session_normalization(self) -> None:
        by_key = {metric["key"]: metric for metric in self._result()["metrics"]}
        for key in ("uncached_input_per_request", "context_length", "output_tokens", "tool_calls", "ai_cost_usd"):
            self.assertEqual(by_key[key]["scope"], "request", key)
        for key in ("cache_read_ratio", "reasoning_share", "model_requests", "duration_seconds", "activity_density"):
            self.assertEqual(by_key[key]["scope"], "session", key)
        self.assertEqual(by_key["validation_coverage"]["group"], "evidence")
        self.assertEqual(by_key["session_usage_coverage"]["group"], "evidence")
        self.assertEqual(by_key["cache_read_ratio"]["group"], "behavior")

    def test_calculates_session_and_request_values_without_classifying_them(self) -> None:
        by_key = {metric["key"]: metric for metric in self._result()["metrics"]}
        self.assertEqual(by_key["cache_read_ratio"]["current"], 0)
        self.assertEqual(by_key["uncached_input_per_request"]["current"], 10_000)
        self.assertEqual(by_key["context_length"]["current"], 10_000)
        self.assertEqual(by_key["output_tokens"]["current"], 10_000)
        self.assertEqual(by_key["model_requests"]["current"], 160)
        self.assertEqual(by_key["tool_calls"]["current"], 1_599 / 160)
        self.assertEqual(by_key["ai_cost_usd"]["current"], 2)
        self.assertEqual(by_key["activity_density"]["current"], 0.5)
        for metric in by_key.values():
            self.assertEqual(metric["status"], "descriptive")

    def test_exposes_explicit_operational_signals_without_reusing_status(self) -> None:
        by_key = {metric["key"]: metric for metric in self._result()["metrics"]}

        self.assertEqual(by_key["cache_read_ratio"]["signal"]["level"], "danger")
        self.assertEqual(by_key["cache_read_ratio"]["signal"]["attentionBoundary"], 0.5)
        self.assertEqual(by_key["cache_read_ratio"]["signal"]["dangerBoundary"], 0.2)
        self.assertEqual(by_key["uncached_input_per_request"]["signal"]["level"], "none")
        self.assertEqual(by_key["context_length"]["signal"]["level"], "none")
        self.assertEqual(by_key["output_tokens"]["signal"]["level"], "danger")
        self.assertEqual(by_key["validation_coverage"]["signal"]["level"], "insufficient")
        self.assertEqual(by_key["session_usage_coverage"]["signal"]["level"], "none")
        self.assertEqual(by_key["model_requests"]["signal"]["level"], "not_rated")
        self.assertEqual(by_key["duration_seconds"]["signal"]["level"], "not_rated")
        for metric in by_key.values():
            self.assertEqual(metric["status"], "descriptive")

    def test_extreme_values_do_not_create_unsupported_priorities(self) -> None:
        result = self._result()
        self.assertEqual(result["priorities"], [])
        self.assertEqual(result["summary"]["actions"], 0)
        self.assertEqual(result["summary"]["watch"], 0)

    def test_sessions_are_ineligible_until_they_settle(self) -> None:
        session = _session(0, validation=True)
        session["completedAt"] = (NOW - timedelta(minutes=5)).isoformat()
        result = build_insights([session], [], 30, now=NOW)
        by_key = {metric["key"]: metric for metric in result["metrics"]}
        self.assertEqual(by_key["duration_seconds"]["status"], "unavailable")
        self.assertEqual(by_key["activity_density"]["aggregateSize"], 0)
        self.assertEqual(by_key["validation_coverage"]["status"], "unavailable")
        self.assertEqual(by_key["cache_read_ratio"]["aggregateSize"], 0)

    def test_session_usage_coverage_is_available_from_the_first_session(self) -> None:
        result = build_insights([_session(0, validation=True)], [], 30, now=NOW)
        coverage = next(metric for metric in result["metrics"] if metric["key"] == "session_usage_coverage")
        self.assertEqual(coverage["status"], "descriptive")
        self.assertEqual(coverage["aggregateSize"], 1)
        self.assertEqual(coverage["minimumBaseline"], 0)
        self.assertEqual(coverage["current"], 1)
        self.assertEqual(coverage["signal"]["level"], "none")
        self.assertIsNone(coverage["severity"])

    def test_incomplete_session_usage_fails_closed(self) -> None:
        session = _session(0, validation=True)
        usage = session["usage"]
        assert isinstance(usage, dict)
        usage.pop("reasoningTokens")
        result = build_insights([session], [], 30, now=NOW)
        coverage = next(metric for metric in result["metrics"] if metric["key"] == "session_usage_coverage")
        self.assertEqual(coverage["status"], "descriptive")
        self.assertEqual(coverage["current"], 0)
        self.assertEqual(coverage["aggregateSize"], 1)
        self.assertIsNone(coverage["severityLabel"])
        health = result["evidenceHealth"]
        self.assertTrue(health["degraded"])
        self.assertFalse(health["integrityPassed"])
        self.assertEqual(health["completeSessions"], 0)
        self.assertEqual(health["eligibleSessions"], 1)
        self.assertIn("Insights are withheld", health["message"])
        self.assertIsNone(result["metrics"][0]["current"])

    def test_withholds_judgment_without_a_direct_threshold_basis(self) -> None:
        result = self._result()
        health = result["evidenceHealth"]

        self.assertEqual(result["summary"]["guardrails"], 0)
        self.assertEqual(result["priorities"], [])
        for metric in result["metrics"]:
            self.assertIsNone(metric["normalZone"], metric["key"])
            self.assertEqual(metric["status"], "descriptive", metric["key"])

        self.assertEqual(health["completeSessions"], 5)
        self.assertEqual(health["eligibleSessions"], 5)
        self.assertTrue(health["integrityPassed"])
        self.assertIsNone(health["message"])

    def test_metrics_are_unavailable_without_observations(self) -> None:
        result = build_insights([], [], 30, now=NOW)
        by_key = {metric["key"]: metric for metric in result["metrics"]}
        self.assertEqual(by_key["cache_read_ratio"]["status"], "unavailable")
        self.assertEqual(by_key["cache_read_ratio"]["aggregateSize"], 0)
        self.assertEqual(by_key["duration_seconds"]["status"], "unavailable")
        self.assertFalse(result["evidenceHealth"]["degraded"])
        self.assertEqual(result["priorities"], [])

    def test_request_metrics_exclude_sessions_without_model_requests(self) -> None:
        session = _session(
            0, validation=False, chatSpans=0, inputTokens=0, cacheReadTokens=0,
            uncachedInputTokens=0, outputTokens=0, reasoningTokens=0, aiCredits=0, aiCostUsd=0,
            engagedSeconds=0, activeSeconds=0, activityDensity=0,
            phases=_phases(
                coding_calls=0, validation_calls=0, unclassified_calls=0,
                active_seconds=0, engaged_seconds=0, model_spans=0,
            ),
        )
        result = build_insights([session], [], 30, now=NOW)
        by_key = {metric["key"]: metric for metric in result["metrics"]}
        self.assertEqual(by_key["model_requests"]["current"], 0)
        for key in ("uncached_input_per_request", "context_length", "output_tokens", "tool_calls", "ai_cost_usd"):
            self.assertEqual(by_key[key]["status"], "unavailable", key)

    def test_previous_window_supplies_the_prior_period_comparison(self) -> None:
        current = _session(
            0, validation=True, cacheReadTokens=1_600_000, uncachedInputTokens=0,
        )
        previous = _session(1, validation=True)
        result = build_insights(
            [current], [], 30, previous_sessions=[previous], previous_prompts=[], now=NOW,
        )
        cache = next(metric for metric in result["metrics"] if metric["key"] == "cache_read_ratio")
        self.assertEqual(cache["current"], 1.0)
        self.assertEqual(cache["previous"], 0.0)
        self.assertEqual(cache["trend"], "up")

    def test_current_integrity_failure_withholds_current_and_trend(self) -> None:
        session = _session(0, validation=True)
        usage = session["usage"]
        assert isinstance(usage, dict)
        usage.pop("outputTokens")
        previous_session = _session(1, validation=True)

        result = build_insights(
            [session], [], 30,
            previous_sessions=[previous_session], previous_prompts=[], now=NOW,
        )
        cache = next(metric for metric in result["metrics"] if metric["key"] == "cache_read_ratio")

        self.assertIsNone(cache["current"])
        self.assertIsNone(cache["previous"])
        self.assertIsNone(cache["trend"])

    def test_previous_integrity_failure_withholds_only_previous_and_trend(self) -> None:
        session = _session(
            0, validation=True, cacheReadTokens=1_600_000, uncachedInputTokens=0,
        )
        previous_session = _session(1, validation=True)
        previous_usage = previous_session["usage"]
        assert isinstance(previous_usage, dict)
        previous_usage.pop("outputTokens")

        result = build_insights(
            [session], [], 30,
            previous_sessions=[previous_session], previous_prompts=[], now=NOW,
        )
        cache = next(metric for metric in result["metrics"] if metric["key"] == "cache_read_ratio")

        self.assertEqual(cache["current"], 1.0)
        self.assertIsNone(cache["previous"])
        self.assertIsNone(cache["trend"])


if __name__ == "__main__":
    unittest.main()
