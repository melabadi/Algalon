from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import URLError
import zipfile

from scripts import copilot_value, release_smoke


class FakeResponse:
    def __init__(self, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *_arguments) -> None:
        return None

    def read(self, *_arguments) -> bytes:
        return json.dumps(self.payload).encode()


def create_release_tree(root: Path) -> None:
    required_paths = (
        "dist/shared/benchmark.js",
        "dist/src/cli.js",
        "dist/src/source-delta-cli.js",
        "node_modules/diff/package.json",
        "node_modules/zod/package.json",
        "config/otel-collector.yaml",
        "config/grafana/dashboards/personal-copilot-value.json",
        "config/value-model.example.json",
        "scripts/copilot_value.py",
        "scripts/docker_runtime.py",
        "scripts/errors.py",
        "scripts/release_smoke.py",
        "backend/app/main.py",
        "backend/app/store.py",
        "backend/requirements.txt",
        "web/package.json",
        "web/package-lock.json",
        "web/src/App.tsx",
        "shared/benchmark.ts",
    )
    for relative_path in required_paths:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "{}\n" if path.suffix == ".json" else "placeholder\n"
        if relative_path == "config/otel-collector.yaml":
            content = "receivers:\n  otlp: {}\n"
        path.write_text(content, encoding="utf-8")
    copilot_value.copy_release_path(Path(__file__).resolve().parents[1], root, "docs/images")


