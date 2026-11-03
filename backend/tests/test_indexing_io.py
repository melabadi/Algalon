from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.app import database_backup, indexing_job
from backend.app.indexing import IndexingBlocked


class IndexingIoTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_preparation_parent_handles_failure_and_explicit_blocked_reasons(self) -> None:
        with patch.object(indexing_job.subprocess, "run", return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(IndexingBlocked, "preparation_failed"):
                indexing_job.prepare_in_process(self.root / "db", {}, False)
        with patch.object(indexing_job.subprocess, "run", return_value=Mock(returncode=0, stdout='{"error":"waiting_for_artifact"}')):
            with self.assertRaisesRegex(IndexingBlocked, "waiting_for_artifact"):
                indexing_job.prepare_in_process(self.root / "db", {}, False)

    def test_preparation_parent_sends_only_database_work_and_content_policy(self) -> None:
        result = Mock(returncode=0, stdout='{"artifact":{"experiment":"synthetic"},"groups":[]}')
        with patch.object(indexing_job.subprocess, "run", return_value=result) as run:
            artifact, groups = indexing_job.prepare_in_process(
                self.root / "value.db", {"work_key": "trace:synthetic"}, True,
            )
        self.assertEqual((artifact, groups), ({"experiment": "synthetic"}, []))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {
            "database": str(self.root / "value.db"),
            "work": {"work_key": "trace:synthetic"},
            "contentEnabled": True,
        })
        self.assertEqual(run.call_args.args[0][1:], ["-B", "-m", "backend.app.indexing_job"])
        self.assertEqual(run.call_args.kwargs["timeout"], indexing_job.PREPARATION_TIMEOUT_SECONDS)

    def test_preparation_child_opens_database_read_only_and_returns_safe_results(self) -> None:
        database = self.root / "value.db"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE fixture (value INTEGER)")
        request = json.dumps({"database": str(database), "work": {}, "contentEnabled": False})

        def prepare(store, _work, _content_enabled):
            self.assertFalse(store.isolate_preparation)
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
        for failure, reason in (
            (IndexingBlocked("waiting_for_telemetry"), "waiting_for_telemetry"),
            (ValueError("private fixture"), "preparation_failed"),
        ):
            with (
                patch("backend.app.store.ValueStore._prepare_index_work", side_effect=failure),
                patch.object(sys, "stdin", io.StringIO(request)),
                redirect_stdout(io.StringIO()) as output,
            ):
                indexing_job.main()
            self.assertEqual(json.loads(output.getvalue()), {"error": reason})
            self.assertNotIn("private fixture", output.getvalue())

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