from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.app.indexing import enqueue_session, initialize_indexing
from backend.app.runtime_telemetry import (
    CONTAINER_TOOL_NAMES,
    MODE_ATTRIBUTE,
    SESSION_KEY_ATTRIBUTE,
    SURFACE_ATTRIBUTE,
    is_runtime_resource,
    resource_mode,
    resource_surface,
    span_operation,
    stamped_resource,
    tool_name,
)
from backend.app.session_identity import public_session_id
from backend.app.store import (
    RUNTIME_CAPTURE_GENERATION,
    RUNTIME_MIGRATION_KEY,
    SPAN_SESSION_RETENTION_SECONDS,
    ValueStore,
)
from backend.app.trace_archive import (
    archive_paths,
    archive_signature,
    archive_snapshot,
    attributes,
    collect_trace_sessions,
    read_json_lines,
)
from backend.tests.runtime_otlp import (
    agent_span,
    attribute,
    chat_span,
    load_runtime_fixture,
    record_span,
    runtime_payload,
    tool_span,
)


BASE_NANOS = 1_793_296_800_000_000_000
MILLISECOND = 1_000_000
RUNTIME_CONVERSATION = "conversation-1"

# Tables written by the last VS Code-era release, before runtime attribution columns existed.
VSCODE_ERA_SCHEMA = """
CREATE TABLE sessions (
    experiment TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
    status TEXT NOT NULL, duration_seconds REAL NOT NULL, ai_cost_usd REAL NOT NULL,
    ai_credits REAL NOT NULL, chat_spans INTEGER NOT NULL, input_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, uncached_input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL,
    retained_source_characters INTEGER NOT NULL, source_evidence_complete INTEGER NOT NULL,
    usage_json TEXT NOT NULL, source_json TEXT NOT NULL, benchmark_json TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE prompts (
    prompt_id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL REFERENCES sessions(experiment) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, started_at TEXT NOT NULL, content TEXT NOT NULL,
    captured_content_length INTEGER NOT NULL, model_requests INTEGER NOT NULL,
    tool_calls INTEGER NOT NULL, input_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL, ai_cost_usd REAL NOT NULL, models_json TEXT NOT NULL,
    ai_credits REAL NOT NULL DEFAULT 0, usage_source TEXT NOT NULL DEFAULT 'otel_trace',
    UNIQUE(experiment, ordinal)
);
CREATE TABLE session_time_index (
    experiment TEXT PRIMARY KEY REFERENCES sessions(experiment) ON DELETE CASCADE,
    completed_at_epoch REAL
);
CREATE TABLE otel_records (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT, record_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL, started_at_milliseconds INTEGER NOT NULL,
    record_json TEXT NOT NULL, ingested_at TEXT NOT NULL
);
CREATE TABLE store_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE indexing_conversations (
    session_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    PRIMARY KEY(session_id, conversation_id)
);
CREATE TABLE indexing_logs (conversation_id TEXT PRIMARY KEY, signature TEXT NOT NULL);
"""
LEGACY_EXPERIMENT = "session-20260801-120000-abcdef0123"
LEGACY_CURSOR = 41
CLEARED_TABLES = (
    "sessions", "prompts", "session_time_index", "otel_records", "otel_span_sessions",
    "indexing_work", "indexing_changes", "indexing_artifacts",
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def nanos_to_utc(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) // MILLISECOND / 1_000, timezone.utc)


class RuntimeTelemetryContractTests(unittest.TestCase):
    def test_runtime_resources_are_accepted_by_service_or_scope(self) -> None:
        self.assertTrue(is_runtime_resource({"service.name": "github-copilot"}, "custom.scope"))
        self.assertTrue(is_runtime_resource({"service.name": "custom-app"}, "github.copilot"))
        self.assertTrue(is_runtime_resource({}, "github.copilot"))
        self.assertFalse(is_runtime_resource({"service.name": "copilot-chat"}, "copilot-chat"))
        self.assertFalse(is_runtime_resource({}, None))

    def test_span_operation_prefers_the_genai_attribute_and_falls_back_to_the_name(self) -> None:
        cases = (
            ({"gen_ai.operation.name": "chat"}, "anything", "chat"),
            ({"gen_ai.operation.name": "execute_tool"}, None, "execute_tool"),
            ({"gen_ai.operation.name": "embeddings"}, "chat model-a", None),
            ({}, "invoke_agent", "invoke_agent"),
            ({}, "invoke_agent explore", "invoke_agent"),
            ({}, "chat model-a", "chat"),
            ({}, "execute_tool view", "execute_tool"),
            ({"gen_ai.operation.name": ""}, "execute_tool view", "execute_tool"),
            ({}, "chatty", None),
            ({}, "invoke_agents", None),
            ({}, "session.timing.model_resolution", None),
            ({}, None, None),
        )
        for span_attributes, name, expected in cases:
            with self.subTest(attributes=span_attributes, name=name):
                self.assertEqual(span_operation(span_attributes, name), expected)

    def test_tool_name_prefers_the_attribute_and_parses_the_span_name(self) -> None:
        self.assertEqual(tool_name({"gen_ai.tool.name": "view"}, "execute_tool other"), "view")
        self.assertEqual(tool_name({"gen_ai.tool.name": ""}, "execute_tool powershell pwsh.exe"), "powershell")
        self.assertIsNone(tool_name({}, "execute_tool"))
        self.assertIsNone(tool_name({}, "chat model-a"))
        self.assertIsNone(tool_name({}, None))
        self.assertEqual(CONTAINER_TOOL_NAMES, {"task", "read_agent", "write_agent", "list_agents"})

    def test_surface_marker_wins_over_namespace_and_defaults_to_cli(self) -> None:
        cases = (
            ({"algalon.surface": "sdk", "service.namespace": "vscode.agent-host"}, "sdk"),
            ({"algalon.surface": "app"}, "app"),
            ({"algalon.surface": "mixed", "service.namespace": "vscode.agent-host"}, "vscode"),
            ({"algalon.surface": 7}, "cli"),
            ({"service.namespace": "vscode.agent-host"}, "vscode"),
            ({"service.namespace": "custom.host"}, "other"),
            ({"service.namespace": ""}, "cli"),
            ({}, "cli"),
        )
        for resource_attributes, expected in cases:
            with self.subTest(resource=resource_attributes):
                self.assertEqual(resource_surface(resource_attributes), expected)

    def test_only_an_explicit_automated_marker_excludes_a_session(self) -> None:
        self.assertEqual(resource_mode({MODE_ATTRIBUTE: "automated"}), "automated")
        self.assertEqual(resource_mode({MODE_ATTRIBUTE: "Automated"}), "interactive")
        self.assertEqual(resource_mode({MODE_ATTRIBUTE: "batch"}), "interactive")
        self.assertEqual(resource_mode({}), "interactive")

    def test_stamped_resource_replaces_client_supplied_stamps(self) -> None:
        resource = {
            "droppedAttributesCount": 2,
            "attributes": [
                attribute("service.name", "github-copilot"),
                attribute(SESSION_KEY_ATTRIBUTE, "spoofed"),
                attribute(SURFACE_ATTRIBUTE, "app"),
                "not-an-attribute",
            ],
        }

        stamped = stamped_resource(resource, "conversation-1", "cli", "interactive")

        self.assertEqual(stamped["droppedAttributesCount"], 2)
        self.assertEqual(stamped["attributes"], [
            attribute("service.name", "github-copilot"),
            attribute(SESSION_KEY_ATTRIBUTE, RUNTIME_CONVERSATION),
            attribute(SURFACE_ATTRIBUTE, "cli"),
            attribute(MODE_ATTRIBUTE, "interactive"),
        ])
        self.assertEqual(len(resource["attributes"]), 4)
        self.assertEqual(stamped_resource(None, "key", "sdk", "automated"), {"attributes": [
            attribute(SESSION_KEY_ATTRIBUTE, "key"),
            attribute(SURFACE_ATTRIBUTE, "sdk"),
            attribute(MODE_ATTRIBUTE, "automated"),
        ]})


class RuntimeFixtureIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sessions = self.root / "value" / "sessions"
        config = self.root / "value-model.local.json"
        config.write_text(json.dumps({"promptStorage": {"retentionDays": 3_650}}), encoding="utf-8")
        self.store = ValueStore(
            self.root / "app" / "value.db", self.sessions, self.root / "otel" / "traces.json", config,
        )
        self.fixture = load_runtime_fixture()
        self.expected = self.fixture["expected"]["sessions"]

    def ingest(self) -> list[dict[str, int]]:
        return [self.store.ingest_otlp_traces(payload) for payload in self.fixture["payloads"]]

    def inbox(self) -> dict[str, list[tuple[dict, dict, dict]]]:
        page = self.store.otel_records(limit=10_000)
        self.assertFalse(page["hasMore"])
        grouped: dict[str, list[tuple[dict, dict, dict]]] = defaultdict(list)
        for record in page["records"]:
            resource, scope_span, span = record_span(record)
            stamps = attributes(resource["attributes"])
            grouped[stamps[SESSION_KEY_ATTRIBUTE]].append((stamps, scope_span, span))
        return grouped

    def fixture_spans(self):
        for payload in self.fixture["payloads"]:
            for resource_span in payload["resourceSpans"]:
                resource = attributes(resource_span["resource"]["attributes"])
                for scope_span in resource_span["scopeSpans"]:
                    for span in scope_span["spans"]:
                        yield resource, scope_span, span

    def test_fixture_accepts_runtime_spans_and_drops_everything_else(self) -> None:
        results = self.ingest()

        accepted = sum(result["accepted"] for result in results)
        dropped = sum(result["dropped"] for result in results)
        self.assertEqual(accepted, self.fixture["expected"]["acceptedSpans"])
        self.assertEqual(dropped, self.fixture["expected"]["droppedSpans"])
        self.assertEqual(sum(result["received"] for result in results), accepted + dropped)
        self.assertEqual(len(self.store.otel_records(limit=10_000)["records"]), accepted)

        replay = self.ingest()

        self.assertEqual(sum(result["accepted"] for result in replay), 0)
        self.assertEqual(sum(result["dropped"] for result in replay), dropped)
        self.assertEqual(len(self.store.otel_records(limit=10_000)["records"]), accepted)

    def test_fixture_records_carry_session_surface_and_mode_stamps(self) -> None:
        self.ingest()
        grouped = self.inbox()
        original_scopes = {
            (span["traceId"], span["spanId"]): scope_span["scope"]
            for _resource, scope_span, span in self.fixture_spans()
        }

        self.assertEqual(set(grouped), set(self.expected))
        for session, entries in grouped.items():
            expected = self.expected[session]
            with self.subTest(session=session):
                operations = Counter()
                for stamps, scope_span, span in entries:
                    self.assertEqual(stamps[SURFACE_ATTRIBUTE], expected["surface"])
                    self.assertEqual(stamps[MODE_ATTRIBUTE], expected["mode"])
                    self.assertEqual(stamps["service.name"], "github-copilot")
                    self.assertEqual(scope_span["scope"], original_scopes[(span["traceId"], span["spanId"])])
                    operations[span_operation(attributes(span["attributes"]), span["name"])] += 1
                self.assertEqual(operations, Counter({
                    "chat": expected["chatSpans"],
                    "execute_tool": expected["toolSpans"],
                    "invoke_agent": expected["agentSpans"],
                }))
                self.assertEqual(
                    min(nanos_to_utc(span["startTimeUnixNano"]) for *_, span in entries),
                    utc(expected["startedAt"]),
                )
                self.assertEqual(
                    max(nanos_to_utc(span["endTimeUnixNano"]) for *_, span in entries),
                    utc(expected["endedAt"]),
                )

    def test_agent_host_tool_span_without_conversation_joins_its_session(self) -> None:
        orphans = [
            span for resource, _scope_span, span in self.fixture_spans()
            if resource.get("service.namespace") == "vscode.agent-host"
            and "gen_ai.conversation.id" not in attributes(span["attributes"])
        ]
        self.assertEqual(len(orphans), 1)
        orphan = orphans[0]
        vscode_session = next(
            session for session, expected in self.expected.items() if expected["surface"] == "vscode"
        )

        self.ingest()

        joined = [
            (session, span) for session, entries in self.inbox().items()
            for *_, span in entries
            if (span["traceId"], span["spanId"]) == (orphan["traceId"], orphan["spanId"])
        ]
        self.assertEqual([session for session, _span in joined], [vscode_session])
        self.assertEqual(joined[0][1], orphan)
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute(
                "SELECT session_id FROM otel_span_sessions WHERE trace_id = ? AND span_id = ?",
                (orphan["traceId"], orphan["spanId"]),
            ).fetchone()[0], vscode_session)

    def test_fixture_prompts_and_totals_match_the_expected_sessions(self) -> None:
        self.ingest()
        cursor = self.store.otel_records(limit=10_000)["nextCursor"]
        self.sessions.mkdir(parents=True)
        experiments = {}
        for session, expected in self.expected.items():
            experiment = public_session_id(session, int(utc(expected["startedAt"]).timestamp() * 1_000))
            experiments[session] = experiment
            credits = expected["nanoAiu"] / 1_000_000_000
            (self.sessions / f"{experiment}.json").write_text(json.dumps({
                "experiment": experiment,
                "startedAt": expected["startedAt"],
                "completedAt": expected["endedAt"],
                "status": "published",
                "inboxCursor": cursor,
                "surface": expected["surface"],
                "sessionMode": expected["mode"],
                **({"exclusionReason": "automated_session"} if expected["mode"] == "automated" else {}),
                "usage": {
                    "source": "otel_traces",
                    "chatSpans": expected["chatSpans"],
                    "aiCredits": credits,
                    "aiCostUsd": credits / 100,
                },
                "source": {},
                "benchmark": None,
            }), encoding="utf-8")

        self.store.index_once()

        self.assertEqual(self.store.indexing_status()["state"], "current")
        grouped = self.inbox()
        for session, expected in self.expected.items():
            with self.subTest(session=session):
                prompts = self.store.prompts(experiments[session])
                self.assertEqual(len(prompts), expected["prompts"])
                container_tools = sum(
                    1 for *_, span in grouped[session]
                    if span_operation(attributes(span["attributes"]), span["name"]) == "execute_tool"
                    and tool_name(attributes(span["attributes"]), span["name"]) in CONTAINER_TOOL_NAMES
                )
                self.assertEqual({
                    field: sum(prompt[field] for prompt in prompts)
                    for field in (
                        "modelRequests", "toolCalls", "inputTokens", "cacheReadTokens",
                        "outputTokens", "reasoningTokens",
                    )
                }, {
                    "modelRequests": expected["chatSpans"],
                    "toolCalls": expected["toolSpans"] - container_tools,
                    "inputTokens": expected["inputTokens"],
                    "cacheReadTokens": expected["cacheReadTokens"],
                    "outputTokens": expected["outputTokens"],
                    "reasoningTokens": expected["reasoningTokens"],
                })
                self.assertEqual(
                    sum(round(prompt["aiCredits"] * 1_000_000_000) for prompt in prompts),
                    expected["nanoAiu"],
                )
                for prompt in prompts:
                    self.assertEqual(prompt["usageSource"], "otel_trace")
                    self.assertEqual(prompt["content"], "")
                    self.assertFalse(prompt["contentAvailable"])
                    self.assertAlmostEqual(prompt["aiCostUsd"], prompt["aiCredits"] / 100)
                payload = self.store.session(experiments[session])
                self.assertEqual(
                    (payload["surface"], payload["sessionMode"], payload["promptCount"]),
                    (expected["surface"], expected["mode"], expected["prompts"]),
                )
        self.assertEqual(sum(
            1 for *_, span in grouped["cli-session-0001"]
            if tool_name(attributes(span["attributes"]), span["name"]) in CONTAINER_TOOL_NAMES
        ), 1)

        totals = self.store.overview("base", 3_650)["totals"]
        interactive = [value for value in self.expected.values() if value["mode"] == "interactive"]
        automated = [value for value in self.expected.values() if value["mode"] == "automated"]
        self.assertEqual(totals["sessions"], len(interactive))
        self.assertEqual(totals["prompts"], sum(value["prompts"] for value in interactive))
        self.assertAlmostEqual(totals["aiCostUsd"], sum(value["nanoAiu"] for value in interactive) / 1e11)
        self.assertEqual(totals["automatedSessions"], len(automated))
        self.assertAlmostEqual(
            totals["automatedAiCostUsd"], sum(value["nanoAiu"] for value in automated) / 1e11,
        )


class RuntimeSpanResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "value-model.local.json"
        config.write_text("{}\n", encoding="utf-8")
        self.store = ValueStore(root / "app" / "value.db", root / "sessions", root / "traces.json", config)

    def stored_sessions(self) -> dict[tuple[str, str], str]:
        result = {}
        for record in self.store.otel_records(limit=10_000)["records"]:
            resource, _scope_span, span = record_span(record)
            result[(span.get("traceId", ""), span.get("spanId", ""))] = attributes(
                resource["attributes"]
            )[SESSION_KEY_ATTRIBUTE]
        return result

    def test_late_spans_without_conversation_join_through_persisted_span_mappings(self) -> None:
        first = runtime_payload([chat_span(
            trace_id="trace-1", span_id="chat", parent_span_id="root",
            conversation="conversation-1", start_ns=BASE_NANOS + MILLISECOND,
        )])
        late_root = runtime_payload([agent_span(trace_id="trace-1", span_id="root", start_ns=BASE_NANOS)])
        late_child = runtime_payload([tool_span(
            "edit", trace_id="trace-1", span_id="tool", parent_span_id="chat",
            start_ns=BASE_NANOS + 2 * MILLISECOND,
        )])
        unknown_parent = runtime_payload([tool_span(
            "view", trace_id="trace-1", span_id="tool-2", parent_span_id="unrecorded",
            start_ns=BASE_NANOS + 3 * MILLISECOND,
        )])

        for payload in (first, late_root, late_child, unknown_parent):
            self.assertEqual(
                self.store.ingest_otlp_traces(payload), {"received": 1, "accepted": 1, "dropped": 0},
            )

        self.assertEqual(self.stored_sessions(), {
            ("trace-1", span): "conversation-1" for span in ("chat", "root", "tool", "tool-2")
        })
        with closing(self.store._connect()) as connection:
            work = connection.execute("SELECT session_id, latest_cursor FROM indexing_work").fetchall()
        self.assertEqual([row["session_id"] for row in work], ["conversation-1"])
        self.assertEqual(work[0]["latest_cursor"], self.store.otel_records()["nextCursor"])

    def test_unattributable_spans_are_dropped(self) -> None:
        ambiguous = runtime_payload([
            chat_span(trace_id="shared", span_id="a", conversation="conversation-a", start_ns=BASE_NANOS),
            chat_span(trace_id="shared", span_id="b", conversation="conversation-b", start_ns=BASE_NANOS),
            tool_span("view", trace_id="shared", span_id="c", parent_span_id="elsewhere", start_ns=BASE_NANOS),
        ])
        traceless = runtime_payload([tool_span("view", trace_id="", span_id="d", start_ns=BASE_NANOS)])
        unknown_trace = runtime_payload([tool_span("view", trace_id="unknown", span_id="e", start_ns=BASE_NANOS)])

        self.assertEqual(self.store.ingest_otlp_traces(ambiguous), {"received": 3, "accepted": 2, "dropped": 1})
        self.assertEqual(self.store.ingest_otlp_traces(traceless), {"received": 1, "accepted": 0, "dropped": 1})
        self.assertEqual(self.store.ingest_otlp_traces(unknown_trace), {"received": 1, "accepted": 0, "dropped": 1})
        self.assertEqual(self.stored_sessions(), {
            ("shared", "a"): "conversation-a", ("shared", "b"): "conversation-b",
        })

    def test_spans_without_trace_identity_deduplicate_by_session_name_and_start(self) -> None:
        span = chat_span(trace_id="", span_id="", conversation="conversation-1", start_ns=BASE_NANOS)
        payload = runtime_payload([span])

        self.assertEqual(self.store.ingest_otlp_traces(payload)["accepted"], 1)
        self.assertEqual(self.store.ingest_otlp_traces(payload)["accepted"], 0)
        moved = runtime_payload([{**span, "startTimeUnixNano": str(BASE_NANOS + MILLISECOND)}])
        self.assertEqual(self.store.ingest_otlp_traces(moved)["accepted"], 1)
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM otel_span_sessions").fetchone()[0], 0)

    def test_spans_ending_before_they_start_are_dropped_like_the_worker_drops_them(self) -> None:
        backwards = chat_span(
            trace_id="trace-1", span_id="backwards", conversation="conversation-1",
            start_ns=BASE_NANOS + MILLISECOND, end_ns=BASE_NANOS,
        )
        zero_end = chat_span(
            trace_id="trace-1", span_id="zero-end", conversation="conversation-1",
            start_ns=BASE_NANOS, end_ns=0,
        )
        open_ended = chat_span(trace_id="trace-1", span_id="open", conversation="conversation-1", start_ns=BASE_NANOS)
        del open_ended["endTimeUnixNano"]
        instantaneous = chat_span(
            trace_id="trace-1", span_id="instant", conversation="conversation-1",
            start_ns=BASE_NANOS, end_ns=BASE_NANOS,
        )

        self.assertEqual(
            self.store.ingest_otlp_traces(runtime_payload([backwards, zero_end, open_ended, instantaneous])),
            {"received": 4, "accepted": 2, "dropped": 2},
        )
        self.assertEqual(set(self.stored_sessions()), {("trace-1", "open"), ("trace-1", "instant")})

    def test_non_runtime_and_non_genai_spans_never_enter_the_inbox(self) -> None:
        def span(name: str, start: object = BASE_NANOS, operation: str | None = None) -> dict:
            span_attributes = [attribute("gen_ai.conversation.id", "conversation-1")]
            if operation:
                span_attributes.append(attribute("gen_ai.operation.name", operation))
            return {
                "traceId": "trace-1", "spanId": f"{name}-{start}", "name": name,
                "startTimeUnixNano": start, "attributes": span_attributes,
            }

        legacy = runtime_payload(
            [span("chat model-a", operation="chat")], service_name="copilot-chat", scope="copilot-chat",
        )
        rejected = runtime_payload([
            span("session.timing.model_resolution"),
            span("chat embeddings", operation="embeddings"),
            span("chat model-a", start="0", operation="chat"),
            span("chat model-a", start="not-a-time", operation="chat"),
            {**span("chat model-a", operation="chat"), "startTimeUnixNano": None},
        ])
        name_only = runtime_payload([span("execute_tool view")])
        scope_only = {"resourceSpans": [{
            "resource": "not-a-resource",
            "scopeSpans": [{"scope": {"name": "github.copilot"}, "spans": [span("invoke_agent")]}],
        }]}

        self.assertEqual(self.store.ingest_otlp_traces(legacy), {"received": 1, "accepted": 0, "dropped": 1})
        self.assertEqual(self.store.ingest_otlp_traces(rejected), {"received": 5, "accepted": 0, "dropped": 5})
        self.assertEqual(self.store.ingest_otlp_traces(name_only), {"received": 1, "accepted": 1, "dropped": 0})
        self.assertEqual(self.store.ingest_otlp_traces(scope_only), {"received": 1, "accepted": 1, "dropped": 0})
        records = self.store.otel_records()["records"]
        self.assertEqual([record_span(record)[2]["name"] for record in records], ["execute_tool view", "invoke_agent"])
        self.assertEqual(record_span(records[1])[0], {"attributes": [
            attribute(SESSION_KEY_ATTRIBUTE, RUNTIME_CONVERSATION),
            attribute(SURFACE_ATTRIBUTE, "cli"),
            attribute(MODE_ATTRIBUTE, "interactive"),
        ]})

    def test_malformed_payload_structure_is_rejected_or_ignored(self) -> None:
        for payload in (None, [], {}, {"resourceSpans": "not-a-list"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.ingest_otlp_traces(payload)
        ignored = {"resourceSpans": [
            "not-a-resource-span",
            {"resource": {"attributes": "not-a-list"}, "scopeSpans": "not-a-list"},
            {"resource": {"attributes": [
                attribute("service.name", "github-copilot"),
                {"key": "missing-value"},
                {"key": "not-a-dict-value", "value": "github-copilot"},
                "not-an-attribute",
            ]}, "scopeSpans": [
                "not-a-scope-span",
                {"spans": "not-a-list"},
                {"spans": ["not-a-span"]},
            ]},
        ]}

        self.assertEqual(self.store.ingest_otlp_traces(ignored), {"received": 0, "accepted": 0, "dropped": 0})
        self.assertEqual(self.store.otel_records()["records"], [])

    def test_inbox_records_keep_one_span_with_its_schema_and_replace_client_stamps(self) -> None:
        payload = runtime_payload(
            [
                chat_span(trace_id="trace-1", span_id="one", conversation="conversation-1", start_ns=BASE_NANOS),
                chat_span(trace_id="trace-1", span_id="two", conversation="conversation-1", start_ns=BASE_NANOS),
            ],
            resource_attributes=[
                attribute("service.namespace", "custom.host"),
                attribute(SESSION_KEY_ATTRIBUTE, "spoofed-session"),
                attribute(SURFACE_ATTRIBUTE, "app"),
                attribute(MODE_ATTRIBUTE, "automated"),
            ],
        )
        payload["resourceSpans"][0]["schemaUrl"] = "https://opentelemetry.io/schemas/1.26.0"
        payload["resourceSpans"][0]["scopeSpans"][0]["schemaUrl"] = "https://opentelemetry.io/schemas/1.25.0"

        self.store.ingest_otlp_traces(payload)

        records = self.store.otel_records()["records"]
        self.assertEqual(len(records), 2)
        for record, span_id in zip(records, ("one", "two")):
            self.assertEqual(record["resourceSpans"][0]["schemaUrl"], "https://opentelemetry.io/schemas/1.26.0")
            resource, scope_span, span = record_span(record)
            self.assertEqual(span["spanId"], span_id)
            self.assertEqual(scope_span["schemaUrl"], "https://opentelemetry.io/schemas/1.25.0")
            self.assertEqual(scope_span["scope"], {"name": "github.copilot"})
            keys = [item["key"] for item in resource["attributes"]]
            self.assertEqual(keys.count(SESSION_KEY_ATTRIBUTE), 1)
            self.assertEqual(attributes(resource["attributes"]), {
                "service.name": "github-copilot",
                "service.namespace": "custom.host",
                SESSION_KEY_ATTRIBUTE: RUNTIME_CONVERSATION,
                SURFACE_ATTRIBUTE: "other",
                MODE_ATTRIBUTE: "automated",
            })

    def test_index_once_prunes_expired_span_session_mappings(self) -> None:
        self.store.ingest_otlp_traces(runtime_payload([chat_span(
            trace_id="trace-1", span_id="chat", parent_span_id="root",
            conversation="conversation-1", start_ns=BASE_NANOS,
        )]))
        with closing(self.store._connect()) as connection, connection:
            connection.execute(
                "UPDATE otel_span_sessions SET recorded_at = recorded_at - ? WHERE span_id = 'root'",
                (SPAN_SESSION_RETENTION_SECONDS + 60,),
            )

        self.store.index_once()

        with closing(self.store._connect()) as connection:
            remaining = [row[0] for row in connection.execute("SELECT span_id FROM otel_span_sessions")]
        self.assertEqual(remaining, ["chat"])


class RuntimeMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.use_root(Path(temporary.name))

    def use_root(self, root: Path) -> None:
        self.root = root
        self.database = root / "app" / "value.db"
        self.sessions = root / "value" / "sessions"
        self.trace_archive = root / "otel" / "traces.json"
        self.rotated_archive = root / "otel" / "traces-2026-08-05T10-01-00.json"
        self.config = root / "value-model.local.json"
        self.backups = root / "backups"
        root.mkdir(parents=True, exist_ok=True)
        self.config.write_text("{}\n", encoding="utf-8")

    def store(self) -> ValueStore:
        return ValueStore(self.database, self.sessions, self.trace_archive, self.config)

    def create_vscode_era_install(self, state: str | None = '{"version": 3}') -> dict[Path, str]:
        self.database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(VSCODE_ERA_SCHEMA)
            initialize_indexing(connection)
            connection.execute(
                """INSERT INTO sessions VALUES (?, '2026-08-01T12:00:00+00:00', '2026-08-01T12:30:00+00:00',
                'published', 1800, 0.5, 50, 3, 300, 100, 200, 30, 0, 0, 0,
                '{"source":"copilot_turn_log"}', '{}', NULL, '2026-08-01T12:31:00+00:00')""",
                (LEGACY_EXPERIMENT,),
            )
            connection.execute(
                """INSERT INTO prompts VALUES ('prompt-001-legacy', ?, 1, '2026-08-01T12:00:00+00:00',
                'Private VS Code prompt', 22, 3, 0, 300, 100, 30, 0, 0.5, '{"gpt-4o":3}', 50,
                'copilot_turn_log')""",
                (LEGACY_EXPERIMENT,),
            )
            connection.execute(
                "INSERT INTO otel_records VALUES (?, 'legacy-record', 'vscode-window-1', 1785585600000, "
                "'{\"resourceSpans\":[]}', '2026-08-01T12:00:01+00:00')",
                (LEGACY_CURSOR,),
            )
            enqueue_session(connection, "vscode-window-1", LEGACY_CURSOR)
            connection.execute(
                "INSERT INTO indexing_artifacts VALUES (?, ?, '[1,2]', '{}', NULL)",
                (f"{LEGACY_EXPERIMENT}.json", LEGACY_EXPERIMENT),
            )
            connection.execute("INSERT INTO indexing_conversations VALUES ('vscode-window-1', 'conversation-1')")
            connection.execute("INSERT INTO indexing_logs VALUES ('conversation-1', '[\"main.jsonl\",1,2]')")
            connection.executemany("INSERT OR REPLACE INTO store_metadata VALUES (?, ?)", (
                ("indexing_last_discovery", "1785585600"),
                ("indexing_discovery_error", "log_unavailable"),
                ("indexing_bootstrap_cursor", str(LEGACY_CURSOR)),
                ("legacy_trace_inbox_import_v1", "complete"),
            ))
        files = {
            self.sessions / f"{LEGACY_EXPERIMENT}.json": json.dumps({"experiment": LEGACY_EXPERIMENT}),
            self.trace_archive: '{"resourceSpans":[]}\n',
            self.rotated_archive: '{"resourceSpans":[]}\n{"resourceSpans":[]}\n',
        }
        if state is not None:
            files[self.sessions / "state.json"] = state
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (self.sessions / "worker-cache").mkdir()
        return files

    def assert_vscode_evidence_cleared(self, store: ValueStore) -> None:
        with closing(store._connect()) as connection:
            for table in CLEARED_TABLES:
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            metadata = {row[0] for row in connection.execute("SELECT key FROM store_metadata")}
        self.assertNotIn("indexing_conversations", tables)
        self.assertNotIn("indexing_logs", tables)
        self.assertEqual(metadata, {"indexing_queue_v1", RUNTIME_MIGRATION_KEY})

    def test_vscode_era_install_is_backed_up_then_cleared_once(self) -> None:
        files = self.create_vscode_era_install()

        store = self.store()

        status = store.runtime_migration_status()
        backup = Path(status["backupPath"])
        self.assertEqual(status, {"completed": True, "backupPath": str(backup), "clearedSessions": 1})
        self.assertEqual(backup.parent, self.backups)
        self.assertRegex(backup.name, r"^vscode-legacy-\d{8}T\d{6}Z$")
        self.assertEqual(
            sorted(path.relative_to(backup).as_posix() for path in backup.rglob("*")),
            sorted([
                "otel", "otel/traces-2026-08-05T10-01-00.json", "otel/traces.json", "sessions",
                f"sessions/{LEGACY_EXPERIMENT}.json", "sessions/state.json", "value.db",
            ]),
        )
        with closing(sqlite3.connect((backup / "value.db").as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("SELECT experiment FROM sessions").fetchall(), [(LEGACY_EXPERIMENT,)])
            self.assertEqual(connection.execute("SELECT content FROM prompts").fetchall(), [("Private VS Code prompt",)])
            self.assertEqual(connection.execute("SELECT cursor FROM otel_records").fetchall(), [(LEGACY_CURSOR,)])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchall(), [("ok",)])
        for path, content in files.items():
            folder = "sessions" if path.parent == self.sessions else "otel"
            self.assertEqual((backup / folder / path.name).read_text(encoding="utf-8"), content)
            self.assertFalse(path.exists(), path)
        self.assertTrue((self.sessions / "worker-cache").is_dir())
        self.assert_vscode_evidence_cleared(store)
        with closing(store._connect()) as connection:
            session_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({"surface", "session_mode", "exclusion_reason"} <= session_columns)
        store.index_once()
        self.assertEqual(store.indexing_status()["state"], "current")

        accepted = store.ingest_otlp_traces(runtime_payload([chat_span(
            trace_id="runtime-trace", span_id="runtime-span", conversation="runtime-session",
            start_ns=BASE_NANOS,
        )]))
        self.assertEqual(accepted["accepted"], 1)
        self.assertGreater(store.otel_records()["nextCursor"], LEGACY_CURSOR)

        runtime_artifact = self.sessions / "session-20261029-180000-0123456789.json"
        runtime_artifact.write_text('{"experiment": "session-20261029-180000-0123456789"}', encoding="utf-8")
        self.trace_archive.write_text('{"resourceSpans":[]}\n', encoding="utf-8")
        with closing(store._connect()) as connection:
            marker = connection.execute(
                "SELECT value FROM store_metadata WHERE key = ?", (RUNTIME_MIGRATION_KEY,)
            ).fetchone()[0]

        restarted = self.store()

        self.assertEqual(restarted.runtime_migration_status(), status)
        with closing(restarted._connect()) as connection:
            self.assertEqual(connection.execute(
                "SELECT value FROM store_metadata WHERE key = ?", (RUNTIME_MIGRATION_KEY,)
            ).fetchone()[0], marker)
        self.assertEqual(list(self.backups.iterdir()), [backup])
        self.assertTrue(runtime_artifact.exists())
        self.assertTrue(self.trace_archive.exists())
        self.assertEqual(len(restarted.otel_records()["records"]), 1)

    def test_only_pre_runtime_worker_state_is_retired(self) -> None:
        cases = (
            (json.dumps({"captureGeneration": RUNTIME_CAPTURE_GENERATION, "cursor": 9}), True),
            (json.dumps({"captureGeneration": "vscode-direct-turns"}), False),
            (json.dumps(["not", "an", "object"]), False),
            ("{not json", False),
        )
        base = self.root
        for index, (state, kept) in enumerate(cases):
            with self.subTest(state=state):
                self.use_root(base / f"case-{index}")
                self.create_vscode_era_install(state)
                state_path = self.sessions / "state.json"

                status = self.store().runtime_migration_status()

                backup = Path(status["backupPath"])
                self.assertEqual(state_path.exists(), kept)
                self.assertEqual((backup / "sessions" / "state.json").exists(), not kept)
                if kept:
                    self.assertEqual(state_path.read_text(encoding="utf-8"), state)
                self.assertFalse((self.sessions / f"{LEGACY_EXPERIMENT}.json").exists())

    def test_interrupted_migration_resumes_on_the_next_start(self) -> None:
        files = self.create_vscode_era_install()
        with (
            patch("backend.app.store.backup_database", side_effect=OSError("synthetic disk full")),
            self.assertRaises(OSError),
        ):
            self.store()
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
            self.assertIn("surface", {row[1] for row in connection.execute("PRAGMA table_info(sessions)")})
            self.assertIsNone(connection.execute(
                "SELECT value FROM store_metadata WHERE key = ?", (RUNTIME_MIGRATION_KEY,)
            ).fetchone())

        store = self.store()

        status = store.runtime_migration_status()
        self.assertEqual(status["clearedSessions"], 1)
        backup = Path(status["backupPath"])
        with closing(sqlite3.connect((backup / "value.db").as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("SELECT experiment FROM sessions").fetchall(), [(LEGACY_EXPERIMENT,)])
        for path in files:
            self.assertFalse(path.exists(), path)
        self.assert_vscode_evidence_cleared(store)

    def test_migration_interrupted_while_retiring_files_resumes_without_leftovers(self) -> None:
        files = self.create_vscode_era_install()
        original_unlink = Path.unlink
        retired: list[Path] = []

        def unlink_then_crash(path: Path, missing_ok: bool = False) -> None:
            original_unlink(path, missing_ok=missing_ok)
            if path in files:
                retired.append(path)
                raise OSError("synthetic crash while retiring legacy files")

        with patch.object(Path, "unlink", unlink_then_crash), self.assertRaises(OSError):
            self.store()
        self.assertEqual(len(retired), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT value FROM store_metadata WHERE key = ?", (RUNTIME_MIGRATION_KEY,)
            ).fetchone())

        store = self.store()

        for path in files:
            self.assertFalse(path.exists(), path)
        self.assert_vscode_evidence_cleared(store)
        backups = sorted(self.backups.iterdir())
        self.assertEqual(len(backups), 2)
        self.assertEqual(Path(store.runtime_migration_status()["backupPath"]), backups[-1])
        retained = {path.name for backup in backups for path in backup.rglob("*") if path.is_file()}
        self.assertTrue({path.name for path in files} <= retained)
        for backup in backups:
            with closing(sqlite3.connect((backup / "value.db").as_uri() + "?mode=ro", uri=True)) as connection:
                self.assertEqual(
                    connection.execute("SELECT experiment FROM sessions").fetchall(), [(LEGACY_EXPERIMENT,)]
                )

    def test_fresh_install_records_completion_without_touching_existing_files(self) -> None:
        artifact = self.sessions / "session-20261029-180000-0123456789.json"
        state = self.sessions / "state.json"
        for path, content in (
            (artifact, json.dumps({
                "experiment": "session-20261029-180000-0123456789",
                "startedAt": "2026-10-29T18:00:00+00:00",
                "completedAt": "2026-10-29T18:01:00+00:00",
                "status": "published",
                "usage": {},
                "source": {},
            })),
            (state, json.dumps({"version": 3})),
            (self.trace_archive, json.dumps(runtime_payload([chat_span(
                trace_id="archived", span_id="archived", conversation="archived", start_ns=BASE_NANOS,
            )])) + "\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        store = self.store()

        self.assertEqual(
            store.runtime_migration_status(),
            {"completed": True, "backupPath": None, "clearedSessions": 0},
        )
        self.assertFalse(self.backups.exists())
        for path in (artifact, state, self.trace_archive):
            self.assertTrue(path.exists(), path)
        store.index_once()
        self.assertEqual(store.session("session-20261029-180000-0123456789")["status"], "published")
        self.assertEqual(store.otel_records()["records"], [])

    def test_migration_status_tolerates_missing_or_malformed_markers(self) -> None:
        store = self.store()
        cases = (
            (None, {"completed": False, "backupPath": None, "clearedSessions": 0}),
            ("not-json", {"completed": True, "backupPath": None, "clearedSessions": 0}),
            ("[1, 2]", {"completed": True, "backupPath": None, "clearedSessions": 0}),
            (
                json.dumps({"backupPath": "/data/backups/x", "clearedSessions": -4}),
                {"completed": True, "backupPath": "/data/backups/x", "clearedSessions": 0},
            ),
            (
                json.dumps({"clearedSessions": True}),
                {"completed": True, "backupPath": None, "clearedSessions": 0},
            ),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                with closing(store._connect()) as connection, connection:
                    connection.execute("DELETE FROM store_metadata WHERE key = ?", (RUNTIME_MIGRATION_KEY,))
                    if value is not None:
                        connection.execute(
                            "INSERT INTO store_metadata VALUES (?, ?)", (RUNTIME_MIGRATION_KEY, value),
                        )
                self.assertEqual(store.runtime_migration_status(), expected)

        restarted = self.store()
        self.assertEqual(
            restarted.runtime_migration_status(),
            {"completed": True, "backupPath": None, "clearedSessions": 0},
        )


class TraceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_json_lines_tolerate_a_truncated_tail_and_optionally_skip_corruption(self) -> None:
        path = self.root / "traces.json"
        self.assertEqual(read_json_lines(path), [])
        path.write_text('{"a": 1}\n\n{"b": 2}\n{"c": ', encoding="utf-8")
        self.assertEqual(read_json_lines(path), [{"a": 1}, {"b": 2}])
        path.write_text('{"a": 1}\nnot-json\n{"b": 2}\n', encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            read_json_lines(path)
        self.assertEqual(read_json_lines(path, skip_invalid=True), [{"a": 1}, {"b": 2}])

    def test_archive_paths_list_rotated_files_before_the_live_archive(self) -> None:
        live = self.root / "traces.json"
        self.assertEqual(archive_paths(live), [])
        rotated = [self.root / "traces-2026-08-05T10-02-00.json", self.root / "traces-2026-08-05T10-01-00.json"]
        for path in (*rotated, live, self.root / "other.json"):
            path.write_text("{}\n", encoding="utf-8")
        (self.root / "traces-directory.json").mkdir()

        self.assertEqual(archive_paths(live), [*sorted(rotated), live])
        snapshot = archive_snapshot(live)
        self.assertEqual([entry[0] for entry in snapshot], [*sorted(rotated), live])
        self.assertEqual(
            archive_signature(snapshot),
            tuple((path.name, modified, size) for path, modified, size in snapshot),
        )
        with patch("backend.app.trace_archive.archive_paths", return_value=[self.root / "vanished.json", live]):
            self.assertEqual([entry[0] for entry in archive_snapshot(live)], [live])

    def test_trace_sessions_group_stamped_spans_once(self) -> None:
        def record(session: str | None, *spans: dict) -> dict:
            resource = [attribute("service.name", "github-copilot")]
            if session:
                resource.append(attribute(SESSION_KEY_ATTRIBUTE, session))
            return {"resourceSpans": [{"resource": {"attributes": resource}, "scopeSpans": [{"spans": list(spans)}]}]}

        first = chat_span(trace_id="trace-1", span_id="one", start_ns=BASE_NANOS + 5 * MILLISECOND)
        unnamed = {"traceId": "trace-2", "name": "chat model-a", "startTimeUnixNano": str(BASE_NANOS)}
        path = self.root / "traces.json"
        path.write_text("\n".join(json.dumps(item) for item in (
            record("session-1", first),
            record("session-1", first, {**first, "spanId": "zero", "startTimeUnixNano": "0"}),
            record("session-2", unnamed, unnamed),
            record(None, {**first, "spanId": "unstamped"}),
        )) + "\n", encoding="utf-8")

        sessions = collect_trace_sessions([path])

        self.assertEqual(set(sessions), {"session-1", "session-2"})
        self.assertEqual(sessions["session-1"]["start"], (BASE_NANOS + 5 * MILLISECOND) // MILLISECOND)
        self.assertEqual([span["spanId"] for span in sessions["session-1"]["spans"]], ["one"])
        self.assertEqual(len(sessions["session-2"]["spans"]), 1)
        self.assertEqual(sessions["session-2"]["spans"][0]["_started"], BASE_NANOS // MILLISECOND)


if __name__ == "__main__":
    unittest.main()
