from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import URLError
import zipfile

from scripts import copilot_value, docker_runtime


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


class DockerRuntimeTests(unittest.TestCase):
    def test_discovers_direct_wsl_optional_and_missing_docker(self) -> None:
        with patch.object(docker_runtime.shutil, "which", side_effect=lambda name: "docker-bin" if name == "docker" else None):
            direct = copilot_value.DockerRuntime()
        self.assertTrue(direct.available)
        self.assertEqual(direct._prefix, ["docker-bin"])

        with (
            patch.object(docker_runtime.os, "name", "nt"),
            patch.object(docker_runtime.shutil, "which", side_effect=lambda name: "wsl.exe" if name == "wsl.exe" else None),
        ):
            wsl = copilot_value.DockerRuntime()
        self.assertEqual(wsl._prefix, ["wsl.exe", "--", "docker"])

        with patch.object(docker_runtime.shutil, "which", return_value=None):
            optional = copilot_value.DockerRuntime(required=False)
            self.assertFalse(optional.available)
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "required"):
                copilot_value.DockerRuntime()

    def test_runs_docker_commands_and_reports_failures(self) -> None:
        runtime = copilot_value.DockerRuntime.__new__(copilot_value.DockerRuntime)
        runtime._prefix = ["docker"]
        runtime._wsl = None
        success = subprocess.CompletedProcess(["docker"], 0, stdout="ok")
        with patch.object(docker_runtime.subprocess, "run", return_value=success) as run:
            result = runtime.run(("ps",), capture=True, input_text="input")
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(run.call_args.args[0], ["docker", "ps"])
        self.assertEqual(run.call_args.kwargs["input"], "input")

        failure = subprocess.CompletedProcess(["docker"], 4, stdout="")
        with patch.object(docker_runtime.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "exit code 4"):
                runtime.run(("compose", "up"))
            self.assertEqual(runtime.run(("compose", "up"), check=False).returncode, 4)

        runtime._prefix = []
        with self.assertRaisesRegex(copilot_value.CopilotValueError, "required"):
            runtime.run(("ps",))

    def test_requires_compose_and_converts_wsl_paths(self) -> None:
        runtime = copilot_value.DockerRuntime.__new__(copilot_value.DockerRuntime)
        runtime._prefix = ["docker"]
        runtime._wsl = None
        runtime.run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=""))
        runtime.require_compose()
        self.assertEqual(
            runtime.run.call_args_list,
            [call(("version",), capture=True), call(("compose", "version"), capture=True)],
        )

        runtime._wsl = "wsl.exe"
        converted = subprocess.CompletedProcess([], 0, stdout="/mnt/c/repository\n")
        with patch.object(docker_runtime.subprocess, "run", return_value=converted):
            self.assertEqual(runtime.docker_path(Path("repository")), "/mnt/c/repository")
        failed = subprocess.CompletedProcess([], 1, stdout="")
        with patch.object(docker_runtime.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "Could not convert"):
                runtime.docker_path(Path("repository"))

    def test_non_wsl_keepalive_operations_are_noops(self) -> None:
        runtime = copilot_value.DockerRuntime.__new__(copilot_value.DockerRuntime)
        runtime._prefix = ["docker"]
        runtime._wsl = None
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime.keep_wsl_alive(root)
            runtime.release_wsl_keepalive(root)
            self.assertFalse((root / "data" / "wsl-keepalive").exists())


