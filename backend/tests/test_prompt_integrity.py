from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import tempfile
import unittest

from backend.app.insights import MAX_MODEL_ROWS, build_insights
from backend.app.prompt_index import (
    group_prompts,
    prompt_group_counters_valid,
    read_copilot_turns,
)


NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def attribute(key: str, value: object, value_type: str = "stringValue") -> dict[str, object]:
    return {"key": key, "value": {value_type: str(value)}}


def direct_turn(index: int) -> dict[str, object]:
    return {
        "conversation_id": "conversation",
        "started_at": f"2026-08-20T12:{index:02d}:00+00:00",
        "content": f"Visible user turn {index}",
        "captured_content_length": 20,
        "model_requests": 2,
        "tool_calls": index % 3,
        "input_tokens": 1_000 + index,
        "cache_read_tokens": 500,
        "output_tokens": 100 + index,
        "reasoning_tokens": 10 + index,
        "ai_credits": 1.0 + index / 10,
        "ai_cost_usd": 0.01 + index / 1_000,
        "models": {"model": 2},
        "usage_source": "copilot_turn_log",
    }


def phases(
    coding: int = 0,
    validation: int = 0,
    *,
    active_seconds: float = 0,
    engaged_seconds: float = 0,
    model_spans: int | None = None,
) -> dict[str, dict[str, int | float]]:
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
    result["coding"]["toolCalls"] = coding
    result["validation"]["toolCalls"] = validation
    active_phase = "coding" if coding > 0 else "validation" if validation > 0 else "unclassified"
    result[active_phase]["activeSeconds"] = active_seconds
    result[active_phase]["allocatedSeconds"] = engaged_seconds
    result[active_phase]["modelSpans"] = (
        1 if model_spans is None and active_seconds > 0 else model_spans or 0
    )
    return result


def models(
    requests: int,
    input_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    ai_credits: float,
    ai_cost_usd: float,
) -> list[dict[str, object]]:
    return [{
        "model": "model",
        "requests": requests,
        "inputTokens": input_tokens,
        "cacheReadTokens": cache_read_tokens,
        "uncachedInputTokens": max(0, input_tokens - cache_read_tokens),
        "outputTokens": output_tokens,
        "reasoningTokens": reasoning_tokens,
        "aiCredits": ai_credits,
        "aiCostUsd": ai_cost_usd,
    }]


