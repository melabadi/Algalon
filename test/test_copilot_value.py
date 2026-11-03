from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from scripts import copilot_value, docker_runtime

RUNTIME_PHASE_TOOL_PATTERNS = {
    "planning": ["ask_user", "exit_plan_mode", "todo"],
    "research": ["view", "rg", "grep", "glob", "fetch", "search", "lsp"],
    "coding": ["edit", "create", "apply_patch", "str_replace"],
    "validation": [
        "powershell", "bash", "shell", "test", "tests", "playwright", "browser", "screenshot",
    ],
}


def write_bytes_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def read_bytes_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


class FakeRegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, *_arguments) -> None:
        return None


class FakeWinreg:
    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(
        self, values: dict[str, tuple[str, int]] | None = None, *, missing_key: bool = False
    ) -> None:
        self.values = dict(values or {})
        self.missing_key = missing_key
        self.access: list[int] = []

    def OpenKey(self, _root, path, _reserved, access):
        if self.missing_key:
            raise FileNotFoundError(path)
        self.access.append(access)
        return FakeRegistryKey()

    def CreateKeyEx(self, _root, _path, _reserved, access):
        self.access.append(access)
        return FakeRegistryKey()

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, _key, name, _reserved, value_type, value):
        self.values[name] = (value, value_type)

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def posix_host(home: Path, platform: str = "linux", **environment: str):
    return copilot_value.UserEnvironmentHost(platform=platform, home=home, environment=environment)


def windows_host(home: Path, registry: FakeWinreg, **environment: str):
    return copilot_value.UserEnvironmentHost(
        platform="windows",
        home=home,
        environment=environment,
        registry=copilot_value.WindowsUserEnvironment(registry),
        broadcast=Mock(return_value=True),
    )


def run_quietly(function, *arguments):
    output = io.StringIO()
    with redirect_stdout(output):
        result = function(*arguments)
    return result, output.getvalue()


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
            (install / "data").mkdir()
            (install / "data" / "grafana-admin-password.txt").write_text("legacy-fixture", encoding="utf-8")
            (install / "data" / "vscode-workspace-storage-empty").mkdir()
            (install / "docker" / ".env").write_text(
                "GRAFANA_ADMIN_PASSWORD=legacy-fixture\n"
                "VSCODE_WORKSPACE_STORAGE_PATH=/mnt/c/Users/example/workspaceStorage\n",
                encoding="utf-8",
            )
            arguments = SimpleNamespace(
                repository_root=str(repository),
                skip_docker_check=True,
                skip_vscode_settings=True,
                skip_copilot_env=True,
                no_start=True,
                poll_seconds=5,
            )
            output = io.StringIO()

            with (
                patch.object(copilot_value, "execution_archive", return_value=None),
                patch.object(copilot_value, "install_root", return_value=install),
                patch.object(copilot_value, "enable_copilot_otel_environment") as enable,
                redirect_stdout(output),
            ):
                self.assertEqual(copilot_value.command_install(arguments), 0)

            environment = copilot_value.read_environment(install / "docker" / ".env")
            self.assertNotIn("REPOSITORY_MOUNT_PATH", environment)
            self.assertNotIn("GRAFANA_ADMIN_PASSWORD", environment)
            self.assertFalse((install / "data" / "grafana-admin-password.txt").exists())
            self.assertNotIn("VSCODE_WORKSPACE_STORAGE_PATH", environment)
            self.assertFalse((install / "data" / "vscode-workspace-storage-empty").exists())
            self.assertIn("Removed the obsolete VS Code workspaceStorage mount", output.getvalue())
            self.assertIn(copilot_value.VSCODE_HARNESS_NOTE, output.getvalue())
            self.assertNotIn(copilot_value.COPILOT_ENV_NOTE, output.getvalue())
            enable.assert_not_called()
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


class CopilotRuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.backups = self.root / "install" / "data" / copilot_value.COPILOT_ENV_BACKUP_DIRECTORY

    def enable(self, host):
        return run_quietly(copilot_value.enable_copilot_otel_environment, self.backups, host)

    def restore(self, host):
        return run_quietly(copilot_value.restore_copilot_otel_environment, self.backups, host)

    def test_posix_enablement_is_idempotent_backed_up_and_restorable(self) -> None:
        bashrc = self.home / ".bashrc"
        zshrc = self.home / ".zshrc"
        bashrc_original = "alias ll='ls -l'\n"
        zshrc_original = 'export PATH="$HOME/bin:$PATH"'
        write_bytes_text(bashrc, bashrc_original)
        write_bytes_text(zshrc, zshrc_original)
        (self.home / ".config" / "fish").mkdir(parents=True)
        fish = self.home / ".config" / "fish" / "conf.d" / "algalon-copilot-otel.fish"
        systemd = self.home / ".config" / "environment.d" / "90-algalon-copilot-otel.conf"
        host = posix_host(self.home)

        outcome, output = self.enable(host)

        block = copilot_value.COPILOT_ENV_BLOCK
        self.assertEqual(outcome, "enabled")
        self.assertIn("export COPILOT_OTEL_ENABLED=true\n", block)
        self.assertEqual(read_bytes_text(bashrc), f"{bashrc_original}\n{block}")
        self.assertEqual(read_bytes_text(zshrc), f"{zshrc_original}\n\n{block}")
        self.assertTrue(read_bytes_text(fish).endswith("set -gx COPILOT_OTEL_ENABLED true\n"))
        self.assertTrue(read_bytes_text(systemd).endswith("\nCOPILOT_OTEL_ENABLED=true\n"))
        self.assertFalse((self.home / ".profile").exists())
        self.assertFalse((self.home / ".bash_profile").exists())
        self.assertEqual(
            sorted(read_bytes_text(path) for path in self.backups.glob("*.backup")),
            sorted([bashrc_original, zshrc_original]),
        )
        self.assertIn(f"Enabled COPILOT_OTEL_ENABLED=true in {bashrc}", output)
        self.assertIn("restore-copilot-env", output)
        self.assertEqual(len(copilot_value.read_copilot_env_changes(self.backups)), 4)
        enabled_bashrc = read_bytes_text(bashrc)

        second, second_output = self.enable(host)

        self.assertEqual(second, "unchanged")
        self.assertIn("already enabled", second_output)
        self.assertEqual(read_bytes_text(bashrc), enabled_bashrc)
        self.assertEqual(read_bytes_text(bashrc).count(copilot_value.COPILOT_ENV_BLOCK_START), 1)
        self.assertEqual(len(copilot_value.read_copilot_env_changes(self.backups)), 4)
        self.assertEqual(len(list(self.backups.glob("*.backup"))), 2)
        self.assertEqual(
            copilot_value.copilot_otel_environment_status(host),
            (
                True,
                "enabled for new shells (.zshrc, .bashrc, algalon-copilot-otel.fish, "
                "90-algalon-copilot-otel.conf)",
            ),
        )

        result, restore_output = self.restore(host)

        self.assertEqual(result, 0)
        self.assertEqual(read_bytes_text(bashrc), bashrc_original)
        self.assertEqual(read_bytes_text(zshrc), f"{zshrc_original}\n")
        self.assertFalse(fish.exists())
        self.assertFalse(systemd.exists())
        self.assertFalse((self.backups / copilot_value.COPILOT_ENV_MANIFEST).exists())
        self.assertTrue((self.backups / copilot_value.COPILOT_ENV_OPT_OUT).exists())
        self.assertIn(f"Removed the Algalon COPILOT_OTEL_ENABLED block from {bashrc}", restore_output)
        self.assertIn("enable-copilot-env", restore_output)
        self.assertEqual(copilot_value.copilot_otel_environment_status(host), (False, "not enabled"))

    def test_creates_one_startup_file_for_the_login_shell_when_none_exists(self) -> None:
        linux = posix_host(self.home, SHELL="/bin/bash")
        systemd = self.home / ".config" / "environment.d" / "90-algalon-copilot-otel.conf"

        self.assertEqual(self.enable(linux)[0], "enabled")

        self.assertEqual(read_bytes_text(self.home / ".profile"), copilot_value.COPILOT_ENV_BLOCK)
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".config" / "fish").exists())
        self.assertTrue(systemd.exists())
        self.assertEqual(self.restore(linux)[0], 0)
        self.assertFalse((self.home / ".profile").exists())
        self.assertFalse(systemd.exists())

        mac_home = self.root / "mac"
        mac_home.mkdir()
        mac = posix_host(mac_home, "macos", SHELL="/bin/zsh")
        outcome, _output = run_quietly(
            copilot_value.enable_copilot_otel_environment, self.root / "mac-backups", mac
        )
        self.assertEqual(outcome, "enabled")
        self.assertEqual(read_bytes_text(mac_home / ".zshrc"), copilot_value.COPILOT_ENV_BLOCK)
        self.assertFalse((mac_home / ".profile").exists())
        self.assertFalse((mac_home / ".config").exists())

    def test_conflicting_user_values_are_reported_and_left_unchanged(self) -> None:
        bashrc = self.home / ".bashrc"
        write_bytes_text(bashrc, "alias ll='ls -l'\n")
        cases = (
            (
                {"COPILOT_OTEL_ENABLED": "false"},
                "COPILOT_OTEL_ENABLED is already set to 'false' in the current environment",
            ),
            (
                {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://user:secret@example.com:4318/v1?x=1"},
                "OTEL_EXPORTER_OTLP_ENDPOINT is set to https://example.com:4318 in the current "
                "environment, so Algalon did not enable COPILOT_OTEL_ENABLED",
            ),
            (
                {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://[::1]:4317/v1/traces"},
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is set to http://[::1]:4317",
            ),
            (
                {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:invalid"},
                "is set to http://localhost in the current environment",
            ),
            (
                {"OTEL_EXPORTER_OTLP_ENDPOINT": "collector:4318"},
                "is set to an unrecognized endpoint",
            ),
        )
        for environment, expected in cases:
            with self.subTest(environment=environment):
                outcome, output = self.enable(posix_host(self.home, **environment))
                self.assertEqual(outcome, "conflict")
                self.assertIn(expected, output)
                self.assertNotIn("secret", output)
                self.assertEqual(read_bytes_text(bashrc), "alias ll='ls -l'\n")
                self.assertFalse(self.backups.exists())

        write_bytes_text(bashrc, "export COPILOT_OTEL_ENABLED='0'\n")
        outcome, output = self.enable(posix_host(self.home))
        self.assertEqual(outcome, "conflict")
        self.assertIn(f"already set to '0' in {bashrc}", output)
        self.assertEqual(read_bytes_text(bashrc), "export COPILOT_OTEL_ENABLED='0'\n")

        registry = FakeWinreg({"COPILOT_OTEL_ENABLED": ("0", FakeWinreg.REG_SZ)})
        windows = windows_host(self.home, registry)
        outcome, output = self.enable(windows)
        self.assertEqual(outcome, "conflict")
        self.assertIn(r"'0' in HKCU\Environment", output)
        self.assertEqual(registry.values["COPILOT_OTEL_ENABLED"], ("0", FakeWinreg.REG_SZ))
        windows.broadcast.assert_not_called()

        write_bytes_text(bashrc, "alias ll='ls -l'\n")
        local = posix_host(
            self.home,
            COPILOT_OTEL_ENABLED="TRUE",
            OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318",
            OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:4318/v1/traces",
        )
        self.assertEqual(self.enable(local)[0], "enabled")

    def test_windows_enablement_uses_the_user_registry_and_restores_it(self) -> None:
        registry = FakeWinreg()
        host = windows_host(self.home, registry)

        outcome, output = self.enable(host)

        self.assertEqual(outcome, "enabled")
        self.assertEqual(registry.values["COPILOT_OTEL_ENABLED"], ("true", FakeWinreg.REG_SZ))
        self.assertIn(r"Enabled COPILOT_OTEL_ENABLED=true for this user (HKCU\Environment).", output)
        host.broadcast.assert_called_once_with()
        self.assertEqual(self.enable(host)[0], "unchanged")
        self.assertEqual(host.broadcast.call_count, 1)
        self.assertEqual(
            copilot_value.copilot_otel_environment_status(host),
            (True, r"enabled for this user (HKCU\Environment)"),
        )
        self.assertEqual(self.restore(host)[0], 0)
        self.assertNotIn("COPILOT_OTEL_ENABLED", registry.values)
        self.assertEqual(host.broadcast.call_count, 2)
        self.assertTrue((self.backups / copilot_value.COPILOT_ENV_OPT_OUT).exists())

        registry.values["COPILOT_OTEL_ENABLED"] = ("", FakeWinreg.REG_EXPAND_SZ)
        self.assertEqual(self.enable(host)[0], "enabled")
        self.assertEqual(registry.values["COPILOT_OTEL_ENABLED"], ("true", FakeWinreg.REG_SZ))
        result, output = self.restore(host)
        self.assertEqual(result, 0)
        self.assertIn("Restored the previous COPILOT_OTEL_ENABLED value", output)
        self.assertEqual(registry.values["COPILOT_OTEL_ENABLED"], ("", FakeWinreg.REG_EXPAND_SZ))

        del registry.values["COPILOT_OTEL_ENABLED"]
        self.assertEqual(self.enable(host)[0], "enabled")
        registry.values["COPILOT_OTEL_ENABLED"] = ("false", FakeWinreg.REG_SZ)
        result, output = self.restore(host)
        self.assertEqual(result, 0)
        self.assertIn("changed after Algalon enabled it", output)
        self.assertEqual(registry.values["COPILOT_OTEL_ENABLED"], ("false", FakeWinreg.REG_SZ))

        registry.values["COPILOT_OTEL_ENABLED"] = ("true", FakeWinreg.REG_SZ)
        result, output = self.restore(host)
        self.assertEqual(result, 0)
        self.assertIn("was not set by this installation", output)
        self.assertEqual(registry.values["COPILOT_OTEL_ENABLED"], ("true", FakeWinreg.REG_SZ))

        unavailable = copilot_value.UserEnvironmentHost(
            platform="windows", home=self.home, environment={}
        )
        outcome, output = run_quietly(
            copilot_value.enable_copilot_otel_environment, self.root / "other", unavailable
        )
        self.assertEqual(outcome, "failed")
        self.assertIn("setx COPILOT_OTEL_ENABLED true", output)

    def test_partial_posix_failure_rolls_back_every_change(self) -> None:
        bashrc = self.home / ".bashrc"
        write_bytes_text(bashrc, "alias ll='ls -l'\n")
        real_write = copilot_value.write_user_text

        def fail_systemd(path: Path, text: str) -> None:
            if path.suffix == ".conf":
                raise PermissionError("read-only environment.d")
            real_write(path, text)

        with patch.object(copilot_value, "write_user_text", side_effect=fail_systemd):
            outcome, output = self.enable(posix_host(self.home))

        self.assertEqual(outcome, "failed")
        self.assertIn("read-only environment.d", output)
        self.assertIn("No user environment changes were kept.", output)
        self.assertIn("export COPILOT_OTEL_ENABLED=true", output)
        self.assertEqual(read_bytes_text(bashrc), "alias ll='ls -l'\n")
        self.assertFalse((self.backups / copilot_value.COPILOT_ENV_MANIFEST).exists())

        with (
            patch.object(copilot_value, "write_user_text", side_effect=fail_systemd),
            patch.object(
                copilot_value, "revert_copilot_env_change", side_effect=OSError("locked")
            ),
        ):
            outcome, output = self.enable(posix_host(self.home))
        self.assertEqual(outcome, "failed")
        self.assertIn("could not be rolled back (locked)", output)
        self.assertEqual(len(copilot_value.read_copilot_env_changes(self.backups)), 2)

    def test_incomplete_blocks_and_invalid_manifests_fail_without_changes(self) -> None:
        bashrc = self.home / ".bashrc"
        incomplete = f"{copilot_value.COPILOT_ENV_BLOCK_START}\nexport COPILOT_OTEL_ENABLED=true\n"
        write_bytes_text(bashrc, incomplete)

        outcome, output = self.enable(posix_host(self.home))

        self.assertEqual(outcome, "failed")
        self.assertIn(f"{bashrc}: The Algalon block", output)
        self.assertEqual(read_bytes_text(bashrc), incomplete)
        result, output = self.restore(posix_host(self.home))
        self.assertEqual(result, 1)
        self.assertIn("Could not restore", output)
        self.assertFalse((self.backups / copilot_value.COPILOT_ENV_OPT_OUT).exists())

        write_bytes_text(self.backups / copilot_value.COPILOT_ENV_MANIFEST, "[]")
        outcome, output = self.enable(posix_host(self.home))
        self.assertEqual(outcome, "failed")
        self.assertIn("not a valid Algalon environment backup manifest", output)
        write_bytes_text(self.backups / copilot_value.COPILOT_ENV_MANIFEST, "{")
        with self.assertRaisesRegex(copilot_value.CopilotValueError, "Could not read"):
            copilot_value.read_copilot_env_changes(self.backups)

    def test_restore_retries_failed_changes_and_sweeps_unrecorded_blocks(self) -> None:
        bashrc = self.home / ".bashrc"
        write_bytes_text(bashrc, "export EDITOR=vim\n")
        host = posix_host(self.home)
        self.assertEqual(self.enable(host)[0], "enabled")
        broken = read_bytes_text(bashrc).replace(f"{copilot_value.COPILOT_ENV_BLOCK_END}\n", "")
        write_bytes_text(bashrc, broken)

        result, output = self.restore(host)

        self.assertEqual(result, 1)
        self.assertIn(f"Could not restore {bashrc}", output)
        remaining = copilot_value.read_copilot_env_changes(self.backups)
        self.assertEqual([change["path"] for change in remaining], [str(bashrc)])
        self.assertFalse(
            (self.home / ".config" / "environment.d" / "90-algalon-copilot-otel.conf").exists()
        )

        write_bytes_text(bashrc, f"export EDITOR=vim\n\n{copilot_value.COPILOT_ENV_BLOCK}")
        zshrc = self.home / ".zshrc"
        write_bytes_text(zshrc, f"setopt autocd\n\n{copilot_value.COPILOT_ENV_BLOCK}")
        result, output = self.restore(host)
        self.assertEqual(result, 0)
        self.assertEqual(read_bytes_text(bashrc), "export EDITOR=vim\n")
        self.assertEqual(read_bytes_text(zshrc), "setopt autocd\n")
        self.assertIn(f"Removed the Algalon COPILOT_OTEL_ENABLED block from {zshrc}", output)

        result, output = self.restore(host)
        self.assertEqual(result, 0)
        self.assertIn("No Algalon-managed COPILOT_OTEL_ENABLED settings were found.", output)

    def test_restores_previous_algalon_named_files_outside_home(self) -> None:
        config_home = self.root / "xdg"
        systemd = config_home / "environment.d" / "90-algalon-copilot-otel.conf"
        write_bytes_text(systemd, "COPILOT_OTEL_ENABLED=1\n")
        write_bytes_text(self.home / ".profile", "umask 022\n")
        host = posix_host(self.home, XDG_CONFIG_HOME=str(config_home))

        self.assertEqual(self.enable(host)[0], "enabled")

        self.assertEqual(read_bytes_text(systemd), copilot_value.SYSTEMD_COPILOT_ENV_CONTENT)
        backups = [path.name for path in self.backups.glob("90-algalon-copilot-otel.conf.*.backup")]
        self.assertEqual(len(backups), 1)
        result, output = self.restore(host)
        self.assertEqual(result, 0)
        self.assertEqual(read_bytes_text(systemd), "COPILOT_OTEL_ENABLED=1\n")
        self.assertIn(f"Restored {systemd}", output)
        self.assertEqual(read_bytes_text(self.home / ".profile"), "umask 022\n")

    def test_preserves_symlinked_startup_files(self) -> None:
        target = self.root / "dotfiles" / "bashrc"
        write_bytes_text(target, "alias ll='ls -l'\n")
        link = self.home / ".bashrc"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symbolic links are unavailable: {error}")

        self.assertEqual(self.enable(posix_host(self.home))[0], "enabled")

        self.assertTrue(link.is_symlink())
        self.assertIn(copilot_value.COPILOT_ENV_BLOCK_START, read_bytes_text(target))

    def test_revert_ignores_changes_that_were_already_undone(self) -> None:
        host = windows_host(self.home, FakeWinreg())
        revert = copilot_value.revert_copilot_env_change
        bashrc = self.home / ".bashrc"
        write_bytes_text(bashrc, "alias ll='ls -l'\n")

        self.assertIsNone(revert({"kind": "registry", "previous": None}, host, self.backups))
        self.assertIsNone(
            revert({"kind": "block", "path": str(self.home / ".missing"), "created": True}, host, self.backups)
        )
        self.assertIsNone(revert({"kind": "block", "path": str(bashrc), "created": False}, host, self.backups))
        self.assertIsNone(revert({"kind": "file", "path": ""}, host, self.backups))
        self.assertIsNone(revert({"kind": "unknown", "path": str(bashrc)}, host, self.backups))
        self.assertEqual(read_bytes_text(bashrc), "alias ll='ls -l'\n")
        host.broadcast.assert_not_called()
        with patch.object(copilot_value, "read_user_text", side_effect=PermissionError("denied")):
            self.assertEqual(copilot_value.startup_file_values(bashrc, "COPILOT_OTEL_ENABLED"), [])

    def test_marked_blocks_are_replaced_in_place_and_deduplicated(self) -> None:
        start = copilot_value.COPILOT_ENV_BLOCK_START
        end = copilot_value.COPILOT_ENV_BLOCK_END
        stale = f"{start}\nexport COPILOT_OTEL_ENABLED=1\n{end}\n"
        block = copilot_value.COPILOT_ENV_BLOCK

        updated = copilot_value.with_copilot_env_block(f"first\n{stale}last\n\n{stale}")

        self.assertEqual(updated, f"first\n{block}last\n")
        self.assertEqual(copilot_value.with_copilot_env_block(updated), updated)
        self.assertEqual(copilot_value.remove_copilot_env_block(updated), "first\nlast\n")
        self.assertEqual(copilot_value.with_copilot_env_block(""), block)

    def test_unique_backup_paths_never_overwrite_existing_backups(self) -> None:
        with patch.object(copilot_value.time, "strftime", return_value="20261029T190000Z"):
            first = copilot_value.unique_backup_path(self.backups, "zshrc", ".backup")
            first.write_text("original", encoding="utf-8")
            second = copilot_value.unique_backup_path(self.backups, "zshrc", ".backup")

        self.assertEqual(first.name, "zshrc.20261029T190000Z.backup")
        self.assertEqual(second.name, "zshrc.20261029T190000Z-1.backup")

    def test_windows_user_environment_wraps_hkcu(self) -> None:
        registry = FakeWinreg()
        environment = copilot_value.WindowsUserEnvironment(registry)

        self.assertIsNone(environment.get("MISSING"))
        environment.set("NAME", "value")
        self.assertEqual(environment.get("NAME"), ("value", FakeWinreg.REG_SZ))
        environment.delete("NAME")
        environment.delete("NAME")
        self.assertIsNone(environment.get("NAME"))
        self.assertEqual(registry.access[0], FakeWinreg.KEY_QUERY_VALUE)
        self.assertIn(FakeWinreg.KEY_QUERY_VALUE | FakeWinreg.KEY_SET_VALUE, registry.access)
        self.assertIsNone(
            copilot_value.WindowsUserEnvironment(FakeWinreg(missing_key=True)).get("NAME")
        )
        with patch.object(
            copilot_value.importlib, "import_module", return_value=registry
        ) as import_module:
            self.assertIs(copilot_value.WindowsUserEnvironment()._winreg, registry)
        import_module.assert_called_once_with("winreg")

    def test_broadcasts_environment_changes_best_effort(self) -> None:
        import ctypes

        user32 = Mock()
        user32.SendMessageTimeoutW.return_value = 1
        with patch.object(ctypes, "WinDLL", create=True, return_value=user32) as windll:
            self.assertTrue(copilot_value.broadcast_environment_change())
        windll.assert_called_once_with("user32", use_last_error=True)
        self.assertEqual(
            user32.SendMessageTimeoutW.call_args.args[:6],
            (0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000),
        )
        with patch.object(ctypes, "WinDLL", create=True, side_effect=OSError("no user32")):
            self.assertFalse(copilot_value.broadcast_environment_change())

    def test_selects_the_platform_environment_host(self) -> None:
        for os_name, platform, expected in (
            ("nt", "win32", "windows"),
            ("posix", "darwin", "macos"),
            ("posix", "linux", "linux"),
        ):
            with (
                self.subTest(expected=expected),
                patch.object(copilot_value.os, "name", os_name),
                patch.object(copilot_value.sys, "platform", platform),
            ):
                self.assertEqual(copilot_value.copilot_env_platform(), expected)

        with patch.object(copilot_value, "copilot_env_platform", return_value="linux"):
            host = copilot_value.user_environment_host()
        self.assertIsNone(host.registry)
        self.assertEqual(host.platform, "linux")
        registry = Mock()
        with (
            patch.object(copilot_value, "copilot_env_platform", return_value="windows"),
            patch.object(copilot_value, "WindowsUserEnvironment", return_value=registry),
        ):
            self.assertIs(copilot_value.user_environment_host().registry, registry)

    def test_reports_copilot_otel_environment_status(self) -> None:
        status = copilot_value.copilot_otel_environment_status
        self.assertEqual(status(posix_host(self.home)), (False, "not enabled"))
        self.assertEqual(
            status(posix_host(self.home, COPILOT_OTEL_ENABLED="true")),
            (True, "enabled in the current environment"),
        )
        self.assertEqual(
            status(posix_host(self.home, COPILOT_OTEL_ENABLED="0")),
            (False, "set to '0' in the current environment"),
        )
        write_bytes_text(self.home / ".bashrc", copilot_value.COPILOT_ENV_BLOCK)
        self.assertEqual(status(posix_host(self.home)), (True, "enabled for new shells (.bashrc)"))
        self.assertEqual(
            status(windows_host(self.home, FakeWinreg({"COPILOT_OTEL_ENABLED": ("0", 1)}))),
            (False, r"set to '0' (HKCU\Environment)"),
        )
        failing = windows_host(self.home, FakeWinreg())
        with patch.object(failing.registry, "get", side_effect=PermissionError("denied")):
            enabled, detail = status(failing)
            self.assertEqual(copilot_value.user_environment_values(failing, "NAME"), [])
        self.assertFalse(enabled)
        self.assertIn("unknown", detail)

    def test_reports_copilot_cli_version_and_old_releases(self) -> None:
        with patch.object(copilot_value.shutil, "which", return_value=None):
            self.assertIsNone(copilot_value.copilot_cli_status())

        def which(name: str) -> str | None:
            return f"/tools/{name}" if name.startswith("copilot") else None

        cases = (
            ("GitHub Copilot CLI 1.0.105.\nRun 'copilot update' to check for updates.\n", 0, ("1.0.105", None)),
            ("1.0.64\n", 0, ("1.0.64", None)),
            ("1.0.70\n", 1, ("installed (version unavailable)", None)),
            ("no version\n", 0, ("installed (version unavailable)", None)),
        )
        for stdout, returncode, expected in cases:
            with (
                self.subTest(stdout=stdout, returncode=returncode),
                patch.object(copilot_value.shutil, "which", side_effect=which),
                patch.object(
                    copilot_value.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], returncode, stdout=stdout),
                ) as run,
            ):
                self.assertEqual(copilot_value.copilot_cli_status(), expected)
            self.assertEqual(run.call_args.args[0][1], "--version")
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

        with (
            patch.object(copilot_value.shutil, "which", side_effect=which),
            patch.object(
                copilot_value.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="0.0.354\n"),
            ),
        ):
            version, warning = copilot_value.copilot_cli_status()
        self.assertEqual(version, "0.0.354")
        self.assertIn("Copilot CLI 0.0.354 is older than 1.0.64", warning)
        with (
            patch.object(copilot_value.shutil, "which", side_effect=which),
            patch.object(
                copilot_value.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("copilot", 5),
            ),
        ):
            self.assertEqual(
                copilot_value.copilot_cli_status(), ("installed (version unavailable)", None)
            )