class DeploymentHelperTests(unittest.TestCase):
    def test_detects_execution_archives_and_install_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "release.pyz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("__main__.py", "pass\n")
            with patch.object(sys, "argv", [str(archive_path)]):
                self.assertEqual(copilot_value.execution_archive(), archive_path.resolve())
                with patch.object(Path, "cwd", return_value=root):
                    self.assertEqual(copilot_value.install_root(), (root / ".copilot-value").resolve())
            with patch.object(sys, "argv", [str(root / "script.py")]):
                self.assertIsNone(copilot_value.execution_archive())

    def test_rejects_archive_path_traversal_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "unsafe.pyz"
            install = root / "install"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", "unsafe")
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "unsafe path"):
                copilot_value.stage_archive(archive_path, install)
            self.assertEqual(list(install.glob(".upgrade-*")), [])

    def test_removes_files_and_directories_and_retries_permission_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            file_path = root / "file.txt"
            file_path.write_text("value", encoding="utf-8")
            copilot_value.remove_path(file_path)
            self.assertFalse(file_path.exists())
            directory = root / "directory"
            directory.mkdir()
            (directory / "child").write_text("value", encoding="utf-8")
            copilot_value.remove_path(directory)
            self.assertFalse(directory.exists())

        operation = Mock(side_effect=[PermissionError("busy"), None])
        with patch.object(copilot_value.time, "sleep") as sleep:
            copilot_value.retry_file_operation(operation, "test path", timeout_seconds=2)
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(0.25)

        with (
            patch.object(copilot_value.time, "monotonic", side_effect=[0, 1]),
            self.assertRaisesRegex(copilot_value.CopilotValueError, "Timed out"),
        ):
            copilot_value.retry_file_operation(
                Mock(side_effect=PermissionError("busy")), "test path", timeout_seconds=0
            )

    def test_compose_uses_installation_paths_and_detects_running_services(self) -> None:
        runtime = Mock()
        runtime.docker_path.side_effect = lambda path: f"docker:{path.name}"
        runtime.run.return_value = subprocess.CompletedProcess([], 0, stdout="worker\n")
        root = Path("installation")

        result = copilot_value.compose(runtime, root, ("ps",), capture=True, check=False)
        self.assertEqual(result.stdout, "worker\n")
        runtime.run.assert_called_with(
            ("compose", "--env-file", "docker:.env", "-f", "docker:compose.yaml", "ps"),
            capture=True,
            check=False,
            input_text=None,
        )
        self.assertTrue(copilot_value.existing_installation_running(runtime, root))
        runtime.run.return_value = subprocess.CompletedProcess([], 1, stdout="")
        self.assertFalse(copilot_value.existing_installation_running(runtime, root))