class PromptIntegrityTests(unittest.TestCase):
    def test_operational_insights_are_complete_without_prompt_index_data(self) -> None:
        session = {
            "experiment": "resource-session",
            "completedAt": "2026-08-20T13:00:00Z",
            "durationSeconds": 600,
            "usage": {
                "source": "otel_traces",
                "chatSpans": 10,
                "inputTokens": 1_000,
                "cacheReadTokens": 500,
                "uncachedInputTokens": 500,
                "outputTokens": 200,
                "reasoningTokens": 100,
                "aiCredits": 50,
                "aiCostUsd": 0.5,
                "models": models(10, 1_000, 500, 200, 100, 50, 0.5),
                "elapsedSeconds": 600,
                "engagedSeconds": 300,
                "activeSeconds": 300,
                "activityDensity": 0.5,
                "phases": phases(
                    coding=3, validation=2, active_seconds=300, engaged_seconds=300,
                ),
            },
        }
        noisy_prompt = {
            "experiment": "resource-session",
            "startedAt": "2026-08-20T12:00:00Z",
            "modelRequests": 1,
            "toolCalls": 0,
            "inputTokens": 10,
            "cacheReadTokens": 0,
            "outputTokens": 1,
            "reasoningTokens": 0,
            "aiCostUsd": 0.01,
            "usageSource": "otel_trace",
        }

        without_prompts = build_insights([session], [], 30, now=NOW)
        with_noise = build_insights([session], [noisy_prompt], 30, now=NOW)
        expected = {
            "cache_read_ratio": 0.5,
            "uncached_input_per_request": 50,
            "context_length": 100,
            "reasoning_share": 1 / 3,
            "output_tokens": 20,
            "model_requests": 10,
            "tool_calls": 0.5,
            "ai_cost_usd": 0.05,
            "duration_seconds": 600,
            "activity_density": 0.5,
            "validation_coverage": 1,
            "session_usage_coverage": 1,
        }

        for result in (without_prompts, with_noise):
            by_key = {metric["key"]: metric for metric in result["metrics"]}
            self.assertEqual(set(by_key), set(expected))
            for key, value in expected.items():
                self.assertAlmostEqual(by_key[key]["current"], value, msg=key)
            self.assertTrue(result["evidenceHealth"]["integrityPassed"])

        self.assertEqual(without_prompts["metrics"], with_noise["metrics"])

    def test_direct_turn_reader_is_bounded_to_the_resource_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "main.jsonl"
            events = []
            for timestamp, content, output_tokens in (
                (1_000, "Before", 10),
                (2_000, "Inside", 20),
                (3_000, "After", 30),
            ):
                events.extend((
                    {"ts": timestamp, "type": "user_message", "attrs": {"content": content}},
                    {"ts": timestamp + 1, "type": "llm_request", "attrs": {
                        "model": "model", "inputTokens": 100, "cachedTokens": 50,
                        "outputTokens": output_tokens, "copilotUsageNanoAiu": 1_000_000_000,
                    }},
                ))
            path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            turns = read_copilot_turns(path, 1_500, 2_500)

            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["content"], "Inside")
            self.assertEqual(turns[0]["output_tokens"], 20)

    def test_direct_turn_reader_preserves_exact_signed_64_bit_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "main.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in (
                {"ts": 2_000, "type": "user_message", "attrs": {"content": "Exact"}},
                {"ts": 2_001, "type": "llm_request", "attrs": {
                    "model": "model",
                    "inputTokens": str(2**53 + 1),
                    "cachedTokens": 1,
                    "outputTokens": str(2**63 - 1),
                    "reasoningTokens": str(2**63),
                    "copilotUsageNanoAiu": 1_000_000_000,
                }},
            )) + "\n", encoding="utf-8")

            turn = read_copilot_turns(path)[0]

            self.assertEqual(turn["input_tokens"], 2**53 + 1)
            self.assertEqual(turn["cache_read_tokens"], 1)
            self.assertEqual(turn["output_tokens"], 2**63 - 1)
            self.assertIsNone(turn["reasoning_tokens"])
            invalid_group = {
                **turn,
                "ordinal": 1,
            }
            self.assertFalse(prompt_group_counters_valid(invalid_group))
            self.assertFalse(prompt_group_counters_valid({
                **invalid_group,
                "reasoning_tokens": -1,
            }))

    def test_direct_turn_reader_detects_signed_64_bit_aggregate_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "main.jsonl"
            events = [{
                "ts": 2_000,
                "type": "user_message",
                "attrs": {"content": "Overflow"},
            }]
            events.extend({
                "ts": 2_001 + index,
                "type": "llm_request",
                "attrs": {"model": "model", "inputTokens": str(2**62)},
            } for index in range(2))
            path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            turn = read_copilot_turns(path)[0]

            self.assertIsNone(turn["input_tokens"])

    def test_prompt_groups_reject_invalid_nano_aiu(self) -> None:
        for nano_aiu in (-1, str(2**63), "not-a-counter"):
            with self.subTest(nano_aiu=nano_aiu), tempfile.TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / "main.jsonl"
                path.write_text("\n".join(json.dumps(event) for event in (
                    {"ts": 2_000, "type": "user_message", "attrs": {"content": "Invalid cost"}},
                    {"ts": 2_001, "type": "llm_request", "attrs": {
                        "model": "model",
                        "copilotUsageNanoAiu": nano_aiu,
                    }},
                )) + "\n", encoding="utf-8")

                turn = read_copilot_turns(path)[0]

                self.assertIsNone(turn["ai_credits"])
                self.assertIsNone(turn["ai_cost_usd"])
                self.assertFalse(prompt_group_counters_valid({**turn, "ordinal": 1}))

    def test_exact_turns_produce_100_percent_integrity_despite_noisy_otel_requests(self) -> None:
        direct_turns = [direct_turn(index) for index in range(20)]
        trace_session = {
            "spans": [
                {
                    "_started": 1_000 + index * 10 + continuation,
                    "traceId": f"internal-{index}-{continuation}",
                    "name": "chat model",
                    "attributes": [
                        attribute(
                            "copilot_chat.user_request",
                            f"Internal request envelope {index}-{continuation}",
                        ),
                        attribute("gen_ai.conversation.id", "conversation"),
                        attribute("gen_ai.usage.input_tokens", 10_000, "intValue"),
                        attribute("gen_ai.usage.output_tokens", 1_000, "intValue"),
                    ],
                }
                for index in range(20)
                for continuation in range(3)
            ],
        }

        groups = group_prompts(trace_session, direct_turns, content_enabled=False)

        self.assertEqual(len(groups), len(direct_turns))
        self.assertEqual({group["usage_source"] for group in groups}, {"copilot_turn_log"})
        for field in (
            "model_requests",
            "tool_calls",
            "input_tokens",
            "cache_read_tokens",
            "output_tokens",
            "reasoning_tokens",
            "ai_credits",
            "ai_cost_usd",
        ):
            self.assertEqual(
                sum(group[field] for group in groups),
                sum(turn[field] for turn in direct_turns),
                field,
            )

    def test_incomplete_or_invalid_session_usage_fails_closed(self) -> None:
        base_usage = {
            "source": "otel_traces",
            "chatSpans": 1,
            "inputTokens": 100,
            "cacheReadTokens": 50,
            "uncachedInputTokens": 50,
            "outputTokens": 10,
            "reasoningTokens": 0,
            "aiCredits": 1,
            "aiCostUsd": 0.01,
            "models": models(1, 100, 50, 10, 0, 1, 0.01),
            "elapsedSeconds": 60,
            "engagedSeconds": 30,
            "activeSeconds": 30,
            "activityDensity": 0.5,
            "phases": phases(active_seconds=30, engaged_seconds=30),
        }
        incomplete = {**base_usage}
        incomplete.pop("outputTokens")
        invalid = {**base_usage, "uncachedInputTokens": 49}
        missing_usage_cases = []
        for field in ("elapsedSeconds", "engagedSeconds"):
            candidate = {**base_usage}
            candidate.pop(field)
            missing_usage_cases.append(candidate)
        missing_phase_cases = []
        for field in (
            "activeSeconds", "allocatedSeconds", "toolActiveSeconds", "modelSpans",
            "uncachedInputTokens", "outputTokens", "reasoningTokens",
        ):
            candidate_phases = {
                name: dict(value) for name, value in base_usage["phases"].items()
            }
            candidate_phases["planning"].pop(field)
            missing_phase_cases.append({**base_usage, "phases": candidate_phases})
        base_phases = base_usage["phases"]
        non_proportional_phases = {
            name: dict(value) for name, value in base_phases.items()
        }
        non_proportional_phases["planning"].update({
            "activeSeconds": 10,
            "allocatedSeconds": 0,
        })
        non_proportional_phases["unclassified"].update({
            "activeSeconds": 20,
            "allocatedSeconds": 30,
        })
        excessive_phase_counters = {
            name: dict(value) for name, value in base_phases.items()
        }
        excessive_phase_counters["planning"]["modelSpans"] = 2
        orphaned_phase_tokens = {
            name: dict(value) for name, value in base_phases.items()
        }
        orphaned_phase_tokens["planning"]["outputTokens"] = 1
        orphaned_tool_runtime = {
            name: dict(value) for name, value in base_phases.items()
        }
        orphaned_tool_runtime["planning"]["toolActiveSeconds"] = 1
        orphaned_phase_time = {
            name: dict(value) for name, value in base_phases.items()
        }
        orphaned_phase_time["unclassified"]["modelSpans"] = 0
        excessive_models = [
            {
                "model": f"model-{index}",
                "requests": 1,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
            }
            for index in range(MAX_MODEL_ROWS + 1)
        ]
        malformed_phase_cases = (
            {key: value for key, value in base_usage.items() if key != "aiCredits"},
            {key: value for key, value in base_usage.items() if key != "models"},
            {**base_usage, "phases": {name: dict(value) for name, value in base_phases.items() if name != "research"}},
            {**base_usage, "phases": {**base_phases, "extra": dict(base_phases["planning"])}},
            {**base_usage, "phases": {**base_phases, "planning": {**base_phases["planning"], "toolCalls": -1}}},
            {**base_usage, "phases": {**base_phases, "planning": {**base_phases["planning"], "toolCalls": "one"}}},
            {**base_usage, "phases": {**base_phases, "planning": {**base_phases["planning"], "toolCalls": 1.5}}},
            {**base_usage, "aiCostUsd": "0.01"},
            {
                **base_usage,
                "aiCredits": 0,
                "aiCostUsd": 1e-12,
                "models": models(1, 100, 50, 10, 0, 0, 1e-12),
            },
            {**base_usage, "activeSeconds": True},
            {**base_usage, "activityDensity": "0.5"},
            {**base_usage, "chatSpans": -1},
            {**base_usage, "inputTokens": 1.5},
            {**base_usage, "cacheReadTokens": 101},
            {**base_usage, "aiCostUsd": -0.01},
            {**base_usage, "activeSeconds": float("inf")},
            {**base_usage, "activityDensity": -0.1},
            {**base_usage, "activityDensity": 1.1},
            {**base_usage, "activeSeconds": 10, "activityDensity": 0.9},
            {**base_usage, "phases": non_proportional_phases},
            {**base_usage, "phases": excessive_phase_counters},
            {**base_usage, "phases": orphaned_phase_tokens},
            {**base_usage, "phases": orphaned_tool_runtime},
            {**base_usage, "phases": orphaned_phase_time},
            {
                **base_usage,
                "chatSpans": MAX_MODEL_ROWS + 1,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
                "models": excessive_models,
            },
            {**base_usage, "models": models(2, 100, 50, 10, 0, 1, 0.01)},
            {
                **base_usage,
                "models": [
                    {
                        "model": "active-model",
                        "requests": 1,
                        "inputTokens": 99,
                        "cacheReadTokens": 49,
                        "uncachedInputTokens": 50,
                        "outputTokens": 10,
                        "reasoningTokens": 0,
                        "aiCredits": 1,
                        "aiCostUsd": 0.01,
                    },
                    {
                        "model": "zero-request-model",
                        "requests": 0,
                        "inputTokens": 1,
                        "cacheReadTokens": 1,
                        "uncachedInputTokens": 0,
                        "outputTokens": 0,
                        "reasoningTokens": 0,
                        "aiCredits": 0,
                        "aiCostUsd": 0,
                    },
                ],
            },
            {
                **base_usage,
                "aiCostUsd": 0.02,
                "models": models(1, 100, 50, 10, 0, 1, 0.02),
            },
            {**base_usage, "phases": {**base_phases, "planning": {**base_phases["planning"], "toolCalls": 10**400}}},
            {
                **base_usage,
                "phases": {
                    **base_phases,
                    "planning": {**base_phases["planning"], "toolCalls": 10**308},
                    "research": {**base_phases["research"], "toolCalls": 10**308},
                },
            },
            {
                **base_usage,
                "inputTokens": 10**400,
                "uncachedInputTokens": 10**400 - base_usage["cacheReadTokens"],
            },
            {
                **base_usage,
                "outputTokens": 10**308,
                "reasoningTokens": 10**308,
            },
            {
                **base_usage,
                "chatSpans": 0,
            },
            {
                **base_usage,
                "chatSpans": 0,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 1,
                "aiCostUsd": 0,
                "models": [],
            },
            {
                **base_usage,
                "chatSpans": 0,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
                "models": models(1, 0, 0, 0, 0, 0, 0),
            },
            {
                **base_usage,
                "chatSpans": 0,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 1,
                "aiCostUsd": 0,
                "models": models(0, 0, 0, 0, 0, 1, 0),
            },
            {
                **base_usage,
                "source": "copilot_turn_log",
                "chatSpans": 0,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
                "models": [],
                "phases": phases(active_seconds=30, engaged_seconds=30),
            },
            {
                **base_usage,
                "chatSpans": 0,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
                "models": models(0, 0, 0, 0, 0, 0, 0),
                "phases": phases(
                    coding=1, active_seconds=30, engaged_seconds=30, model_spans=0,
                ),
            },
        )

        for index, usage in enumerate((
            incomplete, invalid, *missing_usage_cases, *missing_phase_cases,
            *malformed_phase_cases,
        )):
            session = {
                "experiment": f"session-{index}",
                "completedAt": "2026-08-20T13:00:00Z",
                "durationSeconds": 60,
                "usage": usage,
            }
            insights = build_insights([session], [], 30, now=NOW)
            self.assertFalse(insights["evidenceHealth"]["integrityPassed"])
            self.assertTrue(insights["evidenceHealth"]["degraded"])
            self.assertEqual(insights["evidenceHealth"]["completeSessions"], 0)
            for metric in insights["metrics"]:
                if metric["key"] == "session_usage_coverage":
                    self.assertEqual(metric["current"], 0)
                else:
                    self.assertIsNone(metric["current"], metric["key"])

    def test_invalid_completion_timestamp_fails_closed(self) -> None:
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
                "models": models(1, 100, 50, 10, 0, 1, 0.01),
                "elapsedSeconds": 60,
                "engagedSeconds": 30,
                "activeSeconds": 30,
                "activityDensity": 0.5,
                "phases": phases(active_seconds=30, engaged_seconds=30),
        }
        for completed_at in (
            "not-a-time",
            "0001-01-01T00:00:00+23:59",
            "9999-12-31T23:59:59-23:59",
            "2026-02-29T12:00:00Z",
            "2026-08-20T24:00:00Z",
        ):
            with self.subTest(completed_at=completed_at):
                session = {
                    "experiment": "invalid-time",
                    "completedAt": completed_at,
                    "durationSeconds": 60,
                    "usage": usage,
                }
                insights = build_insights([session], [], 30, now=NOW)

                self.assertEqual(insights["evidenceHealth"]["eligibleSessions"], 1)
                self.assertEqual(insights["evidenceHealth"]["completeSessions"], 0)
                self.assertTrue(insights["evidenceHealth"]["degraded"])

    def test_unsettled_sessions_do_not_fail_integrity(self) -> None:
        sessions = [{
            "experiment": "resource-session",
            "completedAt": "2026-08-20T23:50:00Z",
            "durationSeconds": 60,
            "usage": {
                "source": "otel_traces",
                "chatSpans": 1,
                "inputTokens": 1,
                "cacheReadTokens": 0,
                "outputTokens": 1,
                "reasoningTokens": 0,
                "uncachedInputTokens": 1,
                "aiCredits": 1,
                "aiCostUsd": 0.01,
                "models": models(1, 1, 0, 1, 0, 1, 0.01),
                "elapsedSeconds": 60,
                "engagedSeconds": 1,
                "activeSeconds": 1,
                "activityDensity": 1,
                "phases": phases(active_seconds=1, engaged_seconds=1),
            },
        }]
        insights = build_insights(sessions, [], 30, now=NOW)

        self.assertEqual(insights["evidenceHealth"]["eligibleSessions"], 0)
        self.assertFalse(insights["evidenceHealth"]["integrityPassed"])
        self.assertFalse(insights["evidenceHealth"]["degraded"])
        self.assertIsNone(insights["evidenceHealth"]["message"])

    def test_far_future_completion_cannot_evade_the_integrity_denominator(self) -> None:
        insights = build_insights([{
            "experiment": "future-corrupt",
            "completedAt": "2026-08-22T00:00:00Z",
            "durationSeconds": 60,
            "usage": {},
        }], [], 30, now=NOW)

        self.assertEqual(insights["evidenceHealth"]["eligibleSessions"], 1)
        self.assertEqual(insights["evidenceHealth"]["completeSessions"], 0)
        self.assertTrue(insights["evidenceHealth"]["degraded"])
        self.assertFalse(insights["evidenceHealth"]["integrityPassed"])

    def test_otel_tool_only_session_is_complete_without_request_metrics(self) -> None:
        session = {
            "experiment": "tool-only",
            "completedAt": "2026-08-20T13:00:00Z",
            "durationSeconds": 60,
            "usage": {
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
                "elapsedSeconds": 60,
                "engagedSeconds": 30,
                "activeSeconds": 30,
                "activityDensity": 0.5,
                "phases": phases(
                    coding=1, validation=1, active_seconds=30, engaged_seconds=30,
                    model_spans=0,
                ),
            },
        }

        insights = build_insights([session], [], 30, now=NOW)
        by_key = {metric["key"]: metric for metric in insights["metrics"]}

        self.assertTrue(insights["evidenceHealth"]["integrityPassed"])
        self.assertEqual(by_key["model_requests"]["current"], 0)
        self.assertIsNone(by_key["tool_calls"]["current"])
        self.assertEqual(by_key["validation_coverage"]["current"], 1)

    def test_direct_and_otel_sources_produce_identical_metrics(self) -> None:
        usage = {
            "chatSpans": 4,
            "inputTokens": 400,
            "cacheReadTokens": 200,
            "uncachedInputTokens": 200,
            "outputTokens": 80,
            "reasoningTokens": 20,
            "aiCredits": 20,
            "aiCostUsd": 0.2,
            "models": models(4, 400, 200, 80, 20, 20, 0.2),
            "elapsedSeconds": 100,
            "engagedSeconds": 50,
            "activeSeconds": 50,
            "activityDensity": 0.5,
            "phases": phases(
                coding=2, validation=1, active_seconds=50, engaged_seconds=50,
            ),
        }
        results = []
        for source in ("otel_traces", "copilot_turn_log"):
            session = {
                "experiment": source,
                "completedAt": "2026-08-20T13:00:00Z",
                "durationSeconds": 100,
                "usage": {"source": source, **usage},
            }
            results.append(build_insights([session], [], 30, now=NOW))

        self.assertEqual(results[0]["metrics"], results[1]["metrics"])
        self.assertEqual(results[0]["evidenceHealth"]["otelUsageSessions"], 1)
        self.assertEqual(results[1]["evidenceHealth"]["directUsageSessions"], 1)

    def test_latest_five_session_median_uses_absolute_completion_time(self) -> None:
        sessions = []
        observations = (
            (0, "2026-08-11T08:00:00Z"),
            (100, "2026-08-10T23:00:00-10:00"),
            (10, "2026-08-12T13:00:00Z"),
            (20, "2026-08-13T13:00:00Z"),
            (30, "2026-08-14T13:00:00Z"),
            (40, "2026-08-15T13:00:00Z"),
        )
        for index, (output_per_request, completed_at) in enumerate(observations):
            sessions.append({
                "experiment": f"session-{index}",
                "completedAt": completed_at,
                "durationSeconds": 100,
                "usage": {
                    "source": "otel_traces",
                    "chatSpans": 2,
                    "inputTokens": 200,
                    "cacheReadTokens": 100,
                    "uncachedInputTokens": 100,
                    "outputTokens": output_per_request * 2,
                    "reasoningTokens": 0,
                    "aiCredits": 10,
                    "aiCostUsd": 0.1,
                    "models": models(2, 200, 100, output_per_request * 2, 0, 10, 0.1),
                    "elapsedSeconds": 100,
                    "engagedSeconds": 50,
                    "activeSeconds": 50,
                    "activityDensity": 0.5,
                    "phases": phases(active_seconds=50, engaged_seconds=50),
                },
            })

        insights = build_insights(sessions, [], 30, now=NOW)
        output = next(metric for metric in insights["metrics"] if metric["key"] == "output_tokens")

        self.assertEqual(output["aggregateSize"], 6)
        self.assertEqual(output["current"], 30)

    def test_even_session_median_remains_finite_for_finite_counters(self) -> None:
        requests = 10**308
        sessions = [{
            "experiment": f"session-{index}",
            "completedAt": f"2026-08-{10 + index:02d}T13:00:00Z",
            "durationSeconds": 100,
            "usage": {
                "source": "otel_traces",
                "chatSpans": requests,
                "inputTokens": 0,
                "cacheReadTokens": 0,
                "uncachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningTokens": 0,
                "aiCredits": 0,
                "aiCostUsd": 0,
                "models": models(requests, 0, 0, 0, 0, 0, 0),
                "elapsedSeconds": 100,
                "engagedSeconds": 50,
                "activeSeconds": 50,
                "activityDensity": 0.5,
                "phases": phases(active_seconds=50, engaged_seconds=50),
            },
        } for index in range(2)]

        insights = build_insights(sessions, [], 30, now=NOW)
        request_metric = next(
            metric for metric in insights["metrics"] if metric["key"] == "model_requests"
        )

        self.assertTrue(isfinite(request_metric["current"]))
        self.assertEqual(request_metric["current"], float(requests))

    def test_empty_window_has_neutral_usage_coverage(self) -> None:
        insights = build_insights([], [], 30, now=NOW)

        self.assertEqual(insights["evidenceHealth"]["eligibleSessions"], 0)
        self.assertIsNone(insights["evidenceHealth"]["sessionUsageCoverage"])
        self.assertFalse(insights["evidenceHealth"]["degraded"])


if __name__ == "__main__":
    unittest.main()