class BundleConstructionTests(unittest.TestCase):
    def test_runtime_routes_traces_to_transactional_sqlite_inbox(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        collector = (project_root / "config" / "otel-collector.yaml").read_text(
            encoding="utf-8"
        )
        compose = (project_root / "docker" / "compose.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("otlp_http/algalon:", collector)
        self.assertIn("storage: file_storage/indexing_queue", collector)
        self.assertIn("extensions: [health_check, file_storage/indexing_queue]", collector)
        self.assertIn("fsync: true", collector)
        self.assertIn("processors: [memory_limiter, transform/privacy]", collector)
        self.assertIn(
            "traces_endpoint: http://app:8000/api/internal/otel/v1/traces",
            collector,
        )
        self.assertNotIn("file/traces:", collector)
        self.assertIn("exporters: [otlp_http/algalon, span_metrics/copilot]", collector)
        self.assertIn(
            "COPILOT_VALUE_OTEL_INBOX_URL: http://app:8000/api/internal/otel/records",
            compose,
        )
        worker = compose.split("  worker:", 1)[1].split("  app:", 1)[0]
        app = compose.split("  app:", 1)[1].split("volumes:", 1)[0]
        self.assertIn("- app", worker)
        self.assertNotIn("- worker", app)

    def test_app_image_copies_bundled_frontend_inputs(self) -> None:
        dockerfile = Path(__file__).resolve().parents[1] / "docker" / "App.Dockerfile"
        content = dockerfile.read_text(encoding="utf-8")

        self.assertIn("COPY shared/ /shared/", content)
        self.assertIn(
            "COPY config/value-model.example.json /config/value-model.example.json",
            content,
        )

    def test_docker_builds_use_configurable_package_feeds(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        worker = (project_root / "docker" / "Dockerfile").read_text(encoding="utf-8")
        app = (project_root / "docker" / "App.Dockerfile").read_text(encoding="utf-8")
        compose = (project_root / "docker" / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("ARG NPM_REGISTRY=https://registry.npmjs.org/", worker)
        self.assertIn('npm config set registry "$NPM_REGISTRY"', worker)
        self.assertIn("ARG NPM_REGISTRY=https://registry.npmjs.org/", app)
        self.assertIn('npm config set registry "$NPM_REGISTRY"', app)
        self.assertIn("ARG PIP_INDEX_URL=https://pypi.org/simple", app)
        self.assertIn('pip install --no-cache-dir --index-url "$PIP_INDEX_URL"', app)
        self.assertEqual(compose.count("NPM_REGISTRY: ${NPM_REGISTRY:-https://registry.npmjs.org/}"), 2)
        self.assertIn("PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}", compose)

    def test_copies_release_paths_and_writes_deterministic_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            payload = root / "payload"
            (source / "directory").mkdir(parents=True)
            (source / "directory" / "nested.txt").write_text("nested", encoding="utf-8")
            (source / "directory" / "module.pyc").write_bytes(b"local path")
            (source / "directory" / "__pycache__").mkdir()
            (source / "directory" / "__pycache__" / "module.pyc").write_bytes(b"local path")
            (source / "single.txt").write_text("single", encoding="utf-8")

            copilot_value.copy_release_path(source, payload, "directory")
            copilot_value.copy_release_path(source, payload, "single.txt")
            self.assertEqual((payload / "directory" / "nested.txt").read_text(), "nested")
            self.assertFalse((payload / "directory" / "module.pyc").exists())
            self.assertFalse((payload / "directory" / "__pycache__").exists())
            self.assertEqual((payload / "single.txt").read_text(), "single")

            archive_path = root / "release.zip"
            digest = copilot_value.write_archive(payload, archive_path)
            self.assertEqual(digest, copilot_value.hashlib.sha256(archive_path.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(sorted(archive.namelist()), ["directory/nested.txt", "single.txt"])

    def test_builds_zip_and_self_installing_release_from_staged_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory) / "project"
            output = project_root / "artifacts"
            project_root.mkdir(parents=True)
            (project_root / "package.json").write_text(
                json.dumps({
                    "version": "9.8.7",
                    "dependencies": {"diff": "1.0.0", "zod": "2.0.0"},
                }),
                encoding="utf-8",
            )

            def fake_copy(_project_root: Path, payload_root: Path, relative_path: str) -> None:
                destination = payload_root / relative_path
                if relative_path in {"dist/shared", "dist/src", "shared", "backend/app", "web/mock", "web/src", "config/grafana", "docs/images"}:
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / "placeholder.txt").write_text("payload", encoding="utf-8")
                    return
                destination.parent.mkdir(parents=True, exist_ok=True)
                if relative_path == "config/otel-collector.yaml":
                    content = "receivers:\n  otlp: {}\n"
                elif relative_path == "web/package.json":
                    content = json.dumps({
                        "devDependencies": {
                            "vite": "1.0.0",
                            "typescript": "1.0.0",
                            "vitest": "1.0.0",
                        }
                    })
                else:
                    content = "payload\n"
                destination.write_text(content, encoding="utf-8")

            def fake_run(command, *, cwd=None) -> None:
                if "install" in command:
                    Path(cwd, "package-lock.json").write_text("{}\n", encoding="utf-8")

            arguments = argparse.Namespace(output_directory=str(output), skip_tests=False)
            with (
                patch.object(copilot_value, "install_root", return_value=project_root),
                patch.object(copilot_value.shutil, "which", return_value="npm"),
                patch.object(copilot_value, "copy_release_path", side_effect=fake_copy),
                patch.object(copilot_value, "run_checked", side_effect=fake_run) as run_checked,
            ):
                self.assertEqual(copilot_value.command_bundle(arguments), 0)

            bundle = output / "copilot-value-dashboard-9.8.7.zip"
            application = output / "copilot-value-dashboard-9.8.7.pyz"
            self.assertTrue(bundle.is_file())
            self.assertTrue(application.is_file())
            self.assertEqual(run_checked.call_count, 3)
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.read("LICENSE").decode("utf-8").splitlines(), ["payload"])
                self.assertEqual(archive.read("SECURITY.md").decode("utf-8").splitlines(), ["payload"])
                self.assertNotIn("__main__.py", archive.namelist())
                self.assertNotIn("test", {name.split("/")[0] for name in archive.namelist()})
                self.assertIn("dist/shared/placeholder.txt", archive.namelist())
                self.assertIn("shared/placeholder.txt", archive.namelist())
                self.assertIn("docs/images/placeholder.txt", archive.namelist())
                self.assertIn("web/mock/placeholder.txt", archive.namelist())
                bundled_web_package = json.loads(archive.read("web/package.json"))
                self.assertEqual(
                    bundled_web_package["devDependencies"],
                    {"typescript": "1.0.0", "vite": "1.0.0"},
                )
            with zipfile.ZipFile(application) as archive:
                self.assertEqual(archive.read("LICENSE").decode("utf-8").splitlines(), ["payload"])
                self.assertEqual(archive.read("SECURITY.md").decode("utf-8").splitlines(), ["payload"])
                self.assertIn("__main__.py", archive.namelist())
                self.assertIn("scripts/__init__.py", archive.namelist())

    def test_bundle_requires_npm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "package.json").write_text(
                json.dumps({"version": "1.0.0", "dependencies": {}}), encoding="utf-8"
            )
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value.shutil, "which", return_value=None),
                self.assertRaisesRegex(copilot_value.CopilotValueError, "npm are required"),
            ):
                copilot_value.command_bundle(
                    argparse.Namespace(output_directory="artifacts", skip_tests=True)
                )