class HealthAndGrafanaTests(unittest.TestCase):
    def test_health_helpers_handle_success_failure_and_timeout(self) -> None:
        with patch.object(copilot_value, "urlopen", return_value=FakeResponse(204)):
            self.assertTrue(copilot_value.endpoint_healthy("http://health"))
        with patch.object(copilot_value, "urlopen", side_effect=URLError("offline")):
            self.assertFalse(copilot_value.endpoint_healthy("http://health"))

        with (
            patch.object(copilot_value, "endpoint_healthy", side_effect=[False, True]),
            patch.object(copilot_value.time, "monotonic", side_effect=[0, 0, 0]),
            patch.object(copilot_value.time, "sleep") as sleep,
        ):
            copilot_value.wait_for_endpoint("service", "http://health", timeout_seconds=2)
        sleep.assert_called_once_with(0.5)

        with (
            patch.object(copilot_value, "endpoint_healthy", return_value=False),
            patch.object(copilot_value.time, "monotonic", side_effect=[0, 1]),
            self.assertRaisesRegex(copilot_value.CopilotValueError, "did not become healthy"),
        ):
            copilot_value.wait_for_endpoint("service", "http://health", timeout_seconds=0)

    def test_grafana_authentication_and_password_reconciliation(self) -> None:
        with patch.object(copilot_value, "urlopen", return_value=FakeResponse(200)):
            self.assertTrue(copilot_value.grafana_authenticates("password", "http://grafana/"))
        with patch.object(copilot_value, "urlopen", side_effect=OSError("offline")):
            self.assertFalse(copilot_value.grafana_authenticates("password"))

        runtime = Mock()
        root = Path("installation")
        with patch.object(copilot_value, "grafana_authenticates", return_value=True):
            self.assertIsNone(copilot_value.reconcile_grafana_password(runtime, root, "fixture-passphrase"))

        with (
            patch.object(copilot_value, "grafana_authenticates", side_effect=[False, True]),
            patch.object(copilot_value, "compose") as compose,
            patch("builtins.print") as print_output,
        ):
            self.assertIsNone(copilot_value.reconcile_grafana_password(runtime, root, "fixture-passphrase"))
        self.assertIn("reset-admin-password", compose.call_args.args[2])
        self.assertEqual(compose.call_args.kwargs["input_text"], "fixture-passphrase\n")
        self.assertNotIn("fixture-passphrase", str(print_output.call_args_list))

        with (
            patch.object(copilot_value, "grafana_authenticates", return_value=False),
            patch.object(copilot_value, "compose"),
            self.assertRaisesRegex(copilot_value.CopilotValueError, "authentication still failed"),
        ):
            copilot_value.reconcile_grafana_password(runtime, root, "fixture-passphrase")

    def test_grafana_command_prompts_without_printing_or_persisting_credentials(self) -> None:
        runtime = Mock()
        with (
            patch.object(copilot_value, "install_root", return_value=Path("installation")),
            patch.object(copilot_value.sys.stdin, "isatty", return_value=True),
            patch.object(copilot_value.getpass, "getpass", return_value="fixture-passphrase"),
            patch.object(copilot_value, "assert_docker_install"),
            patch.object(copilot_value, "DockerRuntime", return_value=runtime),
            patch.object(copilot_value, "compose") as compose,
            patch.object(copilot_value, "wait_for_endpoint"),
            patch.object(copilot_value, "reconcile_grafana_password") as reconcile,
            patch.object(copilot_value, "write_environment") as write_environment,
            patch("builtins.print") as print_output,
        ):
            self.assertEqual(copilot_value.command_grafana(argparse.Namespace()), 0)
        compose.assert_called_once()
        self.assertEqual(compose.call_args.args[2][:2], ("-f", "-"))
        environment = json.loads(compose.call_args.kwargs["input_text"])["services"]["grafana"]["environment"]
        self.assertEqual(environment["GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION"], "false")
        self.assertGreaterEqual(len(environment["GF_SECURITY_ADMIN_PASSWORD"]), 32)
        reconcile.assert_called_once_with(runtime, Path("installation"), "fixture-passphrase")
        write_environment.assert_not_called()
        self.assertNotIn("fixture-passphrase", str(print_output.call_args_list))

    def test_grafana_requires_interactive_strong_matching_passwords(self) -> None:
        cases = (
            (False, [], "interactive terminal"),
            (True, ["short"], "at least 16"),
            (True, ["fixture-passphrase", "different-passphrase"], "did not match"),
        )
        for interactive, answers, message in cases:
            with (
                self.subTest(interactive=interactive, message=message),
                patch.object(copilot_value.sys.stdin, "isatty", return_value=interactive),
                patch.object(copilot_value.getpass, "getpass", side_effect=answers),
                patch.object(copilot_value, "start_grafana_installation") as start,
                self.assertRaisesRegex(copilot_value.CopilotValueError, message),
            ):
                copilot_value.command_grafana(argparse.Namespace())
            start.assert_not_called()


