import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts import copilot_value, docker_runtime


class CopilotValueCliTests(unittest.TestCase):
    def test_initializes_repository_files_with_structured_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "repository"
            install = repository / ".copilot-value"
            (install / "config").mkdir(parents=True)
            (install / "config" / "value-model.example.json").write_text(
                json.dumps({"benchmark": {"acknowledgedAssumptions": False}}),
                encoding="utf-8",
            )
            (repository / ".gitignore").write_text("dist/\n", encoding="utf-8")

            copilot_value.write_gitignore(repository)
            config_path = copilot_value.write_local_config(install)

            self.assertIn(".copilot-value/", (repository / ".gitignore").read_text(encoding="utf-8").splitlines())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(config["benchmark"]["acknowledgedAssumptions"])

    def test_repository_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            (repository / ".gitignore").write_text(".copilot-value/\n", encoding="utf-8")

            copilot_value.write_gitignore(repository)
            copilot_value.write_gitignore(repository)

            entries = (repository / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertEqual(entries.count(".copilot-value/"), 1)

    def test_reads_compose_environment_without_interpreting_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment_path = Path(temporary_directory) / ".env"
            environment_path.write_text(
                "# generated\nGRAFANA_ADMIN_PASSWORD=a=b=c\nSESSION_POLL_SECONDS=5\n",
                encoding="utf-8",
            )

            environment = copilot_value.read_environment(environment_path)

            self.assertEqual(environment["GRAFANA_ADMIN_PASSWORD"], "a=b=c")
            self.assertEqual(environment["SESSION_POLL_SECONDS"], "5")

    def test_round_trips_compose_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment_path = Path(temporary_directory) / ".env"
            values = {
                "COMPOSE_PROJECT_NAME": "copilot-value-test",
                "GRAFANA_ADMIN_PASSWORD": "a=b=c",
                "SESSION_POLL_SECONDS": "5",
            }

            copilot_value.write_environment(environment_path, values)

            self.assertEqual(copilot_value.read_environment(environment_path), values)

    def test_does_not_import_host_package_feeds(self) -> None:
        with (
            patch.object(copilot_value.subprocess, "run") as run,
            patch.dict(os.environ, {
                "NPM_CONFIG_REGISTRY": "https://registry.example/npm/",
                "PIP_INDEX_URL": "https://registry.example/pypi/simple",
            }),
        ):
            self.assertEqual(
                copilot_value.resolve_package_feeds({}),
                {
                    "NPM_REGISTRY": copilot_value.PUBLIC_NPM_REGISTRY,
                    "PIP_INDEX_URL": copilot_value.PUBLIC_PYPI_INDEX,
                },
            )
            run.assert_not_called()

    def test_resolves_package_feeds_for_custom_and_public_installations(self) -> None:
        self.assertEqual(
            copilot_value.resolve_package_feeds({}),
            {
                "NPM_REGISTRY": copilot_value.PUBLIC_NPM_REGISTRY,
                "PIP_INDEX_URL": copilot_value.PUBLIC_PYPI_INDEX,
            },
        )
        existing = {
            "NPM_REGISTRY": "https://registry.example/npm/",
            "PIP_INDEX_URL": "https://registry.example/pypi/simple",
        }
        self.assertEqual(copilot_value.resolve_package_feeds(existing), existing)
        self.assertEqual(
            copilot_value.resolve_package_feeds(
                existing,
                npm_registry="https://override.example/npm/",
                pip_index_url="https://override.example/pypi/simple",
            ),
            {
                "NPM_REGISTRY": "https://override.example/npm/",
                "PIP_INDEX_URL": "https://override.example/pypi/simple",
            },
        )

        with self.assertRaisesRegex(copilot_value.CopilotValueError, "credential-free HTTPS"):
            copilot_value.resolve_package_feeds(
                {}, npm_registry="https://user:secret@example.com/npm/"
            )

    def test_install_does_not_mount_the_installation_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            install = repository / ".copilot-value"
            (install / "config").mkdir(parents=True)
            (install / "docker").mkdir()
            (install / "config" / "value-model.example.json").write_text(
                json.dumps({"benchmark": {"acknowledgedAssumptions": False}}),
                encoding="utf-8",
            )
            arguments = SimpleNamespace(
                repository_root=str(repository),
                skip_docker_check=True,
                skip_vscode_settings=True,
                no_start=True,
                poll_seconds=5,
            )

            with (
                patch.object(copilot_value, "execution_archive", return_value=None),
                patch.object(copilot_value, "install_root", return_value=install),
                patch.object(copilot_value, "vscode_workspace_storage_paths", return_value=[]),
            ):
                self.assertEqual(copilot_value.command_install(arguments), 0)

            environment = copilot_value.read_environment(install / "docker" / ".env")
            self.assertNotIn("REPOSITORY_MOUNT_PATH", environment)
            self.assertIn("VSCODE_WORKSPACE_STORAGE_PATH", environment)
            self.assertEqual(environment["NPM_REGISTRY"], copilot_value.PUBLIC_NPM_REGISTRY)
            self.assertEqual(environment["PIP_INDEX_URL"], copilot_value.PUBLIC_PYPI_INDEX)

    def test_configures_vscode_jsonc_without_losing_unrelated_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_path = root / "Code" / "User" / "settings.json"
            backup_root = root / "backups"
            settings_path.parent.mkdir(parents=True)
            original = """{
  // Preserve this setting.
  \"editor.fontSize\": 15,
    \"example.string\": \",}\",
  \"github.copilot.chat.otel.enabled\": false,
}
"""
            settings_path.write_text(original, encoding="utf-8")

            copilot_value.configure_vscode_user_settings(settings_path, backup_root)

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings["editor.fontSize"], 15)
            self.assertEqual(settings["example.string"], ",}")
            for key, value in copilot_value.VSCODE_OTEL_SETTINGS.items():
                self.assertEqual(settings[key], value)
            self.assertEqual(
                (backup_root / "code-settings.jsonc").read_text(encoding="utf-8"),
                original,
            )

    def test_grafana_basic_auth_uses_admin_and_repository_password(self) -> None:
        self.assertEqual(
            copilot_value.grafana_authorization_header("repository-password"),
            "Basic YWRtaW46cmVwb3NpdG9yeS1wYXNzd29yZA==",
        )

    def test_direct_docker_path_uses_host_native_portable_form(self) -> None:
        runtime = copilot_value.DockerRuntime.__new__(copilot_value.DockerRuntime)
        runtime._prefix = ["docker"]
        runtime._wsl = None
        path = Path.cwd() / "repository with spaces"

        converted = runtime.docker_path(path)

        expected = path.resolve().as_posix() if os.name == "nt" else str(path.resolve())
        self.assertEqual(converted, expected)

    def test_discovers_workspace_storage_next_to_vscode_user_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_root = Path(temporary_directory) / "Code" / "User"
            workspace_storage = user_root / "workspaceStorage"
            workspace_storage.mkdir(parents=True)

            discovered = copilot_value.vscode_workspace_storage_paths(
                [user_root / "settings.json"]
            )

            self.assertEqual(discovered, [workspace_storage])

    def test_wsl_docker_keepalive_is_tied_to_repository_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = copilot_value.DockerRuntime.__new__(copilot_value.DockerRuntime)
            runtime._prefix = ["wsl.exe", "--", "docker"]
            runtime._wsl = "wsl.exe"

            with (
                patch.object(runtime, "docker_path", return_value="/mnt/c/repository/data/wsl-keepalive"),
                patch.object(docker_runtime.subprocess, "Popen") as popen,
            ):
                runtime.keep_wsl_alive(root)

            marker = root / "data" / "wsl-keepalive"
            token = marker.read_text(encoding="utf-8")
            command = popen.call_args.args[0]
            self.assertEqual(command[:4], ["wsl.exe", "--", "sh", "-lc"])
            self.assertEqual(command[-2:], ["/mnt/c/repository/data/wsl-keepalive", token])
            self.assertEqual(popen.call_args.kwargs["stdin"], copilot_value.subprocess.DEVNULL)

            runtime.release_wsl_keepalive(root)
            self.assertFalse(marker.exists())

    def test_materializes_application_archive_without_bootstrap_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "copilot-value.pyz"
            install = root / "repository" / ".copilot-value"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("__main__.py", "raise SystemExit(1)\n")
                archive.writestr("scripts/__init__.py", "")
                archive.writestr("scripts/copilot_value.py", "# installed CLI\n")
                archive.writestr("config/value-model.example.json", "{}\n")

            copilot_value.materialize_archive(archive_path, install)

            self.assertTrue((install / "scripts" / "copilot_value.py").exists())
            self.assertTrue((install / "config" / "value-model.example.json").exists())
            self.assertFalse((install / "__main__.py").exists())
            self.assertFalse((install / "scripts" / "__init__.py").exists())

    def test_materialization_replaces_empty_docker_bind_directory_with_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "copilot-value.pyz"
            install = root / "repository" / ".copilot-value"
            placeholder = install / "config" / "otel-collector.yaml"
            placeholder.mkdir(parents=True)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("config/otel-collector.yaml", "receivers: {}\n")

            copilot_value.materialize_archive(archive_path, install)

            self.assertTrue(placeholder.is_file())
            self.assertEqual(placeholder.read_text(encoding="utf-8"), "receivers: {}\n")

    def test_local_config_replaces_empty_docker_bind_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            install = Path(temporary_directory) / ".copilot-value"
            config_root = install / "config"
            config_root.mkdir(parents=True)
            (config_root / "value-model.example.json").write_text(
                json.dumps({"benchmark": {"acknowledgedAssumptions": False}}),
                encoding="utf-8",
            )
            (config_root / "value-model.local.json").mkdir()

            local_path = copilot_value.write_local_config(install)

            self.assertTrue(local_path.is_file())
            self.assertTrue(
                json.loads(local_path.read_text(encoding="utf-8"))["benchmark"][
                    "acknowledgedAssumptions"
                ]
            )

    def test_upgrade_rolls_back_files_after_permission_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            install = root / ".copilot-value"
            staging = install / ".upgrade-test"
            payload = staging / "payload"
            (payload / "config").mkdir(parents=True)
            (install / "config").mkdir(parents=True, exist_ok=True)
            first = install / "config" / "first.yaml"
            second = install / "config" / "second.yaml"
            first.write_text("old-first\n", encoding="utf-8")
            second.write_text("old-second\n", encoding="utf-8")
            (payload / "config" / "first.yaml").write_text("new-first\n", encoding="utf-8")
            (payload / "config" / "second.yaml").write_text("new-second\n", encoding="utf-8")
            real_replace = os.replace

            def replace_with_lock(source: str | Path, target: str | Path) -> None:
                source_path = Path(source)
                if source_path.name == "second.yaml" and "payload" in source_path.parts:
                    raise PermissionError("simulated bind-mount lock")
                real_replace(source, target)

            with patch.object(copilot_value.os, "replace", side_effect=replace_with_lock):
                with self.assertRaisesRegex(
                    copilot_value.CopilotValueError, "previous runtime was restored"
                ):
                    copilot_value.deploy_staged_archive(
                        staging, install, retry_timeout_seconds=0
                    )

            self.assertEqual(first.read_text(encoding="utf-8"), "old-first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old-second\n")

    def test_application_defaults_to_install_without_arguments(self) -> None:
        archive_path = Path("copilot-value.pyz")

        self.assertEqual(copilot_value.command_line_arguments([], archive_path), ["install"])
        self.assertEqual(
            copilot_value.command_line_arguments(["status"], archive_path), ["status"]
        )
        self.assertEqual(copilot_value.command_line_arguments([], None), [])


if __name__ == "__main__":
    unittest.main()