class TelemetrySmokeTests(unittest.TestCase):
    def test_posts_json_and_handles_transport_failures(self) -> None:
        with patch.object(release_smoke, "urlopen", return_value=FakeResponse(202)):
            copilot_value.post_json("http://collector/v1/metrics", {"value": 1})
        with patch.object(release_smoke, "urlopen", return_value=FakeResponse(500)):
            with self.assertRaisesRegex(copilot_value.CopilotValueError, r"failed \(500\)"):
                copilot_value.post_json("http://collector/v1/metrics", {})
        with patch.object(release_smoke, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "offline"):
                copilot_value.post_json("http://collector/v1/metrics", {})
        with patch.object(release_smoke, "urlopen", side_effect=TimeoutError):
            copilot_value.post_json("http://collector/v1/metrics", {})

    def test_queries_and_waits_for_victoria_metrics(self) -> None:
        payload = {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        with patch.object(release_smoke, "urlopen", return_value=FakeResponse(200, payload)):
            self.assertEqual(copilot_value.query_victoria_metrics("up"), payload)
        with patch.object(release_smoke, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(copilot_value.query_victoria_metrics("up"), {})

        with (
            patch.object(release_smoke, "query_victoria_metrics", side_effect=[{}, payload]),
            patch.object(release_smoke.time, "monotonic", side_effect=[0, 0, 0]),
            patch.object(release_smoke.time, "sleep") as sleep,
        ):
            self.assertEqual(copilot_value.wait_for_metric("up", 2), payload)
        sleep.assert_called_once_with(0.5)

        with (
            patch.object(release_smoke, "query_victoria_metrics", return_value={}),
            patch.object(release_smoke.time, "monotonic", side_effect=[0, 1]),
            self.assertRaisesRegex(copilot_value.CopilotValueError, "Timed out"),
        ):
            copilot_value.wait_for_metric("missing", 0)

    def test_builds_privacy_payload_and_runs_automatic_session_smoke(self) -> None:
        attribute = copilot_value.otlp_attribute("count", 3, "intValue")
        self.assertEqual(attribute["value"], {"intValue": "3"})
        payload = copilot_value.smoke_metric_payload("probe", "private-path")
        self.assertIn("private-path", json.dumps(payload))

        metric_result = {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        uuid_values = [SimpleNamespace(hex="smoke-id"), "session-uuid"]
        with (
            patch.object(release_smoke.uuid, "uuid4", side_effect=uuid_values),
            patch.object(release_smoke.time, "time", return_value=1_786_000_000.0),
            patch.object(release_smoke.secrets, "token_hex", side_effect=lambda length: "a" * (length * 2)),
            patch.object(release_smoke, "post_json") as post_json,
            patch.object(release_smoke, "wait_for_metric", return_value=metric_result) as wait_for_metric,
        ):
            experiment = copilot_value.run_telemetry_smoke()

        self.assertTrue(experiment.startswith("session-"))
        self.assertEqual(post_json.call_count, 2)
        self.assertEqual(wait_for_metric.call_count, 3)
        trace_payload = post_json.call_args_list[1].args[1]
        serialized = json.dumps(trace_payload)
        self.assertIn("portable-smoke-model", serialized)
        self.assertIn("apply_patch", serialized)

    def test_automatic_session_smoke_deduplicates_replay_and_accumulates_new_span(self) -> None:
        metric_result = {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        uuid_values = [SimpleNamespace(hex="smoke-id"), "session-uuid"]
        root = Path("release")
        with (
            patch.object(release_smoke.uuid, "uuid4", side_effect=uuid_values),
            patch.object(release_smoke.time, "time", return_value=1_786_000_000.0),
            patch.object(release_smoke.secrets, "token_hex", side_effect=lambda length: "a" * (length * 2)),
            patch.object(release_smoke, "post_json") as post_json,
            patch.object(release_smoke, "wait_for_metric", return_value=metric_result),
            patch.object(release_smoke, "wait_for_app_session") as wait_for_app,
            patch.object(release_smoke, "wait_for_app_overview") as wait_for_overview,
        ):
            experiment = copilot_value.run_telemetry_smoke(root)

        self.assertTrue(experiment.startswith("session-"))
        self.assertEqual(post_json.call_count, 4)
        self.assertEqual(wait_for_app.call_count, 3)
        self.assertEqual(post_json.call_args_list[1].args[1], post_json.call_args_list[2].args[1])
        first_trace = post_json.call_args_list[1].args[1]
        second_trace = post_json.call_args_list[3].args[1]
        self.assertIn("Synthetic portable smoke prompt", json.dumps(first_trace))
        self.assertIn("Synthetic portable smoke prompt from late-arriving earlier span", json.dumps(second_trace))
        self.assertIn("portable-smoke-session-uuid", json.dumps(first_trace))
        self.assertIn("portable-smoke-session-uuid", json.dumps(second_trace))
        first_started = int(first_trace["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["startTimeUnixNano"])
        second_started = int(second_trace["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["startTimeUnixNano"])
        self.assertLess(second_started, first_started)
        self.assertEqual(
            wait_for_app.call_args_list[-1].kwargs["expected_started_milliseconds"],
            second_started // 1_000_000,
        )
        wait_for_overview.assert_called_once_with(experiment)

    def test_waits_for_safe_session_and_prompt_indexing(self) -> None:
        session = {
            "experiment": "session-test",
            "startedAt": "2026-08-05T10:00:00Z",
            "durationSeconds": 1,
            "aiCostUsd": 0.01,
            "aiCredits": 1,
            "tokens": {"input": 100, "output": 50},
            "sourceEvidenceComplete": False,
            "retainedSourceCharacters": 0,
            "source": {"source": "otel_only"},
            "usage": {"elapsedSeconds": 1},
            "modelingStatus": "available",
            "scenarioResult": {
                "estimatedManualLaborCostUsd": 8,
                "estimatedAiAssistedLaborCostUsd": 2,
                "estimatedAiAssistedTotalCostUsd": 2.01,
                "estimatedGrossCostSavingsUsd": 5,
            },
        }
        prompts = [{
            "content": "Synthetic portable smoke prompt",
            "aiCredits": 1,
            "usageSource": "otel_trace",
        }]
        with patch.object(
            release_smoke,
            "urlopen",
            side_effect=[FakeResponse(200, session), FakeResponse(200, prompts)],
        ):
            copilot_value.wait_for_app_session("session-test", timeout_seconds=1)

        prompts.insert(0, {
            "content": "Synthetic portable smoke prompt from late-arriving earlier span",
            "aiCredits": 1,
            "usageSource": "otel_trace",
        })
        session.update({
            "startedAt": "2026-08-05T09:59:59Z",
            "durationSeconds": 2,
            "aiCostUsd": 0.02,
            "aiCredits": 2,
            "tokens": {"input": 200, "output": 100},
        })
        session["usage"] = {"elapsedSeconds": 2}
        session["scenarioResult"]["estimatedAiAssistedTotalCostUsd"] = 2.02
        with patch.object(
            release_smoke,
            "urlopen",
            side_effect=[FakeResponse(200, session), FakeResponse(200, prompts)],
        ):
            copilot_value.wait_for_app_session(
                "session-test",
                expected_prompt_contents=(
                    "Synthetic portable smoke prompt from late-arriving earlier span",
                    "Synthetic portable smoke prompt",
                ),
                expected_ai_credits=2,
                expected_input_tokens=200,
                expected_output_tokens=100,
                expected_started_milliseconds=round(
                    datetime.fromisoformat(session["startedAt"].replace("Z", "+00:00")).timestamp() * 1000
                ),
                timeout_seconds=1,
            )

    def test_waits_for_session_index_to_converge_after_late_earlier_span(self) -> None:
        stale_session = {
            "experiment": "session-test",
            "startedAt": "2026-08-05T10:00:00Z",
            "durationSeconds": 1,
            "aiCostUsd": 0.02,
            "aiCredits": 2,
            "tokens": {"input": 200, "output": 100},
            "sourceEvidenceComplete": False,
            "retainedSourceCharacters": 0,
            "source": {"source": "otel_only"},
            "usage": {"elapsedSeconds": 2},
            "modelingStatus": "invalid",
            "scenarioResult": None,
        }
        corrected_session = {
            **stale_session,
            "startedAt": "2026-08-05T09:59:59Z",
            "durationSeconds": 2,
            "modelingStatus": "available",
            "scenarioResult": {
                "estimatedManualLaborCostUsd": 8,
                "estimatedAiAssistedLaborCostUsd": 2,
                "estimatedAiAssistedTotalCostUsd": 2.02,
                "estimatedGrossCostSavingsUsd": 5,
            },
        }
        prompts = [
            {
                "content": "Synthetic portable smoke prompt from late-arriving earlier span",
                "aiCredits": 1,
                "usageSource": "otel_trace",
            },
            {
                "content": "Synthetic portable smoke prompt",
                "aiCredits": 1,
                "usageSource": "otel_trace",
            },
        ]
        expected_started_milliseconds = round(
            datetime.fromisoformat(
                corrected_session["startedAt"].replace("Z", "+00:00")
            ).timestamp() * 1000
        )
        with (
            patch.object(
                release_smoke,
                "urlopen",
                side_effect=[
                    FakeResponse(200, stale_session),
                    FakeResponse(200, prompts),
                    FakeResponse(200, corrected_session),
                    FakeResponse(200, prompts),
                ],
            ),
            patch.object(release_smoke.time, "monotonic", side_effect=[0, 0, 0]),
            patch.object(release_smoke.time, "sleep") as sleep,
        ):
            release_smoke.wait_for_app_session(
                "session-test",
                expected_prompt_contents=tuple(prompt["content"] for prompt in prompts),
                expected_ai_credits=2,
                expected_input_tokens=200,
                expected_output_tokens=100,
                expected_started_milliseconds=expected_started_milliseconds,
                timeout_seconds=1,
            )
        sleep.assert_called_once_with(0.5)

    def test_times_out_when_late_span_session_bounds_never_reconcile(self) -> None:
        stale_session = {
            "experiment": "session-test",
            "startedAt": "2026-08-05T10:00:00Z",
            "durationSeconds": 1,
            "aiCostUsd": 0.02,
            "aiCredits": 2,
            "tokens": {"input": 200, "output": 100},
            "sourceEvidenceComplete": False,
            "retainedSourceCharacters": 0,
            "source": {"source": "otel_only"},
            "usage": {"elapsedSeconds": 2},
            "modelingStatus": "invalid",
            "scenarioResult": None,
        }
        prompts = [
            {
                "content": "Synthetic portable smoke prompt from late-arriving earlier span",
                "aiCredits": 1,
                "usageSource": "otel_trace",
            },
            {
                "content": "Synthetic portable smoke prompt",
                "aiCredits": 1,
                "usageSource": "otel_trace",
            },
        ]
        with (
            patch.object(
                release_smoke,
                "urlopen",
                side_effect=[FakeResponse(200, stale_session), FakeResponse(200, prompts)],
            ),
            patch.object(release_smoke.time, "monotonic", side_effect=[0, 0, 2]),
            patch.object(release_smoke.time, "sleep"),
            self.assertRaisesRegex(
                copilot_value.CopilotValueError,
                "did not expand to late-arriving earlier evidence",
            ),
        ):
            release_smoke.wait_for_app_session(
                "session-test",
                expected_prompt_contents=tuple(prompt["content"] for prompt in prompts),
                expected_ai_credits=2,
                expected_input_tokens=200,
                expected_output_tokens=100,
                expected_started_milliseconds=1_754_388_799_000,
                timeout_seconds=1,
            )

    def test_waits_for_available_portfolio_modeling(self) -> None:
        overview = {
            "modelingStatus": "available",
            "totals": {"estimatedManualMinutes": 2.5},
            "sessions": [{"experiment": "session-test", "modelingStatus": "available"}],
        }
        with patch.object(
            release_smoke,
            "urlopen",
            return_value=FakeResponse(200, overview),
        ):
            release_smoke.wait_for_app_overview("session-test", timeout_seconds=1)


class InstalledArtifactSmokeTests(unittest.TestCase):
    def test_validates_extracted_release_without_external_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "release"
            create_release_tree(root)

            def captured(command, *, cwd=None) -> str:
                if any("process.versions.node" in str(part) for part in command):
                    return "22\n"
                if "diff" in command:
                    return json.dumps({
                        "filesModified": 1,
                        "filesAdded": 0,
                        "charactersAdded": 2,
                        "charactersRemoved": 1,
                    })
                return json.dumps({"sessions": 2, "codingToolCalls": 3})

            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value.shutil, "which", return_value="node"),
                patch.object(copilot_value, "run_checked") as run_checked,
                patch.object(copilot_value, "run_captured", side_effect=captured),
            ):
                self.assertEqual(
                    copilot_value.command_smoke_installation(
                        argparse.Namespace(telemetry=False)
                    ),
                    0,
                )
            self.assertEqual(run_checked.call_count, 1)

    def test_installed_release_exercises_live_telemetry_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "release"
            create_release_tree(root)

            def captured(command, *, cwd=None) -> str:
                if any("process.versions.node" in str(part) for part in command):
                    return "22\n"
                if "diff" in command:
                    return json.dumps({
                        "filesModified": 1,
                        "filesAdded": 0,
                        "charactersAdded": 2,
                        "charactersRemoved": 1,
                    })
                return json.dumps({"sessions": 2, "codingToolCalls": 3})

            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value.shutil, "which", return_value="node"),
                patch.object(copilot_value, "run_checked"),
                patch.object(copilot_value, "run_captured", side_effect=captured),
                patch.object(copilot_value, "endpoint_healthy", return_value=True),
                patch.object(copilot_value, "run_telemetry_smoke", return_value="session-test") as telemetry,
            ):
                self.assertEqual(
                    copilot_value.command_smoke_installation(
                        argparse.Namespace(telemetry=True)
                    ),
                    0,
                )
            telemetry.assert_called_once_with(root)


class BundleSmokeOrchestrationTests(unittest.TestCase):
    def test_smokes_pyz_without_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            (root / "package.json").write_text(
                json.dumps({"version": "1.0.0"}), encoding="utf-8"
            )
            bundle = root / "release.pyz"
            bundle.write_text("application", encoding="utf-8")
            extraction = Path(temporary_directory) / "extraction"
            arguments = argparse.Namespace(
                bundle=str(bundle),
                skip_build=True,
                skip_telemetry=True,
                keep_extracted=False,
            )
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value.tempfile, "mkdtemp", return_value=str(extraction)),
                patch.object(copilot_value, "run_checked") as run_checked,
            ):
                self.assertEqual(copilot_value.command_smoke_bundle(arguments), 0)
            self.assertEqual(run_checked.call_count, 2)
            self.assertFalse(extraction.exists())

    def test_smokes_zip_with_telemetry_and_restores_displaced_containers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            (root / "package.json").write_text(
                json.dumps({"version": "1.0.0"}), encoding="utf-8"
            )
            bundle = root / "release.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("scripts/copilot_value.py", "# extracted CLI\n")
            extraction = Path(temporary_directory) / "extraction"
            runtime = Mock()

            def docker_run(arguments, **_kwargs):
                output = "container-a\n" if arguments[0] == "ps" else ""
                return subprocess.CompletedProcess([], 0, stdout=output)

            runtime.run.side_effect = docker_run
            arguments = argparse.Namespace(
                bundle=str(bundle),
                skip_build=True,
                skip_telemetry=False,
                keep_extracted=True,
            )
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value.tempfile, "mkdtemp", return_value=str(extraction)),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "run_checked") as run_checked,
                patch.object(copilot_value, "compose") as compose,
                patch.object(copilot_value.time, "sleep"),
            ):
                self.assertEqual(copilot_value.command_smoke_bundle(arguments), 0)
            self.assertGreaterEqual(run_checked.call_count, 3)
            compose.assert_called_once_with(
                runtime,
                extraction / "repository" / ".copilot-value",
                ("--profile", "grafana", "down", "--volumes", "--remove-orphans"),
                capture=True,
                check=False,
            )
            self.assertIn(call(("start", "container-a"), capture=True, check=False), runtime.run.call_args_list)
            self.assertTrue(extraction.exists())
            shutil.rmtree(extraction)

    def test_bundle_smoke_requires_an_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "package.json").write_text(
                json.dumps({"version": "1.0.0"}), encoding="utf-8"
            )
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                self.assertRaisesRegex(copilot_value.CopilotValueError, "Bundle not found"),
            ):
                copilot_value.command_smoke_bundle(argparse.Namespace(
                    bundle="missing.pyz",
                    skip_build=True,
                    skip_telemetry=True,
                    keep_extracted=False,
                ))


if __name__ == "__main__":
    unittest.main()