class ConfigurationEdgeTests(unittest.TestCase):
    def test_jsonc_parser_preserves_comment_markers_in_strings(self) -> None:
        source = r'''{
  // remove this comment
  "url": "https://example.test/path//still-string",
  "escaped": "quote: \" /* still string */",
  /* remove
     this block */
  "items": [1, 2,],
}
'''
        self.assertEqual(
            json.loads(copilot_value.strip_jsonc(source)),
            {
                "url": "https://example.test/path//still-string",
                "escaped": 'quote: " /* still string */',
                "items": [1, 2],
            },
        )

    def test_discovers_default_vscode_settings_on_each_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            host_path_class = type(home)
            with (
                patch.object(copilot_value.os, "name", "nt"),
                patch.object(copilot_value, "Path", host_path_class),
                patch.dict(copilot_value.os.environ, {"APPDATA": str(home)}, clear=False),
                patch.object(host_path_class, "home", side_effect=AssertionError("APPDATA should avoid home lookup")),
            ):
                self.assertEqual(
                    copilot_value.vscode_user_settings_paths()[0],
                    home / "Code" / "User" / "settings.json",
                )
            with (
                patch.object(copilot_value.os, "name", "posix"),
                patch.object(copilot_value.sys, "platform", "darwin"),
                patch.object(copilot_value, "Path", host_path_class),
                patch.object(host_path_class, "home", return_value=home),
            ):
                self.assertEqual(
                    copilot_value.vscode_user_settings_paths()[0],
                    home / "Library" / "Application Support" / "Code" / "User" / "settings.json",
                )
            with (
                patch.object(copilot_value.os, "name", "posix"),
                patch.object(copilot_value.sys, "platform", "linux"),
                patch.object(copilot_value, "Path", host_path_class),
                patch.dict(copilot_value.os.environ, {"XDG_CONFIG_HOME": str(home)}, clear=False),
                patch.object(host_path_class, "home", side_effect=AssertionError("XDG_CONFIG_HOME should avoid home lookup")),
            ):
                self.assertEqual(
                    copilot_value.vscode_user_settings_paths()[0],
                    home / "Code" / "User" / "settings.json",
                )

    def test_vscode_settings_creation_validation_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = root / "Code" / "User" / "settings.json"
            backups = root / "backups"
            copilot_value.configure_vscode_user_settings(settings, backups)
            created = json.loads(settings.read_text(encoding="utf-8"))
            self.assertTrue(created["chat.agentHost.otel.enabled"])
            first_content = settings.read_text(encoding="utf-8")
            copilot_value.configure_vscode_user_settings(settings, backups)
            self.assertEqual(settings.read_text(encoding="utf-8"), first_content)

            settings.write_text("{ invalid", encoding="utf-8")
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "Fix the JSONC"):
                copilot_value.configure_vscode_user_settings(settings, backups)
            settings.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "must be a JSON object"):
                copilot_value.configure_vscode_user_settings(settings, backups)

    def test_local_config_reuses_files_and_rejects_nonempty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_root = root / "config"
            config_root.mkdir()
            local = config_root / "value-model.local.json"
            local.write_text("{}\n", encoding="utf-8")
            self.assertEqual(copilot_value.write_local_config(root), local)
            local.unlink()
            local.mkdir()
            (local / "unexpected").write_text("value", encoding="utf-8")
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "nonempty directory"):
                copilot_value.write_local_config(root)


