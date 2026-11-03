from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.app.store import ValueStore
from backend.app.database_backup import backup_database
from backend.app.indexing import IndexingBlocked, bootstrap_indexing, enqueue_session, retry_work
from backend.app.indexing_job import prepare_in_process
from backend.app.session_identity import public_session_id
from backend.tests.runtime_otlp import attribute, chat_span, runtime_payload


class DurableIndexingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config.json"
        self.config.write_text("{}\n", encoding="utf-8")
        self.started = int(datetime.now(timezone.utc).timestamp()) * 1_000
        self.store = self.new_store()

    def new_store(self) -> ValueStore:
        return ValueStore(
            self.root / "value.db", self.root / "sessions",
            self.root / "traces.json", self.config,
        )

    def payload(self, session: str = "session-a", ordinal: int = 0) -> dict:
        started = (self.started + ordinal * 1_000) * 1_000_000
        return runtime_payload([chat_span(
            trace_id=f"trace-{session}-{ordinal}",
            span_id=f"span-{session}-{ordinal}",
            conversation=session,
            start_ns=started,
            end_ns=started + 500_000_000,
            input_tokens=100,
            output_tokens=10,
        )])

    def artifact(
        self, session: str = "session-a", output: int = 10, *,
        directory: Path | None = None, **fields: object,
    ) -> str:
        experiment = public_session_id(session, self.started)
        directory = directory or self.root / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{experiment}.json").write_text(json.dumps({
            "experiment": experiment,
            "startedAt": datetime.fromtimestamp(self.started / 1_000, timezone.utc).isoformat(),
            "completedAt": datetime.fromtimestamp(self.started / 1_000 + 60, timezone.utc).isoformat(),
            "status": "published",
            "usage": {"source": "otel_traces", "outputTokens": output},
            "source": {}, "benchmark": None,
            **fields,
        }), encoding="utf-8")
        return experiment

    def test_preparation_blocks_only_the_affected_session_with_an_explicit_reason(self) -> None:
        earlier_start = datetime.fromtimestamp(self.started / 1_000 - 60, timezone.utc).isoformat()

        def retained_history_gap(_root: Path, sessions: Path) -> None:
            self.artifact(directory=sessions, startedAt=earlier_start)

        def unsummarized_runtime_session(_root: Path, sessions: Path) -> None:
            self.artifact(directory=sessions, calculationVersion=7)

        def duplicated_artifact(_root: Path, sessions: Path) -> None:
            experiment = self.artifact(directory=sessions)
            (sessions / "session-duplicate.json").write_text(
                (sessions / f"{experiment}.json").read_text(encoding="utf-8"), encoding="utf-8",
            )

        def malformed_artifact(_root: Path, sessions: Path) -> None:
            experiment = self.artifact(directory=sessions)
            (sessions / f"{experiment}.json").write_text("[]", encoding="utf-8")

        def missing_telemetry(root: Path, sessions: Path) -> None:
            self.artifact(directory=sessions)
            with closing(sqlite3.connect(root / "value.db")) as connection, connection:
                connection.execute("DELETE FROM otel_records")

        cases = (
            ("incomplete_retained_history", retained_history_gap, True),
            ("waiting_for_worker", unsummarized_runtime_session, False),
            ("ambiguous_artifact", duplicated_artifact, True),
            ("invalid_artifact", malformed_artifact, True),
            ("waiting_for_telemetry", missing_telemetry, False),
        )
        for reason, arrange, blocked in cases:
            with self.subTest(reason=reason):
                root = self.root / reason
                sessions = root / "sessions"
                store = ValueStore(root / "value.db", sessions, root / "traces.json", self.config)
                store.ingest_otlp_traces(self.payload())
                arrange(root, sessions)
                store.index_once()
                with closing(store._connect()) as connection:
                    work = connection.execute(
                        "SELECT * FROM indexing_work WHERE session_id = 'session-a'"
                    ).fetchone()
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 0)
                self.assertEqual(work["error_code"], reason)
                self.assertEqual(work["indexed_version"], 0)
                status = store.indexing_status()
                self.assertEqual(status["state"], "blocked" if blocked else "catching_up")
                self.assertEqual(status["reason"], reason if blocked else None)

    def test_oversized_artifact_is_blocked_until_it_fits(self) -> None:
        self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        with patch("backend.app.indexing.MAX_ARTIFACT_BYTES", 1):
            self.store.index_once()
        self.assertEqual(self.store.indexing_status()["reason"], "artifact_too_large")
        self.store.index_once()
        self.assertEqual(self.store.indexing_status()["state"], "current")

    def test_durable_work_survives_restart_and_deduplicated_replay(self) -> None:
        self.assertEqual(self.store.ingest_otlp_traces(self.payload())["accepted"], 1)
        with closing(self.store._connect()) as connection:
            original = dict(connection.execute(
                "SELECT * FROM indexing_work WHERE session_id = 'session-a'"
            ).fetchone())
        self.assertEqual(original["requested_version"], 1)
        self.assertEqual(original["indexed_version"], 0)
        self.assertGreater(original["latest_cursor"], 0)

        restarted = self.new_store()
        self.assertEqual(restarted.ingest_otlp_traces(self.payload())["accepted"], 0)
        with closing(restarted._connect()) as connection:
            resumed = dict(connection.execute(
                "SELECT * FROM indexing_work WHERE session_id = 'session-a'"
            ).fetchone())
        self.assertEqual(resumed, original)

    def test_inbox_and_pending_work_are_committed_together(self) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute("""
                CREATE TRIGGER reject_test_work BEFORE INSERT ON indexing_work
                BEGIN SELECT RAISE(ABORT, 'simulated queue failure'); END
            """)
            connection.commit()

        with self.assertRaises(sqlite3.DatabaseError):
            self.store.ingest_otlp_traces(self.payload())

        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM otel_records").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM indexing_work").fetchone()[0], 0)

    def test_snapshot_and_checkpoint_rollback_together_then_resume(self) -> None:
        experiment = self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        self.store.index_once()
        original = self.store.prompts(experiment)
        with closing(self.store._connect()) as connection:
            checkpoint = connection.execute(
                "SELECT indexed_version FROM indexing_work WHERE session_id = 'session-a'"
            ).fetchone()[0]
            self.assertGreater(checkpoint, 0)
            connection.execute("""
                CREATE TRIGGER fail_test_checkpoint BEFORE UPDATE OF indexed_version ON indexing_work
                BEGIN SELECT RAISE(ABORT, 'simulated crash at checkpoint'); END
            """)
            connection.commit()
        self.artifact(output=20)
        self.store.ingest_otlp_traces(self.payload(ordinal=1))
        self.store.index_once()
        self.assertEqual(self.store.prompts(experiment), original)
        self.assertEqual(self.store.session(experiment)["tokens"]["output"], 10)
        with closing(self.store._connect()) as connection:
            work = connection.execute(
                "SELECT * FROM indexing_work WHERE session_id = 'session-a'"
            ).fetchone()
            self.assertEqual(work["indexed_version"], checkpoint)
            self.assertGreater(work["requested_version"], checkpoint)
            connection.execute("DROP TRIGGER fail_test_checkpoint")
            connection.execute("UPDATE indexing_work SET next_attempt_at = 0")
            connection.commit()
        restarted = self.new_store()
        restarted.index_once()
        self.assertEqual(len(restarted.prompts(experiment)), 2)
        self.assertEqual(restarted.session(experiment)["tokens"]["output"], 20)

    def test_missing_artifact_does_not_block_other_sessions(self) -> None:
        self.store.ingest_otlp_traces(self.payload("missing-artifact"))
        experiment = self.artifact("ready")
        self.store.ingest_otlp_traces(self.payload("ready"))
        self.store.index_once()
        self.assertEqual(len(self.store.prompts(experiment)), 1)
        with closing(self.store._connect()) as connection:
            missing = connection.execute(
                "SELECT * FROM indexing_work WHERE session_id = 'missing-artifact'"
            ).fetchone()
            self.assertEqual(missing["indexed_version"], 0)
            self.assertEqual(missing["error_code"], "waiting_for_artifact")
            self.assertGreater(missing["next_attempt_at"], 0)

    def test_isolated_preparation_publishes_without_a_second_writer(self) -> None:
        experiment = self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        self.store.isolate_preparation = True
        self.store.index_once()
        self.assertEqual(self.store.prompts(experiment)[0]["outputTokens"], 10)
        with closing(self.store._connect()) as connection:
            work = connection.execute("SELECT * FROM indexing_work").fetchone()
            self.assertEqual(work["requested_version"], work["indexed_version"])

    def test_freshness_follows_pending_work_and_success_not_api_liveness(self) -> None:
        self.assertEqual(self.store.indexing_status()["state"], "catching_up")
        self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        self.assertEqual(self.store.indexing_status()["pendingSessions"], 1)
        self.store.index_once()
        current = self.store.indexing_status()
        self.assertEqual(current["state"], "current")
        self.assertIsNotNone(current["lastSuccessfulAt"])
        self.store.ingest_otlp_traces(self.payload(ordinal=1))
        with closing(self.store._connect()) as connection, connection:
            connection.execute("UPDATE indexing_work SET pending_since = pending_since - 120")
        self.assertEqual(self.store.indexing_status()["state"], "blocked")
        self.assertEqual(self.store.indexing_status()["reason"], "stale")
        self.store.index_once()
        self.assertEqual(self.store.indexing_status()["state"], "current")

    def test_storage_pressure_does_not_acknowledge_or_commit_inbox_data(self) -> None:
        with patch("backend.app.indexing.shutil.disk_usage") as capacity:
            capacity.return_value.free = 1
            with self.assertRaisesRegex(IndexingBlocked, "storage_pressure"):
                self.store.ingest_otlp_traces(self.payload())
        self.assertEqual(self.store.otel_records()["records"], [])

    def test_worker_cursor_must_cover_pending_spans(self) -> None:
        experiment = self.artifact()
        path = self.root / "sessions" / f"{experiment}.json"
        artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact["inboxCursor"] = 0
        path.write_text(json.dumps(artifact), encoding="utf-8")
        self.store.ingest_otlp_traces(self.payload())
        self.store.index_once()
        self.assertIsNone(self.store.session(experiment))
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT error_code FROM indexing_work").fetchone()[0], "waiting_for_worker")
        artifact["inboxCursor"] = self.store.otel_records()["nextCursor"]
        path.write_text(json.dumps(artifact), encoding="utf-8")
        self.store.index_once()
        self.assertEqual(self.store.indexing_status()["state"], "current")

    def test_online_backup_restores_evidence_and_unfinished_work(self) -> None:
        experiment = self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        self.store.index_once()
        self.store.ingest_otlp_traces(self.payload(ordinal=1))
        backup = self.root / "backup.sqlite3"
        backup_database(self.root / "value.db", backup)
        with self.assertRaises(FileExistsError):
            backup_database(self.root / "value.db", backup)
        restored_path = self.root / "restored.sqlite3"
        backup_database(backup, restored_path)
        restored = ValueStore(restored_path, self.root / "sessions", self.root / "traces.json", self.config)
        self.assertEqual(len(restored.otel_records()["records"]), 2)
        self.assertEqual(restored.indexing_status()["pendingSessions"], 1)
        restored.index_once()
        self.assertEqual(len(restored.prompts(experiment)), 2)
        self.assertEqual(restored.indexing_status()["state"], "current")

    def test_hard_process_exit_rolls_back_unpublished_results(self) -> None:
        experiment = self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        self.store.index_once()
        self.artifact(output=20)
        self.store.ingest_otlp_traces(self.payload(ordinal=1))
        code = (
            "import os,sys; from pathlib import Path; import backend.app.store as module; "
            "root=Path(sys.argv[1]); store=module.ValueStore(root/'value.db',root/'sessions',root/'traces.json',root/'config.json'); "
            "module.complete_work=lambda *arguments: os._exit(73); store.index_once()"
        )
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.root)], timeout=15, capture_output=True)
        self.assertEqual(result.returncode, 73)
        self.assertEqual(len(self.store.prompts(experiment)), 1)
        self.assertEqual(self.store.session(experiment)["tokens"]["output"], 10)
        restarted = self.new_store()
        restarted.index_once()
        self.assertEqual(len(restarted.prompts(experiment)), 2)
        self.assertEqual(restarted.session(experiment)["tokens"]["output"], 20)

    def test_new_input_during_preparation_remains_pending(self) -> None:
        experiment = self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        prepare = self.store._prepare_index_work

        def arrival(work, content_enabled):
            result = prepare(work, content_enabled)
            self.store.ingest_otlp_traces(self.payload(ordinal=1))
            return result

        with patch.object(self.store, "_prepare_index_work", side_effect=arrival):
            self.store.index_once()
        self.assertEqual(len(self.store.prompts(experiment)), 1)
        self.assertEqual(self.store.indexing_status()["pendingSessions"], 1)
        self.store.index_once()
        self.assertEqual(len(self.store.prompts(experiment)), 2)
        self.assertEqual(self.store.indexing_status()["state"], "current")

    def test_malformed_session_is_blocked_without_blocking_healthy_work(self) -> None:
        self.artifact("bad")
        malformed = self.payload("bad")
        malformed["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
            attribute("gen_ai.usage.output_tokens", -1, "intValue")
        )
        self.store.ingest_otlp_traces(malformed)
        healthy = self.artifact("healthy")
        self.store.ingest_otlp_traces(self.payload("healthy"))
        self.store.index_once()
        self.assertEqual(len(self.store.prompts(healthy)), 1)
        self.assertEqual(self.store.indexing_status()["blockedSessions"], 1)
        self.assertEqual(self.store.indexing_status()["reason"], "invalid_prompt_counters")
        self.assertEqual(len(self.store.otel_records()["records"]), 2)

    def test_batches_are_bounded_and_remaining_sessions_resume(self) -> None:
        for ordinal in range(24):
            self.artifact(f"bounded-{ordinal}")
            self.store.ingest_otlp_traces(self.payload(f"bounded-{ordinal}"))
        self.store.index_once()
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 16)
        self.assertEqual(self.store.indexing_status()["pendingSessions"], 8)
        self.store.index_once()
        self.assertEqual(self.store.indexing_status()["state"], "current")

    def test_bootstrap_resumes_without_replaying_completed_pages(self) -> None:
        for ordinal in range(3):
            self.store.ingest_otlp_traces(self.payload(ordinal=ordinal))
        with closing(self.store._connect()) as connection, connection:
            connection.execute("DELETE FROM indexing_work")
            connection.execute("DELETE FROM store_metadata WHERE key = 'indexing_queue_v1'")
        restarted = self.new_store()
        with closing(restarted._connect()) as connection:
            bootstrap_indexing(connection, page_size=2)
            first = connection.execute("SELECT value FROM store_metadata WHERE key = 'indexing_bootstrap_cursor'").fetchone()[0]
        restarted = self.new_store()
        with closing(restarted._connect()) as connection:
            bootstrap_indexing(connection, page_size=2)
            final = connection.execute("SELECT value FROM store_metadata WHERE key = 'indexing_bootstrap_cursor'").fetchone()[0]
            self.assertGreater(int(final), int(first))
            self.assertIsNotNone(connection.execute("SELECT value FROM store_metadata WHERE key = 'indexing_queue_v1'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM indexing_work").fetchone()[0], 1)

    def test_lock_contention_rejects_ingestion_without_losing_replay(self) -> None:
        with closing(self.store._connect()) as competitor:
            competitor.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                self.store.ingest_otlp_traces(self.payload())
            competitor.rollback()
        self.assertEqual(self.store.ingest_otlp_traces(self.payload())["accepted"], 1)
        self.assertEqual(self.store.ingest_otlp_traces(self.payload())["accepted"], 0)

    def test_preparation_timeout_is_a_retryable_blocked_result(self) -> None:
        with patch("backend.app.indexing_job.subprocess.run", side_effect=subprocess.TimeoutExpired("synthetic", 15)):
            with self.assertRaisesRegex(IndexingBlocked, "preparation_timeout"):
                prepare_in_process(self.root / "value.db", {}, False)

    def test_inbox_byte_budget_preserves_cursor_progress(self) -> None:
        for ordinal in range(3):
            self.store.ingest_otlp_traces(self.payload(ordinal=ordinal))
        with patch("backend.app.store.OTEL_INBOX_PAGE_BYTES", 1):
            first = self.store.otel_records(limit=10_000)
            self.assertEqual(len(first["records"]), 1)
            self.assertTrue(first["hasMore"])
            second = self.store.otel_records(first["nextCursor"], limit=10_000)
            final = self.store.otel_records(second["nextCursor"], limit=10_000)
            self.assertFalse(final["hasMore"])
            self.assertGreater(final["nextCursor"], second["nextCursor"])

    def test_temporarily_unreadable_artifact_recovers_without_a_content_change(self) -> None:
        experiment = self.artifact()
        self.store.ingest_otlp_traces(self.payload())
        path = self.root / "sessions" / f"{experiment}.json"
        original_read = Path.read_text

        def unavailable(candidate, *arguments, **keywords):
            if candidate == path:
                raise PermissionError("temporary fixture failure")
            return original_read(candidate, *arguments, **keywords)

        with patch.object(Path, "read_text", autospec=True, side_effect=unavailable):
            self.store.index_once()
            self.assertEqual(self.store.indexing_status()["reason"], "artifact_unavailable")
            with closing(self.store._connect()) as connection:
                version = connection.execute("SELECT requested_version FROM indexing_work").fetchone()[0]
            self.store.index_once()
            with closing(self.store._connect()) as connection:
                self.assertEqual(connection.execute("SELECT requested_version FROM indexing_work").fetchone()[0], version)
        self.store.index_once()
        self.assertEqual(self.store.indexing_status()["state"], "current")
        self.assertEqual(len(self.store.prompts(experiment)), 1)

    def test_failed_older_job_records_attempt_without_backing_off_new_input(self) -> None:
        self.store.ingest_otlp_traces(self.payload())
        with closing(self.store._connect()) as connection, connection:
            selected = connection.execute("SELECT * FROM indexing_work").fetchone()
            enqueue_session(connection, "session-a")
            retry_work(connection, selected, "preparation_timeout")
            current = connection.execute("SELECT * FROM indexing_work").fetchone()
            self.assertGreater(current["last_attempt_at"], selected["last_attempt_at"])
            self.assertEqual(current["next_attempt_at"], 0)
            self.assertIsNone(current["error_code"])


if __name__ == "__main__":
    unittest.main()