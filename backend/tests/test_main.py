from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from starlette.testclient import TestClient
from backend.app.indexing import IndexingBlocked


class BackendAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(cls.temporary_directory.name)
        sessions = root / "sessions"
        static = root / "static"
        (static / "assets").mkdir(parents=True)
        sessions.mkdir()
        (static / "index.html").write_text("<main>Algalon</main>", encoding="utf-8")
        (static / "asset.txt").write_text("asset", encoding="utf-8")
        (static / "assets" / "app.js").write_text("export {};", encoding="utf-8")
        config = root / "value-model.local.json"
        config.write_text("{}\n", encoding="utf-8")
        cls.environment = patch.dict(os.environ, {
            "COPILOT_VALUE_DB": str(root / "value.db"),
            "COPILOT_VALUE_SESSION_DIR": str(sessions),
            "COPILOT_VALUE_TRACE_ARCHIVE": str(root / "traces.json"),
            "COPILOT_VALUE_CHAT_LOG_ROOT": str(root / "workspace-storage"),
            "COPILOT_VALUE_CONFIG": str(config),
            "COPILOT_VALUE_STATIC_DIR": str(static),
        })
        cls.environment.start()
        sys.modules.pop("backend.app.main", None)
        cls.main = importlib.import_module("backend.app.main")
        cls.real_store = cls.main.store
        cls.store = Mock()
        cls.main.store = cls.store
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.main.store = cls.real_store
        cls.client.close()
        cls.environment.stop()
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.store.reset_mock()
        self.store.indexing_status.return_value = {"state": "current", "reason": None}
        self.main.indexing_failure = None
        self.main.indexing_started_at = None

    def test_health_overview_and_methodology_routes(self) -> None:
        self.store.overview.return_value = {"totals": {"sessions": 2}}
        self.store.insights.return_value = {"summary": {"metrics": 13}}
        self.store.methodology.return_value = {"claim": "Modeled AI Usage ROI"}

        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        overview = self.client.get("/api/overview?scenario=optimistic&days=90")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["totals"]["sessions"], 2)
        self.store.overview.assert_called_once_with("optimistic", 90)
        self.assertEqual(
            self.client.get("/api/methodology").json()["claim"],
            "Modeled AI Usage ROI",
        )
        insights = self.client.get("/api/insights?days=90")
        self.assertEqual(insights.status_code, 200)
        self.assertEqual(insights.json()["summary"]["metrics"], 13)
        self.store.insights.assert_called_once_with(90)

        self.assertEqual(
            self.client.get("/api/overview?scenario=unknown").status_code,
            400,
        )
        self.assertEqual(self.client.get("/api/overview?days=0").status_code, 422)
        self.assertEqual(self.client.get("/api/insights?days=0").status_code, 422)

        self.main.indexing_failure = "local detail must not leave the app"
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 503)
        self.assertEqual(
            health.json(),
            {"status": "degraded", "component": "indexing"},
        )
        self.assertNotIn("local detail", health.text)

    def test_health_reports_stale_indexing_without_failing_normal_work(self) -> None:
        with patch.object(self.main, "indexing_started_at", 10.0):
            for elapsed in (0, 59.9, 60, 1_800):
                with (
                    self.subTest(elapsed=elapsed),
                    patch.object(self.main, "monotonic", return_value=10.0 + elapsed),
                ):
                    response = self.client.get("/api/health")
                    if elapsed < self.main.INDEXING_STALE_AFTER_SECONDS:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json(), {"status": "ok"})
                    else:
                        self.assertEqual(response.status_code, 503)
                        self.assertEqual(response.json(), {
                            "status": "degraded",
                            "component": "indexing",
                            "reason": "stale",
                            "elapsedSeconds": elapsed,
                        })

    def test_health_uses_one_snapshot_when_indexing_finishes_concurrently(self) -> None:
        self.main.indexing_started_at = 10.0

        def finish_indexing() -> float:
            self.main.indexing_started_at = None
            return 20.0

        with patch.object(self.main, "monotonic", side_effect=finish_indexing):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_internal_otlp_routes_ingest_and_page_records(self) -> None:
        self.store.ingest_otlp_traces.return_value = {"received": 1, "accepted": 1}
        self.store.otel_records.return_value = {
            "records": [{"resourceSpans": []}],
            "nextCursor": 4,
            "hasMore": False,
        }

        response = self.client.post(
            "/api/internal/otel/v1/traces",
            json={"resourceSpans": []},
        )
        page = self.client.get("/api/internal/otel/records?after=3&limit=10")

        self.assertEqual(response.json(), {})
        self.store.ingest_otlp_traces.assert_called_once_with({"resourceSpans": []})
        self.assertEqual(page.json()["nextCursor"], 4)
        self.store.otel_records.assert_called_once_with(3, 10)

    def test_indexing_status_and_backpressure_routes(self) -> None:
        progress = {
            "state": "blocked", "pendingSessions": 2, "blockedSessions": 1,
            "oldestPendingSeconds": 61, "lastSuccessfulAt": None,
            "lastDiscoveryAt": None, "reason": "invalid_prompt_counters",
        }
        self.store.indexing_status.return_value = progress
        self.assertEqual(self.client.get("/api/indexing").json(), progress)
        self.assertEqual(self.client.get("/api/health").status_code, 503)
        self.store.ingest_otlp_traces.side_effect = IndexingBlocked("storage_pressure")
        try:
            response = self.client.post("/api/internal/otel/v1/traces", json={"resourceSpans": []})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["Retry-After"], "5")
            self.assertEqual(response.json()["detail"]["reason"], "storage_pressure")
        finally:
            self.store.ingest_otlp_traces.side_effect = None

    def test_session_and_prompt_routes_cover_found_and_missing_records(self) -> None:
        self.store.session.side_effect = lambda experiment, scenario="base": (
            {"experiment": experiment, "scenario": scenario}
            if experiment == "known"
            else None
        )
        self.store.prompts.return_value = [{"promptId": "prompt-1"}]
        self.store.prompt.side_effect = lambda prompt_id: (
            {"promptId": prompt_id} if prompt_id == "prompt-1" else None
        )

        session = self.client.get("/api/sessions/known?scenario=pessimistic")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["scenario"], "pessimistic")
        self.assertEqual(self.client.get("/api/sessions/missing").status_code, 404)

        prompts = self.client.get("/api/sessions/known/prompts")
        self.assertEqual(prompts.status_code, 200)
        self.assertEqual(prompts.json(), [{"promptId": "prompt-1"}])
        self.assertEqual(
            self.client.get("/api/sessions/missing/prompts").status_code,
            404,
        )

        self.assertEqual(
            self.client.get("/api/prompts/prompt-1").json(),
            {"promptId": "prompt-1"},
        )
        self.assertEqual(self.client.get("/api/prompts/missing").status_code, 404)

    def test_export_route_downloads_all_session_and_prompt_evidence(self) -> None:
        self.store.export_data.return_value = (
            [{"experiment": "session-1", "benchmark": {"scenarios": {"base": {"netValueUsd": 4}}}}],
            {"session-1": [{"promptId": "prompt-1", "content": "Explain the result"}]},
        )

        response = self.client.get("/api/export.csv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="algalon-data.csv"',
        )
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        text = response.content.decode("utf-8-sig")
        self.assertIn("Session > Benchmark > Scenarios > Base > Net Value USD", text)
        self.assertIn("Explain the result", text)
        self.store.export_data.assert_called_once_with()

    def test_static_file_and_spa_fallback_routes(self) -> None:
        asset = self.client.get("/asset.txt")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.text, "asset")

        spa = self.client.get("/sessions/example")
        self.assertEqual(spa.status_code, 200)
        self.assertIn("Algalon", spa.text)

    def test_static_routes_never_read_outside_the_static_directory(self) -> None:
        private_file = self.main.static_directory.parent / "private-static.txt"
        private_file.write_text("private fixture content", encoding="utf-8")
        self.addCleanup(private_file.unlink)
        for path in ("/%2e%2e%2fprivate-static.txt", "/%2e%2e%5cprivate-static.txt"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotIn("private fixture content", response.text)
                self.assertIn(response.status_code, (200, 404))


class BackendLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexing_loop_logs_failures_before_retrying(self) -> None:
        main = BackendAppTests.main
        main.indexing_failure = None
        with (
            patch.object(main.asyncio, "to_thread", new=AsyncMock(side_effect=RuntimeError("index failed"))),
            patch.object(main.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
            patch.object(main.logger, "exception") as log_exception,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await main.indexing_loop()
        log_exception.assert_called_once_with("Local evidence indexing failed; retrying.")
        self.assertEqual(main.indexing_failure, "index failed")
        self.assertIsNone(main.indexing_started_at)

    async def test_indexing_loop_clears_degraded_health_after_success(self) -> None:
        main = BackendAppTests.main
        main.indexing_failure = "previous failure"

        async def check_running_state(index_once) -> None:
            self.assertEqual(main.indexing_started_at, 10.0)
            self.assertEqual(index_once, main.store.index_once)

        with (
            patch.object(main, "monotonic", return_value=10.0),
            patch.object(main.asyncio, "to_thread", new=AsyncMock(side_effect=check_running_state)),
            patch.object(main.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await main.indexing_loop()
        self.assertIsNone(main.indexing_failure)
        self.assertIsNone(main.indexing_started_at)

    async def test_lifespan_starts_and_cancels_background_indexing(self) -> None:
        main = BackendAppTests.main
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocking_indexing_loop() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(main, "indexing_loop", new=blocking_indexing_loop):
            async with main.lifespan(main.app):
                await asyncio.wait_for(started.wait(), timeout=1)

        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()