class InstallationCommandTests(unittest.TestCase):
    def test_archive_upgrade_rejects_invalid_feed_before_replacing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            root = repository / ".copilot-value"
            (root / "docker").mkdir(parents=True)
            (root / "docker" / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (root / "docker" / ".env").write_text(
                "GRAFANA_ADMIN_PASSWORD=existing\n", encoding="utf-8"
            )
            archive_path = Path(temporary_directory) / "release.pyz"
            archive_path.write_text("archive", encoding="utf-8")
            staging = root / ".upgrade-test"
            arguments = SimpleNamespace(
                repository_root=str(repository),
                skip_docker_check=False,
                skip_vscode_settings=True,
                no_start=False,
                poll_seconds=5,
                npm_registry="https://user:secret@example.com/npm/",
                pip_index_url=None,
            )
            with (
                patch.object(copilot_value, "execution_archive", return_value=archive_path),
                patch.object(copilot_value, "stage_archive", return_value=staging),
                patch.object(copilot_value, "deploy_staged_archive") as deploy,
                patch.object(copilot_value, "compose") as compose,
                self.assertRaisesRegex(copilot_value.CopilotValueError, "credential-free HTTPS"),
            ):
                copilot_value.command_install(arguments)
            deploy.assert_not_called()
            compose.assert_not_called()

    def test_archive_upgrade_stops_and_recreates_the_existing_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            root = repository / ".copilot-value"
            (root / "config").mkdir(parents=True)
            (root / "docker").mkdir()
            (root / "docker" / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (root / "docker" / ".env").write_text(
                "GRAFANA_ADMIN_PASSWORD=existing\n"
                "NPM_REGISTRY=https://registry.example/npm/\n"
                "PIP_INDEX_URL=https://registry.example/pypi/simple\n",
                encoding="utf-8",
            )
            (root / "config" / "value-model.example.json").write_text(
                json.dumps({"benchmark": {"acknowledgedAssumptions": False}}), encoding="utf-8"
            )
            archive_path = Path(temporary_directory) / "release.pyz"
            archive_path.write_text("archive", encoding="utf-8")
            staging = root / ".upgrade-test"
            runtime = Mock()
            runtime.docker_path.side_effect = lambda path: str(path)
            arguments = SimpleNamespace(
                repository_root=str(repository),
                skip_docker_check=False,
                skip_vscode_settings=True,
                no_start=False,
                poll_seconds=5,
            )
            with (
                patch.object(copilot_value, "execution_archive", return_value=archive_path),
                patch.object(copilot_value, "stage_archive", return_value=staging),
                patch.object(copilot_value, "deploy_staged_archive") as deploy,
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "running_services", return_value={*copilot_value.SERVICES, "grafana"}),
                patch.object(copilot_value, "compose") as compose,
                patch.object(copilot_value, "vscode_workspace_storage_paths", return_value=[]),
                patch.object(copilot_value, "start_installation", return_value=0) as start,
                patch.object(copilot_value, "start_grafana_installation", return_value=0) as start_grafana,
            ):
                self.assertEqual(copilot_value.command_install(arguments), 0)
            self.assertEqual(deploy.call_count, 1)
            self.assertEqual(deploy.call_args.args[0], staging)
            self.assertTrue(os.path.samefile(deploy.call_args.args[1], root))
            down_call = next(
                item
                for item in compose.call_args_list
                if item.args[2] == ("--profile", "grafana", "down")
            )
            self.assertTrue(os.path.samefile(down_call.args[1], root))
            self.assertEqual(start.call_count, 1)
            self.assertTrue(os.path.samefile(start.call_args.args[0], root))
            self.assertTrue(start.call_args.kwargs["force_recreate"])
            start_grafana.assert_called_once_with(root.resolve())
            environment = copilot_value.read_environment(root / "docker" / ".env")
            self.assertNotIn("GRAFANA_ADMIN_PASSWORD", environment)
            self.assertEqual(environment["NPM_REGISTRY"], "https://registry.example/npm/")
            self.assertEqual(environment["PIP_INDEX_URL"], "https://registry.example/pypi/simple")

    def test_install_rejects_invalid_source_and_repository_relationship(self) -> None:
        arguments = SimpleNamespace(
            repository_root=None,
            skip_docker_check=True,
            skip_vscode_settings=True,
            no_start=True,
            poll_seconds=5,
        )
        with (
            patch.object(copilot_value, "execution_archive", return_value=None),
            patch.object(copilot_value, "install_root", return_value=Path("source-checkout")),
            self.assertRaisesRegex(copilot_value.CopilotValueError, "Run the release as a .pyz"),
        ):
            copilot_value.command_install(arguments)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".copilot-value"
            root.mkdir()
            arguments.repository_root = str(Path(temporary_directory) / "other")
            with (
                patch.object(copilot_value, "execution_archive", return_value=None),
                patch.object(copilot_value, "install_root", return_value=root),
                self.assertRaisesRegex(copilot_value.CopilotValueError, "directly under repository root"),
            ):
                copilot_value.command_install(arguments)

    def test_start_stop_and_status_commands_cover_service_health(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".docker-install").write_text("docker\n", encoding="utf-8")
            runtime = Mock()
            running = subprocess.CompletedProcess([], 0, stdout="worker\napp\n")
            with (
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "compose", return_value=running) as compose,
                patch.object(copilot_value, "wait_for_endpoint") as wait,
            ):
                self.assertEqual(copilot_value.start_installation(root, force_recreate=True), 0)
            self.assertIn("--force-recreate", compose.call_args_list[0].args[2])
            self.assertEqual(wait.call_count, len(copilot_value.HEALTH_ENDPOINTS))

            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "compose") as compose,
            ):
                self.assertEqual(copilot_value.command_stop(argparse.Namespace()), 0)
            compose.assert_called_once_with(runtime, root, ("--profile", "grafana", "down"))
            runtime.release_wsl_keepalive.assert_called_with(root)

            runtime.reset_mock()
            all_services = subprocess.CompletedProcess([], 0, stdout="\n".join(copilot_value.SERVICES))
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "compose", return_value=all_services),
                patch.object(copilot_value, "endpoint_healthy", return_value=True),
            ):
                self.assertEqual(copilot_value.command_status(argparse.Namespace()), 0)

            missing_services = subprocess.CompletedProcess([], 0, stdout="worker\n")
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "compose", return_value=missing_services),
                patch.object(copilot_value, "endpoint_healthy", return_value=False),
            ):
                self.assertEqual(copilot_value.command_status(argparse.Namespace()), 1)

    def test_command_wrappers_and_parser_report_failures(self) -> None:
        success = subprocess.CompletedProcess([], 0, stdout="output", stderr="")
        with patch.object(copilot_value.subprocess, "run", return_value=success):
            copilot_value.run_checked(("tool", "ok"), cwd=Path("working"))
            self.assertEqual(copilot_value.run_captured(("tool", "read")), "output")

        failure = subprocess.CompletedProcess([], 3, stdout="", stderr="details")
        with patch.object(copilot_value.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "exit code 3"):
                copilot_value.run_checked(("tool", "fail"))
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "details"):
                copilot_value.run_captured(("tool", "fail"))

        parser = copilot_value.create_parser()
        parsed = parser.parse_args([
            "install",
            "--poll-seconds",
            "7",
            "--npm-registry",
            "https://registry.example/npm/",
            "--pip-index-url",
            "https://registry.example/pypi/simple",
            "--no-start",
        ])
        self.assertIs(parsed.handler, copilot_value.command_install)
        self.assertEqual(parsed.poll_seconds, 7)
        self.assertEqual(parsed.npm_registry, "https://registry.example/npm/")
        self.assertEqual(parsed.pip_index_url, "https://registry.example/pypi/simple")
        self.assertTrue(parsed.no_start)
        restart = parser.parse_args(["restart"])
        self.assertIs(restart.handler, copilot_value.command_restart)
        logs = parser.parse_args([
            "logs", "worker", "app", "--tail", "all", "--timestamps", "--follow"
        ])
        self.assertIs(logs.handler, copilot_value.command_logs)
        self.assertEqual(logs.services, ["worker", "app"])
        self.assertEqual(logs.tail, "all")
        self.assertTrue(logs.timestamps)
        self.assertTrue(logs.follow)
        clean_data = parser.parse_args(["clean-data", "--yes", "--no-start"])
        self.assertIs(clean_data.handler, copilot_value.command_clean_data)
        self.assertTrue(clean_data.yes)
        self.assertTrue(clean_data.no_start)

        with patch.object(copilot_value, "create_parser") as create_parser:
            create_parser.return_value.parse_args.return_value = SimpleNamespace(
                handler=Mock(return_value=17)
            )
            self.assertEqual(copilot_value.main(), 17)

    def test_docker_install_guard_and_command_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(copilot_value.CopilotValueError, "Run install first"):
                copilot_value.assert_docker_install(root)
            (root / ".docker-install").write_text("docker\n", encoding="utf-8")
            copilot_value.assert_docker_install(root)
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "start_installation", return_value=9) as start,
            ):
                self.assertEqual(copilot_value.command_start(argparse.Namespace()), 9)
            start.assert_called_once_with(root)

    def test_restart_stops_then_starts_the_installation(self) -> None:
        arguments = argparse.Namespace()
        with (
            patch.object(copilot_value, "install_root", return_value=Path("installation")),
            patch.object(copilot_value, "assert_docker_install"),
            patch.object(copilot_value, "DockerRuntime"),
            patch.object(copilot_value, "running_services", return_value={"grafana"}),
            patch.object(copilot_value, "command_stop", return_value=0) as stop,
            patch.object(copilot_value, "command_start", return_value=7) as start,
            patch.object(copilot_value, "start_grafana_installation") as start_grafana,
        ):
            self.assertEqual(copilot_value.command_restart(arguments), 7)
        stop.assert_called_once_with(arguments)
        start.assert_called_once_with(arguments)
        start_grafana.assert_called_once_with(Path("installation"))

    def test_logs_support_service_filters_tail_timestamps_and_follow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".docker-install").write_text("docker\n", encoding="utf-8")
            runtime = Mock()
            arguments = argparse.Namespace(
                services=["worker", "app"], tail="50", timestamps=True, follow=True
            )
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "compose") as compose,
            ):
                self.assertEqual(copilot_value.command_logs(arguments), 0)
            runtime.keep_wsl_alive.assert_called_once_with(root)
            compose.assert_called_once_with(
                runtime,
                root,
                ("logs", "--tail", "50", "--timestamps", "--follow", "worker", "app"),
            )
        self.assertEqual(copilot_value.log_tail_value("all"), "all")
        self.assertEqual(copilot_value.log_tail_value("25"), "25")
        for invalid in ("0", "-1", "invalid"):
            with self.assertRaises(argparse.ArgumentTypeError):
                copilot_value.log_tail_value(invalid)

    def test_clean_data_requires_confirmation_and_restarts_only_running_stacks(self) -> None:
        with self.assertRaisesRegex(copilot_value.CopilotValueError, "--yes"):
            copilot_value.command_clean_data(argparse.Namespace(yes=False, no_start=False))

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".docker-install").write_text("docker\n", encoding="utf-8")
            runtime = Mock()
            arguments = argparse.Namespace(yes=True, no_start=False)
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "running_services", return_value={*copilot_value.SERVICES, "grafana"}),
                patch.object(copilot_value, "compose") as compose,
                patch.object(copilot_value, "start_installation", return_value=9) as start,
                patch.object(copilot_value, "start_grafana_installation") as start_grafana,
            ):
                self.assertEqual(copilot_value.command_clean_data(arguments), 0)
            compose.assert_called_once_with(
                runtime,
                root,
                ("--profile", "grafana", "down", "--volumes", "--remove-orphans"),
            )
            runtime.release_wsl_keepalive.assert_called_once_with(root)
            start.assert_called_once_with(root)
            start_grafana.assert_called_once_with(root)

            runtime.reset_mock()
            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "running_services", return_value=set()),
                patch.object(copilot_value, "compose"),
                patch.object(copilot_value, "start_installation") as start,
                patch.object(copilot_value, "start_grafana_installation") as start_grafana,
            ):
                self.assertEqual(
                    copilot_value.command_clean_data(
                        argparse.Namespace(yes=True, no_start=True)
                    ),
                    0,
                )
            start.assert_not_called()
            start_grafana.assert_not_called()

    def test_start_reports_when_worker_never_becomes_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".docker-install").write_text("docker\n", encoding="utf-8")
            runtime = Mock()
            running = subprocess.CompletedProcess([], 0, stdout="worker\n")
            with (
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "compose", return_value=running) as compose,
                patch.object(copilot_value, "wait_for_endpoint"),
                patch.object(copilot_value.time, "monotonic", side_effect=[0, 100]),
                self.assertRaisesRegex(copilot_value.CopilotValueError, "worker did not become healthy"),
            ):
                copilot_value.start_installation(root)
            self.assertEqual(compose.call_args_list[-1].args[2], ("ps",))


if __name__ == "__main__":
    unittest.main()