class CopilotEnvironmentCommandTests(unittest.TestCase):
    def test_install_enables_copilot_env_unless_skipped_or_opted_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            install = repository / ".copilot-value"
            (install / "config").mkdir(parents=True)
            (install / "docker").mkdir()
            (install / "config" / "value-model.example.json").write_text(
                json.dumps({"benchmark": {"phaseToolPatterns": RUNTIME_PHASE_TOOL_PATTERNS}}),
                encoding="utf-8",
            )
            backups = install / "data" / copilot_value.COPILOT_ENV_BACKUP_DIRECTORY
            arguments = SimpleNamespace(
                repository_root=str(repository),
                skip_docker_check=True,
                skip_vscode_settings=True,
                skip_copilot_env=False,
                no_start=True,
                poll_seconds=5,
            )

            def install_with(outcome: str, **overrides: object) -> tuple[Mock, str]:
                for name, value in overrides.items():
                    setattr(arguments, name, value)
                output = io.StringIO()
                with (
                    patch.object(copilot_value, "execution_archive", return_value=None),
                    patch.object(copilot_value, "install_root", return_value=install),
                    patch.object(
                        copilot_value, "enable_copilot_otel_environment", return_value=outcome
                    ) as enable,
                    redirect_stdout(output),
                ):
                    self.assertEqual(copilot_value.command_install(arguments), 0)
                return enable, output.getvalue()

            enable, output = install_with("enabled")
            enable.assert_called_once_with(backups)
            self.assertIn(copilot_value.COPILOT_ENV_NOTE, output)

            enable, output = install_with("conflict")
            self.assertIn(copilot_value.VSCODE_HARNESS_NOTE, output)
            self.assertNotIn(copilot_value.COPILOT_ENV_NOTE, output)

            enable, output = install_with("enabled", skip_copilot_env=True)
            enable.assert_not_called()

            backups.mkdir(parents=True, exist_ok=True)
            (backups / copilot_value.COPILOT_ENV_OPT_OUT).write_text("opted out\n", encoding="utf-8")
            enable, output = install_with("enabled", skip_copilot_env=False)
            enable.assert_not_called()
            self.assertIn("opted this installation out", output)

    def test_install_restarts_with_recreated_containers_after_config_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            install = repository / ".copilot-value"
            (install / "config").mkdir(parents=True)
            (install / "docker").mkdir()
            (install / "config" / "value-model.example.json").write_text(
                json.dumps({"benchmark": {"phaseToolPatterns": RUNTIME_PHASE_TOOL_PATTERNS}}),
                encoding="utf-8",
            )
            arguments = SimpleNamespace(
                repository_root=str(repository),
                skip_docker_check=True,
                skip_vscode_settings=True,
                skip_copilot_env=True,
                no_start=False,
                poll_seconds=5,
            )
            output = io.StringIO()
            with (
                patch.object(copilot_value, "execution_archive", return_value=None),
                patch.object(copilot_value, "install_root", return_value=install),
                patch.object(copilot_value, "migrate_phase_tool_patterns", return_value=True) as migrate,
                patch.object(copilot_value, "start_installation", return_value=0) as start,
                patch.object(
                    copilot_value,
                    "runtime_migration_backup",
                    return_value=("/data/backups/vscode-legacy-20261029T190000Z", 4),
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(copilot_value.command_install(arguments), 0)

            migrate.assert_called_once_with(install, install / "config" / "value-model.local.json")
            start.assert_called_once_with(install, force_recreate=True)
            self.assertIn(
                "Legacy VS Code data was backed up to '/data/backups/vscode-legacy-20261029T190000Z'",
                output.getvalue(),
            )

    def test_enable_and_restore_commands_use_installation_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            install = Path(temporary_directory) / ".copilot-value"
            backups = install / "data" / copilot_value.COPILOT_ENV_BACKUP_DIRECTORY
            backups.mkdir(parents=True)
            (backups / copilot_value.COPILOT_ENV_OPT_OUT).write_text("opted out\n", encoding="utf-8")
            arguments = SimpleNamespace()
            with (
                patch.object(copilot_value, "install_root", return_value=install),
                patch.object(
                    copilot_value, "enable_copilot_otel_environment", return_value="unchanged"
                ) as enable,
            ):
                result, output = run_quietly(copilot_value.command_enable_copilot_env, arguments)
            self.assertEqual(result, 0)
            enable.assert_called_once_with(backups)
            self.assertFalse((backups / copilot_value.COPILOT_ENV_OPT_OUT).exists())
            self.assertIn(copilot_value.COPILOT_ENV_NOTE, output)

            with (
                patch.object(copilot_value, "install_root", return_value=install),
                patch.object(
                    copilot_value, "enable_copilot_otel_environment", return_value="conflict"
                ),
            ):
                self.assertEqual(copilot_value.command_enable_copilot_env(arguments), 1)

            with (
                patch.object(copilot_value, "install_root", return_value=install),
                patch.object(
                    copilot_value, "restore_copilot_otel_environment", return_value=0
                ) as restore,
            ):
                self.assertEqual(copilot_value.command_restore_copilot_env(arguments), 0)
            restore.assert_called_once_with(backups)


class PhaseToolPatternMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / ".copilot-value"
        (self.root / "config").mkdir(parents=True)
        self.example = self.root / "config" / "value-model.example.json"
        self.example.write_text(
            json.dumps({"benchmark": {"phaseToolPatterns": RUNTIME_PHASE_TOOL_PATTERNS}}),
            encoding="utf-8",
        )
        self.local = self.root / "config" / "value-model.local.json"
        self.marker = self.root / copilot_value.PHASE_TOOL_PATTERN_MIGRATION_MARKER
        self.backups = self.root / copilot_value.CONFIG_BACKUP_DIRECTORY

    def migrate(self) -> tuple[bool, str]:
        return run_quietly(copilot_value.migrate_phase_tool_patterns, self.root, self.local)

    def test_replaces_previous_vscode_defaults_with_runtime_defaults(self) -> None:
        config = {
            "loadedHourlyRateUsd": 123,
            "codingToolPatterns": ["apply_patch", "create_file"],
            "benchmark": {
                "acknowledgedAssumptions": True,
                "capacityRealization": 0.5,
                "phaseToolPatterns": copilot_value.PREVIOUS_VSCODE_PHASE_TOOL_PATTERNS,
                "scenarios": {"base": {"coding": {"wordsPerMinute": 60}}},
            },
        }
        write_bytes_text(self.local, json.dumps(config, indent=2) + "\n")
        original = self.local.read_bytes()

        changed, output = self.migrate()

        self.assertTrue(changed)
        expected = json.loads(json.dumps(config))
        expected["benchmark"]["phaseToolPatterns"] = RUNTIME_PHASE_TOOL_PATTERNS
        self.assertEqual(read_bytes_text(self.local), json.dumps(expected, indent=2) + "\n")
        backups = list(self.backups.glob("value-model.local.*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertIn("Replaced the previous VS Code benchmark.phaseToolPatterns defaults", output)
        self.assertIn(str(backups[0]), output)
        self.assertEqual(output.count("\n"), 1)
        self.assertTrue(self.marker.exists())

        migrated = self.local.read_bytes()
        self.assertEqual(self.migrate(), (False, ""))
        self.marker.unlink()
        self.assertEqual(self.migrate(), (False, ""))
        self.assertEqual(self.local.read_bytes(), migrated)
        self.assertEqual(len(list(self.backups.glob("*.json"))), 1)
        self.assertTrue(self.marker.exists())

    def test_appends_missing_runtime_patterns_to_customized_lists(self) -> None:
        template = (
            "{{\n"
            '  "loadedHourlyRateUsd": 92,\n'
            '  "benchmark": {{\n'
            '    "capacityRealization": 5e-1,\n'
            '    "phaseToolPatterns": {{\n'
            "{patterns}\n"
            "    }},\n"
            '    "scenarios": {{}}\n'
            "  }}\n"
            "}}\n"
        )
        customized = template.format(patterns=(
            '      "planning": ["todo", "my_planner"],\n'
            '      "research": ["search", "read"],\n'
            '      "validation": ["run_in_terminal", "test"],\n'
            '      "review": ["code_review"]'
        ))
        expected = template.format(patterns=(
            '      "planning": ["todo", "my_planner", "ask_user", "exit_plan_mode"],\n'
            '      "research": ["search", "read", "view", "rg", "grep", "glob", "fetch", "lsp"],\n'
            '      "validation": ["run_in_terminal", "test", "powershell", "bash", "shell", '
            '"tests", "playwright", "browser", "screenshot"],\n'
            '      "review": ["code_review"],\n'
            '      "coding": ["edit", "create", "apply_patch", "str_replace"]'
        ))
        for name, prefix, newline in (
            ("lf", b"", "\n"),
            ("bom-crlf", b"\xef\xbb\xbf", "\r\n"),
        ):
            with self.subTest(name):
                self.marker.unlink(missing_ok=True)
                self.local.write_bytes(prefix + customized.replace("\n", newline).encode("utf-8"))

                changed, output = self.migrate()

                self.assertTrue(changed)
                self.assertEqual(
                    self.local.read_bytes(),
                    prefix + expected.replace("\n", newline).encode("utf-8"),
                )
                self.assertIn(
                    "Added missing GitHub Copilot runtime tool patterns to your customized "
                    "benchmark.phaseToolPatterns",
                    output,
                )
                self.assertEqual(output.count("\n"), 1)
                migrated = self.local.read_bytes()
                self.marker.unlink()
                self.assertEqual(self.migrate(), (False, ""))
                self.assertEqual(self.local.read_bytes(), migrated)

        edited = expected.replace('"bash", "shell", ', '"bash", ')
        write_bytes_text(self.local, edited)
        self.assertEqual(self.migrate(), (False, ""))
        self.assertEqual(read_bytes_text(self.local), edited)
        self.assertEqual(len(list(self.backups.glob("*.json"))), 2)

    def test_single_line_and_tab_indented_configs_keep_their_style(self) -> None:
        compact = json.dumps({
            "benchmark": {"phaseToolPatterns": copilot_value.PREVIOUS_VSCODE_PHASE_TOOL_PATTERNS},
            "monthlySeatCostUsd": 39,
        })
        write_bytes_text(self.local, compact)
        self.assertTrue(self.migrate()[0])
        self.assertEqual(
            read_bytes_text(self.local),
            json.dumps({
                "benchmark": {"phaseToolPatterns": RUNTIME_PHASE_TOOL_PATTERNS},
                "monthlySeatCostUsd": 39,
            }),
        )

        self.marker.unlink()
        tabbed = {"benchmark": {"phaseToolPatterns": {"planning": ["todo"]}}}
        write_bytes_text(self.local, json.dumps(tabbed, indent="\t") + "\n")
        self.assertTrue(self.migrate()[0])
        merged = {phase: list(patterns) for phase, patterns in RUNTIME_PHASE_TOOL_PATTERNS.items()}
        merged["planning"] = ["todo", "ask_user", "exit_plan_mode"]
        self.assertEqual(
            read_bytes_text(self.local),
            json.dumps({"benchmark": {"phaseToolPatterns": merged}}, indent="\t") + "\n",
        )

        self.marker.unlink()
        empty_first = {"benchmark": {"phaseToolPatterns": {"planning": [], "research": ["custom"]}}}
        write_bytes_text(self.local, json.dumps(empty_first, indent=2) + "\n")
        self.assertTrue(self.migrate()[0])
        merged = {phase: list(patterns) for phase, patterns in RUNTIME_PHASE_TOOL_PATTERNS.items()}
        merged["research"] = ["custom", *RUNTIME_PHASE_TOOL_PATTERNS["research"]]
        self.assertEqual(
            read_bytes_text(self.local),
            json.dumps({"benchmark": {"phaseToolPatterns": merged}}, indent=2) + "\n",
        )

    def test_skips_configs_that_cannot_be_migrated_safely(self) -> None:
        write_bytes_text(self.local, json.dumps({"loadedHourlyRateUsd": 92}))
        self.assertEqual(self.migrate(), (False, ""))
        self.assertTrue(self.marker.exists())

        cases = (
            ("{ invalid", "Could not migrate benchmark.phaseToolPatterns"),
            (json.dumps({"benchmark": {"capacityRealization": 0.5}}), "not an object of pattern lists"),
            (json.dumps({"benchmark": {"phaseToolPatterns": ["todo"]}}), "not an object of pattern lists"),
            (json.dumps({"benchmark": {"phaseToolPatterns": {"planning": [1]}}}), "not an object of pattern lists"),
        )
        for text, message in cases:
            with self.subTest(text=text):
                self.marker.unlink(missing_ok=True)
                write_bytes_text(self.local, text)
                changed, output = self.migrate()
                self.assertFalse(changed)
                self.assertIn(message, output)
                self.assertEqual(read_bytes_text(self.local), text)
                self.assertFalse(self.marker.exists())

        customized = json.dumps({"benchmark": {"phaseToolPatterns": {"planning": ["todo"]}}})
        write_bytes_text(self.local, customized)
        with patch.object(copilot_value, "json_member_span", return_value=None):
            changed, output = self.migrate()
        self.assertFalse(changed)
        self.assertIn("could not be updated without reformatting", output)
        with patch.object(copilot_value, "render_json_value_like", return_value="{"):
            changed, output = self.migrate()
        self.assertFalse(changed)
        self.assertIn("could not be updated without reformatting", output)
        with patch.object(copilot_value, "write_file_atomically", side_effect=PermissionError("locked")):
            changed, output = self.migrate()
        self.assertFalse(changed)
        self.assertIn("locked", output)
        self.assertEqual(read_bytes_text(self.local), customized)
        self.assertFalse(self.marker.exists())

        self.example.write_text(json.dumps({"benchmark": {"phaseToolPatterns": "todo"}}), encoding="utf-8")
        changed, output = self.migrate()
        self.assertFalse(changed)
        self.assertIn("bundled example config has no valid benchmark.phaseToolPatterns", output)
        self.example.write_text(json.dumps({"benchmark": {}}), encoding="utf-8")
        changed, output = self.migrate()
        self.assertFalse(changed)
        self.assertIn("Could not migrate", output)

    def test_locates_nested_json_members_with_duplicate_keys(self) -> None:
        text = '{"a": {"b": 1}, "benchmark": {"x": [1, {"y": 2}], "phaseToolPatterns": {"p": []}, "phaseToolPatterns": {"q": ["r"]}}}'
        start, end = copilot_value.json_member_span(text, ("benchmark", "phaseToolPatterns"))
        self.assertEqual(text[start:end], '{"q": ["r"]}')
        self.assertIsNone(copilot_value.json_member_span(text, ("missing",)))
        self.assertIsNone(copilot_value.json_member_span(text, ("a", "b", "c")))


if __name__ == "__main__":
    unittest.main()