from contextlib import closing, redirect_stdout
import errno
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import Mock, patch

from backend.app import database_backup, indexing_job, log_io
from backend.app.indexing import IndexingBlocked


class IndexingIoTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "workspace/GitHub.copilot-chat/debug-logs/synthetic/main.jsonl"
        self.log.parent.mkdir(parents=True)
        self.log.write_text("\n".join(json.dumps(event) for event in [
            {"ts": 1000, "type": "user_message", "attrs": {"content": "Synthetic question"}},
            {"ts": 1100, "type": "llm_request", "attrs": {"model": "synthetic", "outputTokens": 10}},
        ]) + "\n", encoding="utf-8")

    def test_log_child_supports_discovery_and_bounded_turn_reads(self) -> None:
        with patch.object(sys, "argv", ["log_io", "discover", str(self.root)]), redirect_stdout(io.StringIO()) as output:
            log_io.main()
        self.assertEqual(json.loads(output.getvalue())["synthetic"][0], str(self.log))
        with patch.object(sys, "argv", ["log_io", "turns", str(self.log), "0", "2000"]), redirect_stdout(io.StringIO()) as output:
            log_io.main()
        self.assertEqual(json.loads(output.getvalue())[0]["output_tokens"], 10)
        with patch.object(sys, "argv", ["log_io", "unknown", str(self.log)]):
            with self.assertRaises(ValueError):
                log_io.main()

    def test_discovery_tolerates_removed_files_and_rejects_excessive_log_counts(self) -> None:
        original_stat = Path.stat

        def removed(candidate, *arguments, **keywords):
            if candidate == self.log:
                raise FileNotFoundError(errno.ENOENT, "synthetic missing log")
            return original_stat(candidate, *arguments, **keywords)

        with patch.object(Path, "stat", autospec=True, side_effect=removed):
            self.assertEqual(log_io.discover_logs(self.root), {})
        with patch.object(log_io, "MAX_DISCOVERED_LOGS", 0):
            with self.assertRaisesRegex(IndexingBlocked, "log_discovery_limit"):
                log_io.discover_logs(self.root)

    def test_log_larger_than_64_mib_streams_without_retaining_all_events(self) -> None:
        ignored_event = json.dumps({"type": "tool_result", "attrs": {"content": "x" * 65_536}}) + "\n"
        with self.log.open("a", encoding="utf-8") as output:
            for _event in range(1_025):
                output.write(ignored_event)
            output.write(json.dumps({
                "ts": 1200, "type": "llm_request",
                "attrs": {"model": "synthetic", "outputTokens": 7},
            }) + "\n")
        self.assertGreater(self.log.stat().st_size, 64 * 1024 * 1024)
        tracemalloc.start()
        try:
            with (
                patch.object(sys, "argv", ["log_io", "turns", str(self.log), "0", "2000"]),
                redirect_stdout(io.StringIO()) as result,
            ):
                log_io.main()
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(json.loads(result.getvalue())[0]["output_tokens"], 17)
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_isolated_io_errors_remain_bounded_and_do_not_echo_private_details(self) -> None:
        with patch.object(log_io.subprocess, "run", return_value=Mock(returncode=0, stdout='{"synthetic": []}')):
            self.assertEqual(log_io.isolated_log_io("discover", self.root), {"synthetic": []})
        with patch.object(log_io.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 5)):
            with self.assertRaisesRegex(IndexingBlocked, "log_io_timeout"):
                log_io.isolated_log_io("discover", self.root)
        with patch.object(log_io.subprocess, "run", return_value=Mock(returncode=1, stderr="private fixture")):
            with self.assertRaisesRegex(IndexingBlocked, "^log_unavailable$"):
                log_io.isolated_log_io("discover", self.root)

    def test_preparation_parent_handles_failure_and_explicit_blocked_reasons(self) -> None:
        with patch.object(indexing_job.subprocess, "run", return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(IndexingBlocked, "preparation_failed"):
                indexing_job.prepare_in_process(self.root / "db", {}, False, {}, None)
        with patch.object(indexing_job.subprocess, "run", return_value=Mock(returncode=0, stdout='{"error":"waiting_for_logs"}')):
            with self.assertRaisesRegex(IndexingBlocked, "waiting_for_logs"):
                indexing_job.prepare_in_process(self.root / "db", {}, False, {}, None)

    def test_preparation_child_opens_database_read_only_and_returns_safe_results(self) -> None:
        database = self.root / "value.db"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE fixture (value INTEGER)")
        request = json.dumps({
            "database": str(database), "work": {}, "contentEnabled": False,
            "logs": {"synthetic": [str(self.log), 1, 2]}, "logError": None,
        })

        def prepare(store, _work, _content_enabled):
            connection = store._connect()
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO fixture VALUES (1)")
            finally:
                connection.close()
            return {"experiment": "synthetic"}, []

        with (
            patch("backend.app.store.ValueStore._prepare_index_work", new=prepare),
            patch.object(sys, "stdin", io.StringIO(request)),
            redirect_stdout(io.StringIO()) as output,
        ):
            indexing_job.main()
        self.assertEqual(json.loads(output.getvalue()), {"artifact": {"experiment": "synthetic"}, "groups": []})
        for failure, reason in ((IndexingBlocked("waiting_for_logs"), "waiting_for_logs"), (ValueError("private fixture"), "preparation_failed")):
            with (
                patch("backend.app.store.ValueStore._prepare_index_work", side_effect=failure),
                patch.object(sys, "stdin", io.StringIO(request)),
                redirect_stdout(io.StringIO()) as output,
            ):
                indexing_job.main()
            self.assertEqual(json.loads(output.getvalue()), {"error": reason})

    def test_backup_command_preserves_source_and_verifies_destination(self) -> None:
        source = self.root / "source.db"
        destination = self.root / "backup.db"
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute("CREATE TABLE fixture (value INTEGER)")
            connection.execute("INSERT INTO fixture VALUES (7)")
        with patch.object(sys, "argv", ["database_backup", str(source), str(destination)]), redirect_stdout(io.StringIO()) as output:
            database_backup.main()
        self.assertEqual(output.getvalue().strip(), "SQLite backup verified")
        with closing(sqlite3.connect(destination)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM fixture").fetchone()[0], 7)


if __name__ == "__main__":
    unittest.main()