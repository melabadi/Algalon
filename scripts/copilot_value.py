#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import codecs
from dataclasses import dataclass
import getpass
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping, Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import zipfile

try:
    from scripts.docker_runtime import DockerRuntime
    from scripts.errors import CopilotValueError
    from scripts.release_smoke import (
        otlp_attribute,
        post_json,
        query_victoria_metrics,
        run_telemetry_smoke as _run_telemetry_smoke,
        smoke_metric_payload,
        smoke_trace_payload,
        wait_for_app_session,
        wait_for_metric,
    )
except ModuleNotFoundError:
    from docker_runtime import DockerRuntime
    from errors import CopilotValueError
    from release_smoke import (
        otlp_attribute,
        post_json,
        query_victoria_metrics,
        run_telemetry_smoke as _run_telemetry_smoke,
        smoke_metric_payload,
        smoke_trace_payload,
        wait_for_app_session,
        wait_for_metric,
    )

MINIMUM_PYTHON = (3, 11)
SERVICES = ("victoria-metrics", "collector", "worker", "app")
HEALTH_ENDPOINTS = (
    ("VictoriaMetrics", "http://127.0.0.1:8428/-/healthy"),
    ("Collector health", "http://127.0.0.1:13133/"),
    ("Algalon API", "http://127.0.0.1:3000/api/health"),
)
OTLP_TRACES_ENDPOINT = "http://127.0.0.1:4318/v1/traces"
VSCODE_OTEL_SETTINGS: dict[str, object] = {
    "chat.agentHost.otel.enabled": True,
    "chat.agentHost.otel.exporterType": "otlp-http",
    "chat.agentHost.otel.otlpEndpoint": "http://127.0.0.1:4318",
    "chat.agentHost.otel.captureContent": False,
    "github.copilot.chat.otel.enabled": True,
    "github.copilot.chat.otel.exporterType": "otlp-http",
    "github.copilot.chat.otel.otlpEndpoint": "http://127.0.0.1:4318",
    "github.copilot.chat.otel.captureContent": False,
}
PUBLIC_NPM_REGISTRY = "https://registry.npmjs.org/"
PUBLIC_PYPI_INDEX = "https://pypi.org/simple"
COPILOT_OTEL_VARIABLE = "COPILOT_OTEL_ENABLED"
COPILOT_OTEL_ENDPOINT_VARIABLES = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)
COPILOT_ENV_BLOCK_START = "# >>> algalon copilot otel >>>"
COPILOT_ENV_BLOCK_END = "# <<< algalon copilot otel <<<"
COPILOT_ENV_MANAGED_COMMENT = "# Managed by Algalon. Remove with the restore-copilot-env command."
COPILOT_ENV_BLOCK = (
    f"{COPILOT_ENV_BLOCK_START}\n"
    f"{COPILOT_ENV_MANAGED_COMMENT}\n"
    f"export {COPILOT_OTEL_VARIABLE}=true\n"
    f"{COPILOT_ENV_BLOCK_END}\n"
)
FISH_COPILOT_ENV_FILE = Path("fish") / "conf.d" / "algalon-copilot-otel.fish"
FISH_COPILOT_ENV_CONTENT = f"{COPILOT_ENV_MANAGED_COMMENT}\nset -gx {COPILOT_OTEL_VARIABLE} true\n"
SYSTEMD_COPILOT_ENV_FILE = Path("environment.d") / "90-algalon-copilot-otel.conf"
SYSTEMD_COPILOT_ENV_CONTENT = f"{COPILOT_ENV_MANAGED_COMMENT}\n{COPILOT_OTEL_VARIABLE}=true\n"
POSIX_STARTUP_FILES = (".zshrc", ".bashrc", ".bash_profile", ".profile")
COPILOT_ENV_BACKUP_DIRECTORY = "copilot-env-backups"
COPILOT_ENV_MANIFEST = "manifest.json"
COPILOT_ENV_OPT_OUT = "opt-out"
WINDOWS_ENVIRONMENT_KEY = r"HKCU\Environment"
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002
MINIMUM_COPILOT_CLI_VERSION = (1, 0, 64)
RUNTIME_MIGRATION_URL = "http://127.0.0.1:3000/api/internal/runtime-migration"
VSCODE_HARNESS_NOTE = (
    "In VS Code, use the Copilot harness; Local-harness sessions are no longer captured."
)
COPILOT_ENV_NOTE = (
    "Open a new terminal (or restart the Copilot app) so Copilot CLI and SDK sessions inherit "
    f"COPILOT_OTEL_ENABLED=true. {VSCODE_HARNESS_NOTE}"
)
PREVIOUS_VSCODE_PHASE_TOOL_PATTERNS: dict[str, list[str]] = {
    "planning": ["manage_todo_list", "todo", "ask_questions", "questions"],
    "research": [
        "search", "read", "fetch", "grep", "semantic", "reference", "documentation",
        "list_dir", "view_image", "tool_search", "usages",
    ],
    "coding": [
        "apply_patch", "create_file", "create_directory", "replace_string_in_file",
        "edit_notebook_file", "rename",
    ],
    "validation": [
        "run_in_terminal", "get_terminal_output", "terminal", "get_errors", "test",
        "playwright", "screenshot", "click", "navigate", "open_browser", "run_task",
        "task_output",
    ],
}
CONFIG_BACKUP_DIRECTORY = Path("data") / "config-backups"
PHASE_TOOL_PATTERN_MIGRATION_MARKER = Path("data") / "migrations" / "phase-tool-patterns-runtime-v1"
OBSOLETE_COMPOSE_PATTERN = re.compile(
    r"VSCODE_WORKSPACE_STORAGE_PATH|/vscode-workspace-storage|COPILOT_VALUE_CHAT_LOG_ROOT"
)


def execution_archive() -> Path | None:
    candidate = Path(sys.argv[0]).expanduser().resolve()
    if candidate.suffix.lower() == ".pyz" and candidate.is_file() and zipfile.is_zipfile(candidate):
        return candidate
    return None


def install_root() -> Path:
    if execution_archive():
        return (Path.cwd() / ".copilot-value").resolve()
    return Path(__file__).resolve().parent.parent


def stage_archive(archive_path: Path, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    staging_root = root / f".upgrade-{uuid.uuid4().hex}"
    payload_root = staging_root / "payload"
    payload_root.mkdir(parents=True)
    resolved_payload_root = payload_root.resolve()
    skipped = {"__main__.py", "scripts/__init__.py"}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                member_name = member.filename.replace("\\", "/")
                if member_name in skipped:
                    continue
                target = (payload_root / member_name).resolve()
                if not target.is_relative_to(resolved_payload_root):
                    raise CopilotValueError(
                        f"The application archive contains an unsafe path: '{member.filename}'."
                    )
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        return staging_root
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def retry_file_operation(
    operation: Callable[[], None], description: str, timeout_seconds: int = 15
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            operation()
            return
        except PermissionError as error:
            if time.monotonic() >= deadline:
                raise CopilotValueError(
                    f"Timed out waiting for Docker to release {description}: {error}"
                ) from error
            time.sleep(0.25)


def deploy_staged_archive(
    staging_root: Path, root: Path, retry_timeout_seconds: int = 15
) -> None:
    payload_root = staging_root / "payload"
    backup_root = staging_root / "backup"
    deployed: list[tuple[Path, Path | None]] = []
    try:
        for source in sorted(path for path in payload_root.rglob("*") if path.is_file()):
            relative_path = source.relative_to(payload_root)
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            target_existed = target.exists()
            if target_existed:
                backup = backup_root / relative_path
                backup.parent.mkdir(parents=True, exist_ok=True)
                if target.is_dir() and not target.is_symlink():
                    shutil.copytree(target, backup)
                else:
                    shutil.copy2(target, backup)
            deployed.append((target, backup))
            if target_existed:
                retry_file_operation(
                    lambda target=target: remove_path(target),
                    f"'{target}'",
                    retry_timeout_seconds,
                )
            retry_file_operation(
                lambda source=source, target=target: os.replace(source, target),
                f"'{target}'",
                retry_timeout_seconds,
            )
    except Exception as error:
        for target, backup in reversed(deployed):
            try:
                remove_path(target)
                if backup and backup.exists():
                    os.replace(backup, target)
            except OSError:
                pass
        raise CopilotValueError(
            f"Could not update '{root}'. The previous runtime was restored: {error}"
        ) from error
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def materialize_archive(archive_path: Path, root: Path) -> None:
    staging_root = stage_archive(archive_path, root)
    deploy_staged_archive(staging_root, root)


def existing_installation_running(runtime: DockerRuntime, root: Path) -> bool:
    return bool(set(SERVICES) & running_services(runtime, root))


def running_services(runtime: DockerRuntime, root: Path) -> set[str]:
    result = compose(
        runtime,
        root,
        ("--profile", "grafana", "ps", "--status", "running", "--services"),
        capture=True,
        check=False,
    )
    return set(result.stdout.splitlines()) if result.returncode == 0 else set()


def compose(
    runtime: DockerRuntime,
    root: Path,
    arguments: Sequence[str],
    *,
    capture: bool = False,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    compose_path = runtime.docker_path(root / "docker" / "compose.yaml")
    environment_path = runtime.docker_path(root / "docker" / ".env")
    return runtime.run(
        ("compose", "--env-file", environment_path, "-f", compose_path, *arguments),
        capture=capture,
        check=check,
        input_text=input_text,
    )


def endpoint_healthy(url: str, timeout_seconds: float = 2) -> bool:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def otlp_ingress_healthy(
    url: str = OTLP_TRACES_ENDPOINT,
    timeout_seconds: float = 2,
) -> bool:
    request = Request(
        url,
        data=b'{"resourceSpans":[]}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def wait_for_endpoint(name: str, url: str, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if endpoint_healthy(url):
            return
        time.sleep(0.5)
    raise CopilotValueError(f"{name} did not become healthy at {url} within {timeout_seconds} seconds.")


def wait_for_otlp_ingress(timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if otlp_ingress_healthy():
            return
        time.sleep(0.5)
    raise CopilotValueError(
        f"Collector OTLP ingress did not become healthy at {OTLP_TRACES_ENDPOINT} "
        f"within {timeout_seconds} seconds."
    )


def grafana_authorization_header(password: str) -> str:
    credentials = base64.b64encode(f"admin:{password}".encode()).decode()
    return f"Basic {credentials}"


def grafana_authenticates(password: str, base_url: str = "http://127.0.0.1:3001") -> bool:
    request = Request(
        f"{base_url.rstrip('/')}/api/user",
        headers={"Authorization": grafana_authorization_header(password)},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def reconcile_grafana_password(runtime: DockerRuntime, root: Path, password: str) -> None:
    if grafana_authenticates(password):
        return

    compose(
        runtime,
        root,
        (
            "exec",
            "-T",
            "grafana",
            "grafana",
            "cli",
            "--homepath",
            "/usr/share/grafana",
            "admin",
            "reset-admin-password",
            "--password-from-stdin",
        ),
        capture=True,
        input_text=f"{password}\n",
    )
    if not grafana_authenticates(password):
        raise CopilotValueError(
            "Grafana is healthy, but admin authentication still failed after resetting the "
            "persisted password. Check the Grafana container logs."
        )
    print("Grafana admin password updated. Only Grafana retains its password hash.")


def start_grafana_installation(root: Path) -> int:
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.keep_wsl_alive(root)
    runtime.require_compose()
    bootstrap = {
        "services": {
            "grafana": {
                "environment": {
                    "GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION": "false",
                    "GF_SECURITY_ADMIN_PASSWORD": secrets.token_urlsafe(32),
                }
            }
        }
    }
    compose(
        runtime,
        root,
        ("-f", "-", "--profile", "grafana", "up", "-d", "grafana"),
        capture=True,
        input_text=json.dumps(bootstrap),
    )
    wait_for_endpoint("Grafana API", "http://127.0.0.1:3001/api/health")
    print("Grafana: http://127.0.0.1:3001/d/personal-copilot-value")
    print("Grafana username: admin")
    print("Use the grafana command in an interactive terminal to set or reset the admin password.")
    return 0


def command_grafana(_arguments: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise CopilotValueError("Run grafana in an interactive terminal to enter the admin password securely.")
    password = getpass.getpass("Grafana admin password (at least 16 characters): ")
    if len(password) < 16 or "\n" in password or "\r" in password:
        raise CopilotValueError("Use at least 16 characters without line breaks for the Grafana password.")
    confirmation = getpass.getpass("Confirm Grafana admin password: ")
    if not secrets.compare_digest(password.encode("utf-8"), confirmation.encode("utf-8")):
        raise CopilotValueError("Grafana passwords did not match.")
    root = install_root()
    start_grafana_installation(root)
    reconcile_grafana_password(DockerRuntime(), root, password)
    return 0


def read_environment(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def write_environment(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def validate_package_feed_url(value: str, label: str) -> str:
    candidate = value.strip().strip("'\"")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in candidate)
        or "$" in candidate
    ):
        raise CopilotValueError(
            f"{label} must be a credential-free HTTPS URL without query or fragment values."
        )
    return candidate


def resolve_package_feeds(
    existing_environment: dict[str, str],
    *,
    npm_registry: str | None = None,
    pip_index_url: str | None = None,
) -> dict[str, str]:
    values = {
        "NPM_REGISTRY": (
            npm_registry
            or existing_environment.get("NPM_REGISTRY")
            or PUBLIC_NPM_REGISTRY
        ),
        "PIP_INDEX_URL": (
            pip_index_url
            or existing_environment.get("PIP_INDEX_URL")
            or PUBLIC_PYPI_INDEX
        ),
    }
    return {
        "NPM_REGISTRY": validate_package_feed_url(values["NPM_REGISTRY"], "npm registry"),
        "PIP_INDEX_URL": validate_package_feed_url(values["PIP_INDEX_URL"], "PyPI index"),
    }


def strip_jsonc(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        next_character = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "/" and next_character == "/":
            output.extend("  ")
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if character == "/" and next_character == "*":
            output.extend("  ")
            index += 2
            while index < len(text):
                if text[index] == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    output.extend("  ")
                    index += 2
                    break
                output.append(text[index] if text[index] in "\r\n" else " ")
                index += 1
            continue
        output.append(character)
        index += 1
    without_comments = "".join(output)
    cleaned: list[str] = []
    in_string = False
    escaped = False
    for index, character in enumerate(without_comments):
        if in_string:
            cleaned.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            cleaned.append(character)
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(without_comments) and without_comments[lookahead].isspace():
                lookahead += 1
            if lookahead < len(without_comments) and without_comments[lookahead] in "}]":
                continue
        cleaned.append(character)
    return "".join(cleaned)


def vscode_user_settings_paths() -> list[Path]:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        candidates = [
            base / "Code" / "User" / "settings.json",
            base / "Code - Insiders" / "User" / "settings.json",
            base / "VSCodium" / "User" / "settings.json",
        ]
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        candidates = [
            base / "Code" / "User" / "settings.json",
            base / "Code - Insiders" / "User" / "settings.json",
            base / "VSCodium" / "User" / "settings.json",
        ]
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
        candidates = [
            base / "Code" / "User" / "settings.json",
            base / "Code - Insiders" / "User" / "settings.json",
            base / "VSCodium" / "User" / "settings.json",
        ]
    existing = [path for path in candidates if path.exists() or path.parent.exists()]
    return existing or [candidates[0]]


def configure_vscode_user_settings(settings_path: Path, backup_root: Path) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    original = settings_path.read_text(encoding="utf-8-sig") if settings_path.exists() else "{}"
    try:
        settings = json.loads(strip_jsonc(original))
    except json.JSONDecodeError as error:
        raise CopilotValueError(
            f"Could not update VS Code user settings at '{settings_path}': {error}. "
            "Fix the JSONC file and run install again."
        ) from error
    if not isinstance(settings, dict):
        raise CopilotValueError(f"VS Code user settings at '{settings_path}' must be a JSON object.")
    if all(settings.get(key) == value for key, value in VSCODE_OTEL_SETTINGS.items()):
        return

    backup_root.mkdir(parents=True, exist_ok=True)
    installation_name = re.sub(r"[^a-z0-9_-]", "-", settings_path.parent.parent.name.lower())
    backup_path = backup_root / f"{installation_name or 'vscode'}-settings.jsonc"
    if settings_path.exists():
        backup_path.write_text(original, encoding="utf-8")
    settings.update(VSCODE_OTEL_SETTINGS)
    temporary_path = settings_path.with_suffix(".json.copilot-value.tmp")
    temporary_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, settings_path)
    print(f"Configured GitHub Copilot OTel in VS Code user settings: {settings_path}")
    if backup_path.exists():
        print(f"Previous VS Code settings backed up to: {backup_path}")


class WindowsUserEnvironment:
    """Persisted per-user environment values stored in HKCU\\Environment."""

    def __init__(self, registry: object | None = None) -> None:
        self._winreg = registry if registry is not None else importlib.import_module("winreg")

    def _open(self, *, write: bool):
        winreg = self._winreg
        if write:
            return winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                "Environment",
                0,
                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
            )
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_QUERY_VALUE)

    def get(self, name: str) -> tuple[str, int] | None:
        try:
            with self._open(write=False) as key:
                value, value_type = self._winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return str(value), int(value_type)

    def set(self, name: str, value: str, value_type: int | None = None) -> None:
        with self._open(write=True) as key:
            self._winreg.SetValueEx(
                key, name, 0, self._winreg.REG_SZ if value_type is None else value_type, value
            )

    def delete(self, name: str) -> None:
        with self._open(write=True) as key:
            try:
                self._winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def broadcast_environment_change() -> bool:
    # Best effort: lets Explorer and newly launched apps pick up the changed user environment.
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_message = user32.SendMessageTimeoutW
        send_message.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_size_t),
        )
        send_message.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t()
        return bool(
            send_message(
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                "Environment",
                SMTO_ABORTIFHUNG,
                2000,
                ctypes.byref(result),
            )
        )
    except Exception:
        return False


@dataclass(frozen=True)
class UserEnvironmentHost:
    platform: str
    home: Path
    environment: Mapping[str, str]
    registry: WindowsUserEnvironment | None = None
    broadcast: Callable[[], bool] = broadcast_environment_change


def copilot_env_platform() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def user_environment_host() -> UserEnvironmentHost:
    platform = copilot_env_platform()
    return UserEnvironmentHost(
        platform=platform,
        home=Path.home(),
        environment=dict(os.environ),
        registry=WindowsUserEnvironment() if platform == "windows" else None,
    )


def write_file_atomically(path: Path, data: bytes) -> None:
    # Write through symlinks so dotfile managers keep their links.
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(data)
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read_user_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", "surrogateescape")


def write_user_text(path: Path, text: str) -> None:
    write_file_atomically(path, text.encode("utf-8", "surrogateescape"))


def unique_backup_path(directory: Path, name: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidate = directory / f"{name}.{stamp}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{name}.{stamp}-{index}{suffix}"
        index += 1
    return candidate


def posix_config_home(host: UserEnvironmentHost) -> Path:
    configured = host.environment.get("XDG_CONFIG_HOME", "")
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return host.home / ".config"


def existing_startup_files(home: Path) -> list[Path]:
    return [home / name for name in POSIX_STARTUP_FILES if (home / name).is_file()]


def copilot_env_block_bounds(lines: Sequence[str]) -> tuple[int, int] | None:
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == COPILOT_ENV_BLOCK_START),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].strip() == COPILOT_ENV_BLOCK_END
        ),
        None,
    )
    if end is None:
        raise CopilotValueError(
            f"The Algalon block starting with '{COPILOT_ENV_BLOCK_START}' has no "
            f"'{COPILOT_ENV_BLOCK_END}' line. Remove the incomplete block and try again."
        )
    return start, end


def remove_copilot_env_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    while (bounds := copilot_env_block_bounds(lines)) is not None:
        start, end = bounds
        if start > 0 and not lines[start - 1].strip():
            start -= 1
        del lines[start : end + 1]
    return "".join(lines)


def with_copilot_env_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    bounds = copilot_env_block_bounds(lines)
    if bounds is None:
        base = text if not text or text.endswith("\n") else f"{text}\n"
        separator = "\n" if base.strip() else ""
        return f"{base}{separator}{COPILOT_ENV_BLOCK}"
    start, end = bounds
    remainder = remove_copilot_env_block("".join(lines[end + 1 :]))
    return "".join(lines[:start]) + COPILOT_ENV_BLOCK + remainder


def startup_file_values(path: Path, name: str) -> list[str]:
    try:
        text = read_user_text(path)
    except OSError:
        return []
    pattern = re.compile(
        rf"""^[ \t]*(?:export[ \t]+)?{re.escape(name)}=(?:"([^"\n]*)"|'([^'\n]*)'|([^\s#;]*))""",
        re.MULTILINE,
    )
    return [
        next(group for group in match.groups() if group is not None)
        for match in pattern.finditer(text)
    ]


def user_environment_values(host: UserEnvironmentHost, name: str) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if name in host.environment:
        values.append(("the current environment", host.environment[name]))
    if host.platform == "windows":
        try:
            stored = host.registry.get(name) if host.registry else None
        except OSError:
            stored = None
        if stored is not None:
            values.append((WINDOWS_ENVIRONMENT_KEY, stored[0]))
    else:
        for path in existing_startup_files(host.home):
            values.extend((str(path), value) for value in startup_file_values(path, name))
    return values


def first_conflicting_value(
    host: UserEnvironmentHost, name: str, accepted: Callable[[str], bool]
) -> tuple[str, str] | None:
    for source, value in user_environment_values(host, name):
        if value.strip() and not accepted(value.strip()):
            return source, value.strip()
    return None


def is_enabled_value(value: str) -> bool:
    return value.strip().casefold() == "true"


def is_algalon_collector_endpoint(value: str) -> bool:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and port == 4318
    )


def describe_endpoint(value: str) -> str:
    # Never echo credentials, paths, or query strings from a user's endpoint.
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.hostname:
        return "an unrecognized endpoint"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}{port}"


def copilot_env_conflicts(host: UserEnvironmentHost) -> list[str]:
    warnings: list[str] = []
    enabled_conflict = first_conflicting_value(host, COPILOT_OTEL_VARIABLE, is_enabled_value)
    if enabled_conflict:
        source, value = enabled_conflict
        warnings.append(
            f"COPILOT_OTEL_ENABLED is already set to '{value[:40]}' in {source}, so Algalon left "
            "it unchanged. Copilot CLI and SDK runtime telemetry may not reach Algalon until it "
            "is 'true'."
        )
    for name in COPILOT_OTEL_ENDPOINT_VARIABLES:
        endpoint_conflict = first_conflicting_value(host, name, is_algalon_collector_endpoint)
        if endpoint_conflict:
            source, value = endpoint_conflict
            warnings.append(
                f"{name} is set to {describe_endpoint(value)} in {source}, so Algalon did not "
                "enable COPILOT_OTEL_ENABLED. Copilot runtime telemetry would go to that endpoint "
                "instead of Algalon's local Collector at http://127.0.0.1:4318."
            )
    return warnings


def read_copilot_env_changes(backup_root: Path) -> list[dict[str, object]]:
    path = backup_root / COPILOT_ENV_MANIFEST
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CopilotValueError(f"Could not read '{path}': {error}") from error
    changes = payload.get("changes") if isinstance(payload, dict) else None
    if not isinstance(changes, list):
        raise CopilotValueError(f"'{path}' is not a valid Algalon environment backup manifest.")
    return [change for change in changes if isinstance(change, dict)]


def write_copilot_env_changes(backup_root: Path, changes: Sequence[dict[str, object]]) -> None:
    path = backup_root / COPILOT_ENV_MANIFEST
    if not changes:
        path.unlink(missing_ok=True)
        return
    payload = json.dumps({"version": 1, "changes": list(changes)}, indent=2) + "\n"
    write_file_atomically(path, payload.encode("utf-8"))


def apply_windows_copilot_env(
    host: UserEnvironmentHost, backup_root: Path, changes: list[dict[str, object]]
) -> list[str]:
    if host.registry is None:
        raise CopilotValueError("The Windows user environment registry is unavailable.")
    current = host.registry.get(COPILOT_OTEL_VARIABLE)
    if current is not None and is_enabled_value(current[0]):
        return []
    changes.append(
        {
            "kind": "registry",
            "name": COPILOT_OTEL_VARIABLE,
            "previous": None if current is None else {"value": current[0], "type": current[1]},
        }
    )
    write_copilot_env_changes(backup_root, changes)
    host.registry.set(COPILOT_OTEL_VARIABLE, "true")
    host.broadcast()
    return [f"Enabled COPILOT_OTEL_ENABLED=true for this user ({WINDOWS_ENVIRONMENT_KEY})."]


def posix_copilot_env_targets(host: UserEnvironmentHost) -> list[tuple[str, Path, str]]:
    startup_files = existing_startup_files(host.home)
    if not startup_files:
        # zsh ignores ~/.profile, so a zsh login shell gets ~/.zshrc instead.
        shell = Path(host.environment.get("SHELL", "")).name
        startup_files = [host.home / (".zshrc" if shell == "zsh" else ".profile")]
    targets = [("block", path, COPILOT_ENV_BLOCK) for path in startup_files]
    config_home = posix_config_home(host)
    if (config_home / "fish").is_dir():
        targets.append(("file", config_home / FISH_COPILOT_ENV_FILE, FISH_COPILOT_ENV_CONTENT))
    if host.platform == "linux":
        targets.append(
            ("file", config_home / SYSTEMD_COPILOT_ENV_FILE, SYSTEMD_COPILOT_ENV_CONTENT)
        )
    return targets


def apply_posix_copilot_env(
    host: UserEnvironmentHost, backup_root: Path, changes: list[dict[str, object]]
) -> list[str]:
    messages: list[str] = []
    for kind, path, content in posix_copilot_env_targets(host):
        existing = read_user_text(path) if path.is_file() else None
        try:
            updated = with_copilot_env_block(existing or "") if kind == "block" else content
        except CopilotValueError as error:
            raise CopilotValueError(f"{path}: {error}") from error
        if existing == updated:
            continue
        backup: Path | None = None
        if existing is not None:
            relative = (
                path.relative_to(host.home).as_posix()
                if path.is_relative_to(host.home)
                else path.name
            )
            backup_name = re.sub(r"[^A-Za-z0-9._-]+", "_", relative.replace("/", "__"))
            backup = unique_backup_path(backup_root, backup_name.lstrip("._") or "file", ".backup")
            shutil.copy2(path, backup)
        changes.append(
            {
                "kind": kind,
                "path": str(path),
                "created": existing is None,
                "backup": backup.name if backup else None,
            }
        )
        write_copilot_env_changes(backup_root, changes)
        write_user_text(path, updated)
        messages.append(f"Enabled COPILOT_OTEL_ENABLED=true in {path}")
    return messages


def revert_copilot_env_change(
    change: dict[str, object], host: UserEnvironmentHost, backup_root: Path
) -> str | None:
    kind = change.get("kind")
    if kind == "registry":
        name = str(change.get("name") or COPILOT_OTEL_VARIABLE)
        current = host.registry.get(name) if host.registry else None
        if current is None or host.registry is None:
            return None
        if not is_enabled_value(current[0]):
            return (
                f"{name} in {WINDOWS_ENVIRONMENT_KEY} changed after Algalon enabled it, so it "
                "was left unchanged."
            )
        previous = change.get("previous")
        if isinstance(previous, dict):
            value_type = previous.get("type")
            host.registry.set(
                name,
                str(previous.get("value", "")),
                value_type if isinstance(value_type, int) else None,
            )
            message = f"Restored the previous {name} value in {WINDOWS_ENVIRONMENT_KEY}."
        else:
            host.registry.delete(name)
            message = f"Removed {name} from {WINDOWS_ENVIRONMENT_KEY}."
        host.broadcast()
        return message
    raw_path = change.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path)
    if kind == "block":
        if not path.is_file():
            return None
        original = read_user_text(path)
        remaining = remove_copilot_env_block(original)
        if change.get("created") and not remaining.strip():
            path.unlink()
            return f"Deleted {path}"
        if remaining == original:
            return None
        write_user_text(path, remaining)
        return f"Removed the Algalon COPILOT_OTEL_ENABLED block from {path}"
    if kind == "file":
        backup_name = change.get("backup")
        backup = backup_root / backup_name if isinstance(backup_name, str) and backup_name else None
        if backup is not None and backup.is_file():
            write_file_atomically(path, backup.read_bytes())
            return f"Restored {path} from {backup}"
        if path.is_file():
            path.unlink()
            return f"Deleted {path}"
    return None


def enable_copilot_otel_environment(
    backup_root: Path, host: UserEnvironmentHost | None = None
) -> str:
    host = host or user_environment_host()
    try:
        conflicts = copilot_env_conflicts(host)
        changes = read_copilot_env_changes(backup_root)
    except (OSError, CopilotValueError) as error:
        print(f"Warning: Could not inspect COPILOT_OTEL_ENABLED for this user: {error}")
        return "failed"
    if conflicts:
        for warning in conflicts:
            print(f"Warning: {warning}")
        return "conflict"
    recorded = len(changes)
    apply = apply_windows_copilot_env if host.platform == "windows" else apply_posix_copilot_env
    try:
        messages = apply(host, backup_root, changes)
    except (OSError, CopilotValueError) as error:
        try:
            for change in reversed(changes[recorded:]):
                revert_copilot_env_change(change, host, backup_root)
            write_copilot_env_changes(backup_root, changes[:recorded])
            rollback = "No user environment changes were kept."
        except (OSError, CopilotValueError) as rollback_error:
            rollback = (
                f"Some changes could not be rolled back ({rollback_error}); run "
                f"restore-copilot-env. Backups: {backup_root}"
            )
        manual = (
            "run: setx COPILOT_OTEL_ENABLED true"
            if host.platform == "windows"
            else "add 'export COPILOT_OTEL_ENABLED=true' to your shell startup file"
        )
        print(f"Warning: Could not enable COPILOT_OTEL_ENABLED=true for this user: {error}")
        print(f"{rollback} To enable it manually, {manual}.")
        return "failed"
    if not messages:
        print("COPILOT_OTEL_ENABLED=true is already enabled for this user.")
        return "unchanged"
    for message in messages:
        print(message)
    print(f"Run restore-copilot-env to revert these changes. Backups: {backup_root}")
    return "enabled"


def configure_copilot_environment(backup_root: Path) -> str:
    if (backup_root / COPILOT_ENV_OPT_OUT).exists():
        print(
            "COPILOT_OTEL_ENABLED was left unchanged because restore-copilot-env opted this "
            "installation out. Run enable-copilot-env to enable it again."
        )
        return "opted-out"
    return enable_copilot_otel_environment(backup_root)


def restore_copilot_otel_environment(
    backup_root: Path, host: UserEnvironmentHost | None = None
) -> int:
    host = host or user_environment_host()
    changes = read_copilot_env_changes(backup_root)
    pending = [(change, True) for change in reversed(changes)]
    if host.platform == "windows":
        if not any(change.get("kind") == "registry" for change in changes):
            try:
                current = host.registry.get(COPILOT_OTEL_VARIABLE) if host.registry else None
            except OSError:
                current = None
            if current is not None:
                print(
                    f"COPILOT_OTEL_ENABLED in {WINDOWS_ENVIRONMENT_KEY} was not set by this "
                    "installation, so it was left unchanged."
                )
    else:
        # Marked blocks and Algalon-named files are removable even if the manifest was lost.
        recorded = {change.get("path") for change in changes}
        config_home = posix_config_home(host)
        sweep: list[dict[str, object]] = [
            {"kind": "block", "path": str(path), "created": False}
            for path in existing_startup_files(host.home)
        ]
        sweep.extend(
            {"kind": "file", "path": str(path), "created": True, "backup": None}
            for path in (config_home / FISH_COPILOT_ENV_FILE, config_home / SYSTEMD_COPILOT_ENV_FILE)
        )
        pending.extend((change, False) for change in sweep if change["path"] not in recorded)
    remaining: list[dict[str, object]] = []
    errors: list[str] = []
    reverted = False
    for change, is_recorded in pending:
        try:
            message = revert_copilot_env_change(change, host, backup_root)
        except (OSError, CopilotValueError) as error:
            errors.append(f"{change.get('path') or change.get('name')}: {error}")
            if is_recorded:
                remaining.insert(0, change)
            continue
        if message:
            reverted = True
            print(message)
    write_copilot_env_changes(backup_root, remaining)
    if errors:
        for error in errors:
            print(f"Warning: Could not restore {error}")
        print(f"Fix the reported files and run restore-copilot-env again. Backups: {backup_root}")
        return 1
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / COPILOT_ENV_OPT_OUT).write_text("restore-copilot-env\n", encoding="utf-8")
    if not reverted:
        print("No Algalon-managed COPILOT_OTEL_ENABLED settings were found.")
    print(
        "Install and upgrade now leave COPILOT_OTEL_ENABLED unchanged; run enable-copilot-env "
        "to enable it again. Open a new terminal (or restart the Copilot app) to apply the change."
    )
    return 0


def copilot_otel_environment_status(host: UserEnvironmentHost) -> tuple[bool, str]:
    try:
        if host.platform == "windows":
            stored = host.registry.get(COPILOT_OTEL_VARIABLE) if host.registry else None
            if stored is not None and stored[0].strip():
                if is_enabled_value(stored[0]):
                    return True, f"enabled for this user ({WINDOWS_ENVIRONMENT_KEY})"
                return False, f"set to '{stored[0].strip()[:40]}' ({WINDOWS_ENVIRONMENT_KEY})"
        else:
            config_home = posix_config_home(host)
            configured = [
                path.name
                for path in existing_startup_files(host.home)
                if COPILOT_ENV_BLOCK_START in read_user_text(path)
            ]
            configured.extend(
                path.name
                for path in (config_home / FISH_COPILOT_ENV_FILE, config_home / SYSTEMD_COPILOT_ENV_FILE)
                if path.is_file()
            )
            if configured:
                return True, f"enabled for new shells ({', '.join(configured)})"
    except OSError as error:
        return False, f"unknown ({error})"
    value = host.environment.get(COPILOT_OTEL_VARIABLE, "").strip()
    if is_enabled_value(value):
        return True, "enabled in the current environment"
    if value:
        return False, f"set to '{value[:40]}' in the current environment"
    return False, "not enabled"


def copilot_cli_status(timeout_seconds: float = 5) -> tuple[str, str | None] | None:
    # Resolve like the user's shell: first PATH entry wins, PATHEXT applies on Windows.
    executable = shutil.which("copilot")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return "installed (version unavailable)", None
    match = (
        re.search(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)", result.stdout or "")
        if result.returncode == 0
        else None
    )
    if match is None:
        return "installed (version unavailable)", None
    version = ".".join(match.groups())
    if tuple(int(part) for part in match.groups()) < MINIMUM_COPILOT_CLI_VERSION:
        minimum = ".".join(map(str, MINIMUM_COPILOT_CLI_VERSION))
        return version, (
            f"Copilot CLI {version} is older than {minimum}. Upgrade it so its runtime "
            "telemetry uses the attribute names Algalon reads."
        )
    return version, None


def report_copilot_runtime_telemetry(host: UserEnvironmentHost | None = None) -> None:
    host = host or user_environment_host()
    enabled, detail = copilot_otel_environment_status(host)
    cli = copilot_cli_status()
    print(f"{'COPILOT_OTEL_ENABLED':24} {detail}")
    print(f"{'Copilot CLI':24} {cli[0] if cli else 'not found on PATH'}")
    if not enabled:
        print(
            "Warning: Copilot CLI and SDK sessions are not measured until "
            "COPILOT_OTEL_ENABLED=true. Run enable-copilot-env, then open a new terminal."
        )
    if cli and cli[1]:
        print(f"Warning: {cli[1]}")
    for name in COPILOT_OTEL_ENDPOINT_VARIABLES:
        conflict = first_conflicting_value(host, name, is_algalon_collector_endpoint)
        if conflict:
            print(
                f"Warning: {name} is set to {describe_endpoint(conflict[1])} in {conflict[0]}, "
                "so Copilot runtime telemetry goes there instead of Algalon."
            )


def runtime_migration_backup(
    url: str = RUNTIME_MIGRATION_URL, timeout_seconds: float = 2
) -> tuple[str, int | None] | None:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("completed") is not True:
        return None
    backup_path = payload.get("backupPath")
    if not isinstance(backup_path, str) or not backup_path.strip():
        return None
    cleared = payload.get("clearedSessions")
    if not isinstance(cleared, int) or isinstance(cleared, bool) or cleared < 0:
        cleared = None
    return backup_path.strip(), cleared


def print_runtime_migration_backup(root: Path) -> None:
    migration = runtime_migration_backup()
    if migration is None:
        return
    backup_path, cleared = migration
    project = read_environment(root / "docker" / ".env").get("COMPOSE_PROJECT_NAME")
    volume = f"{project}_telemetry-data" if project else "telemetry-data"
    detail = f" ({cleared} legacy session(s) cleared)" if cleared is not None else ""
    print(
        f"Legacy VS Code data was backed up to '{backup_path}' inside Docker volume "
        f"'{volume}'{detail}."
    )


def write_gitignore(repository_root: Path) -> None:
    path = repository_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if ".copilot-value/" in existing.splitlines():
        return
    separator = "" if not existing else "\n" if existing.endswith("\n") else "\n\n"
    path.write_text(
        f"{existing}{separator}# Local Algalon runtime and telemetry\n.copilot-value/\n",
        encoding="utf-8",
    )
    print(f"Added '.copilot-value/' to {path}.")


def write_local_config(root: Path) -> Path:
    local_path = root / "config" / "value-model.local.json"
    if local_path.is_file():
        return local_path
    if local_path.is_dir():
        if any(local_path.iterdir()):
            raise CopilotValueError(
                f"Expected '{local_path}' to be a file, but it is a nonempty directory. "
                "Move that directory aside and run install again."
            )
        retry_file_operation(
            lambda: remove_path(local_path),
            f"empty Docker bind placeholder '{local_path}'",
        )
    example_path = root / "config" / "value-model.example.json"
    config = json.loads(example_path.read_text(encoding="utf-8"))
    config["benchmark"]["acknowledgedAssumptions"] = True
    local_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("Created value-model.local.json and enabled the bundled modeled assumptions.")
    return local_path


def is_phase_pattern_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(patterns, list) and all(isinstance(pattern, str) for pattern in patterns)
        for patterns in value.values()
    )


def json_member_span(text: str, path: Sequence[str]) -> tuple[int, int] | None:
    """Return the source span of a nested object member value in valid JSON text."""
    decoder = json.JSONDecoder()

    def skip_whitespace(index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    index = skip_whitespace(0)
    for key in path:
        if index >= len(text) or text[index] != "{":
            return None
        index = skip_whitespace(index + 1)
        found: int | None = None
        while index < len(text) and text[index] != "}":
            name, index = decoder.raw_decode(text, index)
            index = skip_whitespace(skip_whitespace(index) + 1)
            if name == key:
                found = index
            _, index = decoder.raw_decode(text, index)
            index = skip_whitespace(index)
            if index < len(text) and text[index] == ",":
                index = skip_whitespace(index + 1)
        if found is None:
            return None
        index = found
    _, end = decoder.raw_decode(text, index)
    return index, end


def render_json_value_like(text: str, start: int, end: int, value: object) -> str:
    """Render a replacement value in the indentation and array style of the original span."""
    original = text[start:end]
    if "\n" not in original:
        return json.dumps(value)
    newline = "\r\n" if "\r\n" in text else "\n"
    line_start = text.rfind("\n", 0, start) + 1
    base_indent = re.match(r"[ \t]*", text[line_start:start]).group()
    inner = re.search(r"\n([ \t]*)\S", original)
    unit = "  "
    if inner and inner.group(1).startswith(base_indent) and len(inner.group(1)) > len(base_indent):
        unit = inner.group(1)[len(base_indent) :]
    first_array = re.search(r"\[\s*[^\]\s][^\]]*\]", original)
    if isinstance(value, dict) and first_array and "\n" not in first_array.group():
        members = [
            f"{base_indent}{unit}{json.dumps(key)}: {json.dumps(item)}"
            for key, item in value.items()
        ]
        rendered = "{\n" + ",\n".join(members) + f"\n{base_indent}}}"
    else:
        rendered = json.dumps(value, indent=unit).replace("\n", f"\n{base_indent}")
    return rendered.replace("\n", newline)


def mark_phase_tool_patterns_migrated(root: Path) -> None:
    marker = root / PHASE_TOOL_PATTERN_MIGRATION_MARKER
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("migrated\n", encoding="utf-8")
    except OSError:
        pass


def migrate_phase_tool_patterns(root: Path, local_path: Path) -> bool:
    """Move benchmark.phaseToolPatterns from VS Code tool names to Copilot runtime tool names."""
    if (root / PHASE_TOOL_PATTERN_MIGRATION_MARKER).exists():
        return False
    try:
        example = json.loads(
            (root / "config" / "value-model.example.json").read_text(encoding="utf-8")
        )
        runtime_defaults = example["benchmark"]["phaseToolPatterns"]
        raw = local_path.read_bytes()
        bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
        text = raw[len(bom) :].decode("utf-8")
        config = json.loads(text)
    except (OSError, LookupError, TypeError, ValueError) as error:
        print(f"Warning: Could not migrate benchmark.phaseToolPatterns in '{local_path}': {error}")
        return False
    if not is_phase_pattern_map(runtime_defaults):
        print("Warning: The bundled example config has no valid benchmark.phaseToolPatterns.")
        return False
    if isinstance(config, dict) and "benchmark" not in config:
        mark_phase_tool_patterns_migrated(root)
        return False
    benchmark = config.get("benchmark") if isinstance(config, dict) else None
    patterns = benchmark.get("phaseToolPatterns") if isinstance(benchmark, dict) else None
    if not is_phase_pattern_map(patterns):
        print(
            f"Warning: Left benchmark.phaseToolPatterns in '{local_path}' unchanged because it "
            "is not an object of pattern lists."
        )
        return False
    if patterns == PREVIOUS_VSCODE_PHASE_TOOL_PATTERNS:
        updated = {phase: list(defaults) for phase, defaults in runtime_defaults.items()}
        note = (
            "Replaced the previous VS Code benchmark.phaseToolPatterns defaults in "
            f"'{local_path}' with the GitHub Copilot runtime defaults."
        )
    else:
        updated = {phase: list(entries) for phase, entries in patterns.items()}
        for phase, defaults in runtime_defaults.items():
            entries = updated.setdefault(phase, [])
            for pattern in defaults:
                if pattern not in entries:
                    entries.append(pattern)
        note = (
            "Added missing GitHub Copilot runtime tool patterns to your customized "
            f"benchmark.phaseToolPatterns in '{local_path}'."
        )
    if updated == patterns:
        mark_phase_tool_patterns_migrated(root)
        return False
    span = json_member_span(text, ("benchmark", "phaseToolPatterns"))
    expected = json.loads(text)
    expected["benchmark"]["phaseToolPatterns"] = updated
    migrated_text = (
        text[: span[0]] + render_json_value_like(text, *span, updated) + text[span[1] :]
        if span
        else ""
    )
    try:
        safe = bool(span) and json.loads(migrated_text) == expected
    except ValueError:
        safe = False
    if not safe:
        print(
            f"Warning: Left benchmark.phaseToolPatterns in '{local_path}' unchanged because it "
            "could not be updated without reformatting the file."
        )
        return False
    try:
        backup = unique_backup_path(root / CONFIG_BACKUP_DIRECTORY, local_path.stem, local_path.suffix)
        shutil.copy2(local_path, backup)
        write_file_atomically(local_path, bom + migrated_text.encode("utf-8"))
    except OSError as error:
        print(f"Warning: Could not migrate benchmark.phaseToolPatterns in '{local_path}': {error}")
        return False
    mark_phase_tool_patterns_migrated(root)
    print(f"{note} Backup: '{backup}'.")
    return True


def command_install(arguments: argparse.Namespace) -> int:
    archive_path = execution_archive()
    runtime: DockerRuntime | None = None
    package_feeds: dict[str, str] | None = None
    previous_stack_running = False
    previous_grafana_running = False
    if archive_path:
        repository_root = (
            Path(arguments.repository_root).expanduser().resolve()
            if arguments.repository_root
            else Path.cwd().resolve()
        )
        root = repository_root / ".copilot-value"
        staging_root = stage_archive(archive_path, root)
        try:
            existing_compose = root / "docker" / "compose.yaml"
            existing_environment = root / "docker" / ".env"
            package_feeds = resolve_package_feeds(
                read_environment(existing_environment),
                npm_registry=getattr(arguments, "npm_registry", None),
                pip_index_url=getattr(arguments, "pip_index_url", None),
            )
            if (
                not arguments.skip_docker_check
                and existing_compose.exists()
                and existing_environment.exists()
            ):
                runtime = DockerRuntime()
                runtime.require_compose()
                previous_services = running_services(runtime, root)
                previous_stack_running = bool(set(SERVICES) & previous_services)
                previous_grafana_running = "grafana" in previous_services
                compose(runtime, root, ("--profile", "grafana", "down"), capture=True)
            deploy_staged_archive(staging_root, root)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            if previous_stack_running and runtime:
                try:
                    start_installation(root, force_recreate=True)
                    if previous_grafana_running:
                        start_grafana_installation(root)
                except Exception:
                    pass
            raise
    else:
        root = install_root()
        if root.name != ".copilot-value":
            raise CopilotValueError(
                "Run the release as a .pyz application from the target repository, or extract "
                "the release into '<repository>/.copilot-value' before running this installer. "
                f"Current installation: '{root}'."
            )
        repository_root = (
            Path(arguments.repository_root).expanduser().resolve()
            if arguments.repository_root
            else root.parent.resolve()
        )
    if root.parent.resolve() != repository_root:
        raise CopilotValueError(
            f"The .copilot-value directory must be directly under repository root '{repository_root}'."
        )

    write_gitignore(repository_root)
    local_config_path = write_local_config(root)
    config_migrated = migrate_phase_tool_patterns(root, local_config_path)
    runtime = runtime or DockerRuntime(required=not arguments.skip_docker_check)

    data_root = root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    try:
        (data_root / "vscode-workspace-storage-empty").rmdir()
    except OSError:
        pass

    environment_path = root / "docker" / ".env"
    existing_environment = read_environment(environment_path)
    package_feeds = package_feeds or resolve_package_feeds(
        existing_environment,
        npm_registry=getattr(arguments, "npm_registry", None),
        pip_index_url=getattr(arguments, "pip_index_url", None),
    )
    repository_name = re.sub(r"[^a-z0-9_-]", "-", repository_root.name.lower()) or "repository"
    repository_hash = hashlib.sha256(str(repository_root).casefold().encode()).hexdigest()[:8]
    project_name = f"copilot-value-{repository_name}-{repository_hash}"
    write_environment(
        environment_path,
        {
            "COMPOSE_PROJECT_NAME": project_name,
            "SESSION_POLL_SECONDS": str(arguments.poll_seconds),
            **package_feeds,
        },
    )
    if "VSCODE_WORKSPACE_STORAGE_PATH" in existing_environment:
        print(
            "Removed the obsolete VS Code workspaceStorage mount; Algalon now measures GitHub "
            "Copilot runtime telemetry only."
        )
    (data_root / "grafana-admin-password.txt").unlink(missing_ok=True)
    (root / ".docker-install").write_text("docker\n", encoding="utf-8")
    if not getattr(arguments, "skip_vscode_settings", False):
        backup_root = data_root / "vscode-settings-backups"
        for settings_path in vscode_user_settings_paths():
            configure_vscode_user_settings(settings_path, backup_root)
        print("Reload VS Code before starting a new Copilot session.")
    copilot_env_outcome = "skipped"
    if not getattr(arguments, "skip_copilot_env", False):
        copilot_env_outcome = configure_copilot_environment(
            data_root / COPILOT_ENV_BACKUP_DIRECTORY
        )

    if not arguments.skip_docker_check:
        runtime.require_compose()
        compose(runtime, root, ("config", "--quiet"))
    if not arguments.no_start:
        start_installation(root, force_recreate=bool(archive_path) or config_migrated)
        if previous_grafana_running:
            start_grafana_installation(root)

    print()
    print(f"Algalon is installed at '{root}'.")
    print(f"Configuration: {local_config_path}")
    print(f"Session refresh interval: {arguments.poll_seconds} second(s)")
    print(f"Docker npm registry: {package_feeds['NPM_REGISTRY']}")
    print(f"Docker PyPI index: {package_feeds['PIP_INDEX_URL']}")
    print("Algalon: http://127.0.0.1:3000")
    print("Optional Grafana profile: http://127.0.0.1:3001")
    print("Optional Grafana credentials are configured securely by the grafana command.")
    print("Copilot sessions from all local repositories are measured automatically.")
    print(
        COPILOT_ENV_NOTE
        if copilot_env_outcome in {"enabled", "unchanged"}
        else VSCODE_HARNESS_NOTE
    )
    if not arguments.no_start:
        print_runtime_migration_backup(root)
    return 0


def assert_docker_install(root: Path) -> None:
    if not (root / ".docker-install").exists():
        raise CopilotValueError("This command supports repository-local Docker installations only. Run install first.")


def start_installation(root: Path, *, force_recreate: bool = False) -> int:
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.keep_wsl_alive(root)
    runtime.require_compose()
    up_arguments = ["up", "-d", "--build"]
    if force_recreate:
        up_arguments.append("--force-recreate")
    compose(runtime, root, tuple(up_arguments))
    for name, url in HEALTH_ENDPOINTS:
        wait_for_endpoint(name, url)
    wait_for_otlp_ingress()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        running = compose(runtime, root, ("ps", "--status", "running", "--services"), capture=True)
        if {"worker", "app"}.issubset(set(running.stdout.splitlines())):
            print("Algalon automatic session measurement is running.")
            print("Algalon: http://127.0.0.1:3000")
            print("No manual experiment start or completion is required.")
            return 0
        time.sleep(0.5)
    compose(runtime, root, ("ps",))
    raise CopilotValueError("Session measurement worker did not become healthy.")


def command_start(_arguments: argparse.Namespace) -> int:
    return start_installation(install_root())


def command_stop(_arguments: argparse.Namespace) -> int:
    root = install_root()
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.require_compose()
    try:
        compose(runtime, root, ("--profile", "grafana", "down"))
    finally:
        runtime.release_wsl_keepalive(root)
    print("Algalon session measurement stopped. Persistent Docker volumes were retained.")
    return 0


def command_restart(arguments: argparse.Namespace) -> int:
    root = install_root()
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.require_compose()
    grafana_was_running = "grafana" in running_services(runtime, root)
    command_stop(arguments)
    result = command_start(arguments)
    if grafana_was_running:
        start_grafana_installation(root)
    return result


def log_tail_value(value: str) -> str:
    if value == "all":
        return value
    try:
        lines = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("tail must be a positive integer or 'all'") from error
    if lines <= 0:
        raise argparse.ArgumentTypeError("tail must be a positive integer or 'all'")
    return str(lines)


def command_logs(arguments: argparse.Namespace) -> int:
    root = install_root()
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.require_compose()
    log_arguments = ["logs", "--tail", arguments.tail]
    if arguments.timestamps:
        log_arguments.append("--timestamps")
    if arguments.follow:
        runtime.keep_wsl_alive(root)
        log_arguments.append("--follow")
    log_arguments.extend(arguments.services)
    compose(runtime, root, tuple(log_arguments))
    return 0


def command_clean_data(arguments: argparse.Namespace) -> int:
    if not arguments.yes:
        raise CopilotValueError(
            "clean-data permanently deletes this installation's local telemetry, session, "
            "SQLite, metrics, and Grafana volumes. Re-run with --yes to confirm."
        )
    root = install_root()
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.require_compose()
    services = running_services(runtime, root)
    was_running = bool(set(SERVICES) & services)
    grafana_was_running = "grafana" in services
    try:
        compose(
            runtime,
            root,
            ("--profile", "grafana", "down", "--volumes", "--remove-orphans"),
        )
    finally:
        runtime.release_wsl_keepalive(root)
    print(
        "Deleted local Algalon evidence volumes for this installation. Configuration, "
        "credentials, VS Code backups, and installation files were preserved."
    )
    if was_running and not arguments.no_start:
        start_installation(root)
    if grafana_was_running and not arguments.no_start:
        start_grafana_installation(root)
    return 0


def command_status(_arguments: argparse.Namespace) -> int:
    root = install_root()
    assert_docker_install(root)
    runtime = DockerRuntime()
    runtime.keep_wsl_alive(root)
    runtime.require_compose()
    running = running_services(runtime, root)
    failed = False
    print(f"{'COMPONENT':24} HEALTHY")
    for service in SERVICES:
        healthy = service in running
        failed = failed or not healthy
        print(f"{service:24} {'yes' if healthy else 'no'}")
    for name, url in HEALTH_ENDPOINTS:
        healthy = endpoint_healthy(url)
        failed = failed or not healthy
        print(f"{name:24} {'yes' if healthy else 'no'}")
    healthy = otlp_ingress_healthy()
    failed = failed or not healthy
    print(f"{'Collector OTLP':24} {'yes' if healthy else 'no'}")
    print()
    report_copilot_runtime_telemetry()
    print_runtime_migration_backup(root)
    return 1 if failed else 0


def copilot_env_backup_root() -> Path:
    return install_root() / "data" / COPILOT_ENV_BACKUP_DIRECTORY


def command_restore_copilot_env(_arguments: argparse.Namespace) -> int:
    return restore_copilot_otel_environment(copilot_env_backup_root())


def command_enable_copilot_env(_arguments: argparse.Namespace) -> int:
    backup_root = copilot_env_backup_root()
    (backup_root / COPILOT_ENV_OPT_OUT).unlink(missing_ok=True)
    if enable_copilot_otel_environment(backup_root) not in {"enabled", "unchanged"}:
        return 1
    print(COPILOT_ENV_NOTE)
    return 0


def run_checked(command: Sequence[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise CopilotValueError(
            f"Command failed with exit code {result.returncode}: {' '.join(map(str, command))}"
        )


def run_captured(command: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CopilotValueError(
            f"Command failed with exit code {result.returncode}: {' '.join(map(str, command))}"
            + (f"\n{detail}" if detail else "")
        )
    return result.stdout


def copy_release_path(project_root: Path, payload_root: Path, relative_path: str) -> None:
    source = project_root / relative_path
    destination = payload_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    else:
        shutil.copy2(source, destination)


def validate_release_compose(compose_text: str) -> None:
    if OBSOLETE_COMPOSE_PATTERN.search(compose_text):
        raise CopilotValueError(
            "Release Compose configuration still mounts VS Code workspaceStorage or reads "
            "direct-turn chat logs."
        )


def write_archive(source_root: Path, destination: Path) -> str:
    destination.unlink(missing_ok=True)
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_root).as_posix())
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def command_bundle(arguments: argparse.Namespace) -> int:
    project_root = install_root()
    package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    output_directory = Path(arguments.output_directory)
    if not output_directory.is_absolute():
        output_directory = project_root / output_directory
    output_directory = output_directory.resolve()
    bundle_name = f"copilot-value-dashboard-{package['version']}"
    bundle_path = output_directory / f"{bundle_name}.zip"
    hash_path = Path(f"{bundle_path}.sha256")
    application_path = output_directory / f"{bundle_name}.pyz"
    application_hash_path = Path(f"{application_path}.sha256")
    staging_root = output_directory / ".staging"
    payload_root = staging_root / bundle_name
    npm = (
        shutil.which("npm.cmd") or shutil.which("npm")
        if os.name == "nt"
        else shutil.which("npm")
    )
    if not npm:
        raise CopilotValueError("Node.js and npm are required to build the release bundle.")

    output_directory.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(staging_root, ignore_errors=True)
    payload_root.mkdir(parents=True)
    try:
        npm_command = (npm, "run", "build", "--silent") if arguments.skip_tests else (npm, "test")
        run_checked(npm_command, cwd=project_root)

        release_paths = (
            "dist/shared",
            "dist/src",
            "shared",
            "scripts/copilot_value.py",
            "scripts/docker_runtime.py",
            "scripts/errors.py",
            "scripts/release_smoke.py",
            "backend/app",
            "backend/requirements.txt",
            "web/package.json",
            "web/package-lock.json",
            "web/tsconfig.json",
            "web/vite.config.ts",
            "web/index.html",
            "web/mock",
            "web/src",
            "config/otel-collector.yaml",
            "config/value-model.example.json",
            "config/grafana",
            "docker/App.Dockerfile",
            "docker/Dockerfile",
            "docker/compose.yaml",
            "docker/.env.example",
            ".dockerignore",
            "LICENSE",
            "SECURITY.md",
            "README.md",
            "docs/images",
            "docs/local-deployment/README.md",
            "docs/roi-formula-evolution.md",
        )
        for relative_path in release_paths:
            copy_release_path(project_root, payload_root, relative_path)

        web_package_path = payload_root / "web" / "package.json"
        web_package = json.loads(web_package_path.read_text(encoding="utf-8"))
        web_build_dependencies = {
            "@types/react",
            "@types/react-dom",
            "@vitejs/plugin-react",
            "typescript",
            "vite",
        }
        web_package["devDependencies"] = {
            name: version
            for name, version in web_package.get("devDependencies", {}).items()
            if name in web_build_dependencies
        }
        web_package_path.write_text(
            json.dumps(web_package, indent=2) + "\n", encoding="utf-8"
        )
        run_checked(
            (npm, "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"),
            cwd=payload_root / "web",
        )

        runtime_package = {
            "name": "copilot-value-dashboard-runtime",
            "version": package["version"],
            "private": True,
            "type": "module",
            "engines": {"node": ">=22"},
            "dependencies": package["dependencies"],
        }
        (payload_root / "package.json").write_text(
            json.dumps(runtime_package, indent=2) + "\n", encoding="utf-8"
        )
        run_checked(
            (npm, "install", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"),
            cwd=payload_root,
        )
        if not (payload_root / "package-lock.json").exists():
            raise CopilotValueError("Production dependency installation did not create package-lock.json.")

        for forbidden_path in ("src", "test", "tsconfig.json"):
            if (payload_root / forbidden_path).exists():
                raise CopilotValueError(
                    f"Release staging unexpectedly contains '{forbidden_path}'."
                )
        collector_config = (payload_root / "config" / "otel-collector.yaml").read_text(
            encoding="utf-8"
        )
        if re.search(r"aspire|14317|14318|18888", collector_config, re.IGNORECASE):
            raise CopilotValueError("Release Collector configuration still references Aspire.")
        validate_release_compose(
            (payload_root / "docker" / "compose.yaml").read_text(encoding="utf-8")
        )

        digest = write_archive(payload_root, bundle_path)
        hash_path.write_text(f"{digest}  {bundle_path.name}\n", encoding="utf-8")

        (payload_root / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (payload_root / "__main__.py").write_text(
            "import runpy\nrunpy.run_module('scripts.copilot_value', run_name='__main__')\n",
            encoding="utf-8",
        )
        application_digest = write_archive(payload_root, application_path)
        application_hash_path.write_text(
            f"{application_digest}  {application_path.name}\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    print(f"Name: {bundle_name}")
    print(f"BundlePath: {bundle_path}")
    print(f"Sha256: {digest}")
    print(f"Sha256Path: {hash_path}")
    print(f"ApplicationPath: {application_path}")
    print(f"ApplicationSha256: {application_digest}")
    print(f"ApplicationSha256Path: {application_hash_path}")
    return 0



def run_telemetry_smoke(root: Path | None = None) -> str:
    return _run_telemetry_smoke(root)


def command_smoke_installation(arguments: argparse.Namespace) -> int:
    root = install_root()
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
        "docs/images/algalon-portfolio-desktop.png",
        "docs/images/algalon-methodology-overview.png",
    )
    for relative_path in required_paths:
        if not (root / relative_path).exists():
            raise CopilotValueError(f"Installation is missing '{relative_path}'.")
    for forbidden_path in ("src", "test", "tsconfig.json"):
        if (root / forbidden_path).exists():
            raise CopilotValueError(
                f"Release installation unexpectedly contains development path '{forbidden_path}'."
            )
    collector_config = (root / "config" / "otel-collector.yaml").read_text(encoding="utf-8")
    if re.search(r"aspire|14317|14318|18888", collector_config, re.IGNORECASE):
        raise CopilotValueError("Collector release configuration still references Aspire.")
    json.loads(
        (root / "config" / "grafana" / "dashboards" / "personal-copilot-value.json").read_text(
            encoding="utf-8"
        )
    )

    node = shutil.which("node.exe" if os.name == "nt" else "node")
    if not node:
        raise CopilotValueError("Node.js 22 or later is required for the installation smoke test.")
    node_major = int(run_captured((node, "-p", "process.versions.node.split('.')[0]")).strip())
    if node_major < 22:
        raise CopilotValueError("Node.js 22 or later is required for the installation smoke test.")

    smoke_id = uuid.uuid4().hex
    repository_install = root.name == ".copilot-value"
    temporary_target: Path | None = None
    target_root = root.parent if repository_install else Path(tempfile.mkdtemp(prefix="copilot-value-target-"))
    if not repository_install:
        temporary_target = target_root
    experiment_root = root / "data" / "value" / "experiments"
    work_root = root / "data" / "value" / "smoke" / smoke_id
    snapshot_path = experiment_root / f"bundle-smoke-{smoke_id}.source-baseline.json"
    source_directory = target_root / "copilot-value-smoke-source"
    source_path = source_directory / "app.ts"
    try:
        if repository_install:
            command_install(
                argparse.Namespace(
                    repository_root=None,
                    poll_seconds=1,
                    no_start=True,
                    skip_docker_check=True,
                    skip_vscode_settings=True,
                    skip_copilot_env=True,
                )
            )
        experiment_root.mkdir(parents=True, exist_ok=True)
        work_root.mkdir(parents=True, exist_ok=True)
        source_directory.mkdir(parents=True, exist_ok=True)
        source_path.write_text("export const value = 1;\n", encoding="utf-8")
        run_checked(
            (
                node,
                str(root / "dist" / "src" / "source-delta-cli.js"),
                "snapshot",
                "--output",
                str(snapshot_path),
                "--captured-at",
                "2026-08-05T12:00:00Z",
                "--source-path",
                str(target_root),
            )
        )
        source_path.write_text(
            "export const value = 2;\nexport const added = true;\n", encoding="utf-8"
        )
        diff_output = run_captured(
            (
                node,
                str(root / "dist" / "src" / "source-delta-cli.js"),
                "diff",
                "--snapshot",
                str(snapshot_path),
                "--captured-at",
                "2026-08-05T12:01:00Z",
            )
        )
        delta = json.loads([line for line in diff_output.splitlines() if line][-1])
        if (
            delta["filesModified"] != 1
            or delta["filesAdded"] != 0
            or delta["charactersAdded"] <= 0
            or delta["charactersRemoved"] <= 0
        ):
            raise CopilotValueError(f"Bundled source diff produced unexpected evidence: {delta}")

        fixture_path = work_root / "otel-snapshot.json"
        fixture_path.write_text(
            json.dumps(
                {
                    "day": "2026-08-01",
                    "activeDay": 1,
                    "sessions": 2,
                    "agentInvocations": 3,
                    "agentTurns": 8,
                    "toolCalls": 10,
                    "successfulToolCalls": 9,
                    "codingToolCalls": 3,
                    "researchToolCalls": 4,
                    "planningToolCalls": 2,
                    "unmappedToolCalls": 1,
                    "acceptedEditDecisions": 7,
                    "rejectedEditDecisions": 2,
                    "unmappedEditDecisions": 0,
                    "agentEditLoc": 40,
                    "appliedUserActions": 5,
                    "editSurvivalFourGram": 0.75,
                    "editSurvivalNoRevert": 0.8,
                }
            ),
            encoding="utf-8",
        )
        calculation_output = run_captured(
            (
                node,
                str(root / "dist" / "src" / "cli.js"),
                "calculate",
                "--config",
                str(root / "config" / "value-model.example.json"),
                "--day",
                "2026-08-01",
                "--fixture",
                str(fixture_path),
                "--no-publish",
                "--no-write",
            )
        )
        calculation = json.loads(calculation_output)
        if calculation["sessions"] != 2 or calculation["codingToolCalls"] != 3:
            raise CopilotValueError("Bundled fixture calculation returned unexpected values.")

        if arguments.telemetry:
            for name, url in HEALTH_ENDPOINTS:
                if not endpoint_healthy(url, 3):
                    raise CopilotValueError(f"Telemetry dependency is unhealthy: {name} at {url}")
            run_telemetry_smoke(root)

        print("Portable installation smoke passed.")
        return 0
    finally:
        snapshot_path.unlink(missing_ok=True)
        shutil.rmtree(work_root, ignore_errors=True)
        shutil.rmtree(source_directory, ignore_errors=True)
        if temporary_target:
            shutil.rmtree(temporary_target, ignore_errors=True)


def command_smoke_bundle(
    arguments: argparse.Namespace,
    after_telemetry_smoke: Callable[[], None] | None = None,
) -> int:
    project_root = install_root()
    if after_telemetry_smoke is not None and arguments.skip_telemetry:
        raise CopilotValueError("A post-telemetry check requires the Docker stack.")
    package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    if arguments.bundle:
        bundle_path = Path(arguments.bundle).expanduser()
        if not bundle_path.is_absolute():
            bundle_path = project_root / bundle_path
        bundle_path = bundle_path.resolve()
    else:
        bundle_path = project_root / "artifacts" / f"copilot-value-dashboard-{package['version']}.pyz"
    if not arguments.skip_build:
        command_bundle(
            argparse.Namespace(output_directory=str(bundle_path.parent), skip_tests=False)
        )
    if not bundle_path.exists():
        raise CopilotValueError(f"Bundle not found at '{bundle_path}'.")

    extract_root = Path(tempfile.mkdtemp(prefix="copilot-value-bundle-"))
    repository_root = extract_root / "repository"
    extracted_root = repository_root / ".copilot-value"
    repository_root.mkdir(parents=True)
    runtime: DockerRuntime | None = None
    stack_attempted = False
    stopped_containers: list[str] = []
    try:
        if bundle_path.suffix.lower() == ".pyz":
            install_command = [
                sys.executable,
                str(bundle_path),
                "install",
                "--repository-root",
                str(repository_root),
                "--no-start",
                "--poll-seconds",
                "1",
            ]
        else:
            extracted_root.mkdir(parents=True)
            with zipfile.ZipFile(bundle_path) as archive:
                archive.extractall(extracted_root)
            install_command = [
                sys.executable,
                str(extracted_root / "scripts" / "copilot_value.py"),
                "install",
                "--no-start",
                "--poll-seconds",
                "1",
            ]
        extracted_cli = extracted_root / "scripts" / "copilot_value.py"
        install_command.extend(("--skip-vscode-settings", "--skip-copilot-env"))
        if arguments.skip_telemetry:
            install_command.append("--skip-docker-check")
        run_checked(install_command)

        if arguments.skip_telemetry:
            run_checked((sys.executable, str(extracted_cli), "smoke-installation"))
        else:
            runtime = DockerRuntime()
            runtime.require_compose()
            for port in (4317, 4318, 13133, 3000, 3001, 8428):
                result = runtime.run(
                    ("ps", "--filter", f"publish={port}", "--format", "{{.ID}}"),
                    capture=True,
                )
                stopped_containers.extend(line for line in result.stdout.splitlines() if line)
            stopped_containers = sorted(set(stopped_containers))
            for container_id in stopped_containers:
                runtime.run(("stop", container_id), capture=True)

            stack_attempted = True
            run_checked((sys.executable, str(extracted_cli), "start"))
            time.sleep(3)
            run_checked((sys.executable, str(extracted_cli), "smoke-installation", "--telemetry"))
            if after_telemetry_smoke is not None:
                after_telemetry_smoke()

        print(f"Release bundle smoke passed: {bundle_path}")
        if arguments.keep_extracted:
            print(f"Extracted repository retained at: {repository_root}")
        return 0
    finally:
        if stack_attempted and runtime:
            compose(
                runtime,
                extracted_root,
                ("--profile", "grafana", "down", "--volumes", "--remove-orphans"),
                capture=True,
                check=False,
            )
            runtime.release_wsl_keepalive(extracted_root)
        if runtime:
            for container_id in stopped_containers:
                runtime.run(("start", container_id), capture=True, check=False)
        if not arguments.keep_extracted:
            shutil.rmtree(extract_root, ignore_errors=True)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and operate the Algalon Docker stack.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install Algalon in the containing repository.")
    install.add_argument("--repository-root")
    install.add_argument("--poll-seconds", type=int, choices=range(1, 61), default=5)
    install.add_argument(
        "--npm-registry",
        help="Credential-free HTTPS npm registry for Docker builds; defaults to the public registry.",
    )
    install.add_argument(
        "--pip-index-url",
        help="Credential-free HTTPS PyPI index for Docker builds; defaults to the public index.",
    )
    install.add_argument("--no-start", action="store_true")
    install.add_argument("--skip-docker-check", action="store_true", help=argparse.SUPPRESS)
    install.add_argument("--skip-vscode-settings", action="store_true", help=argparse.SUPPRESS)
    install.add_argument("--skip-copilot-env", action="store_true", help=argparse.SUPPRESS)
    install.set_defaults(handler=command_install)

    for name, handler, help_text in (
        ("start", command_start, "Start the Docker stack."),
        ("stop", command_stop, "Stop the Docker stack while preserving volumes."),
        ("restart", command_restart, "Restart the Docker stack while preserving volumes."),
        ("status", command_status, "Report Docker service and endpoint health."),
        ("grafana", command_grafana, "Start the optional Grafana profile on port 3001."),
        (
            "enable-copilot-env",
            command_enable_copilot_env,
            "Set COPILOT_OTEL_ENABLED=true for Copilot CLI and SDK sessions of this user.",
        ),
        (
            "restore-copilot-env",
            command_restore_copilot_env,
            "Revert the COPILOT_OTEL_ENABLED user environment changes made by Algalon.",
        ),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.set_defaults(handler=handler)

    logs = subparsers.add_parser("logs", help="Show logs for the deployed stack.")
    logs.add_argument("services", nargs="*", choices=(*SERVICES, "grafana"))
    logs.add_argument("--tail", type=log_tail_value, default="200")
    logs.add_argument("--timestamps", action="store_true")
    logs.add_argument("--follow", action="store_true")
    logs.set_defaults(handler=command_logs)

    clean_data = subparsers.add_parser(
        "clean-data", help="Permanently delete this installation's local evidence volumes."
    )
    clean_data.add_argument("--yes", action="store_true", help="Confirm permanent deletion.")
    clean_data.add_argument(
        "--no-start", action="store_true", help="Leave a previously running stack stopped."
    )
    clean_data.set_defaults(handler=command_clean_data)

    bundle = subparsers.add_parser("bundle", help="Build a platform-neutral release archive.")
    bundle.add_argument("--output-directory", default="artifacts")
    bundle.add_argument("--skip-tests", action="store_true")
    bundle.set_defaults(handler=command_bundle)

    smoke_installation = subparsers.add_parser(
        "smoke-installation", help="Validate an extracted release installation."
    )
    smoke_installation.add_argument("--telemetry", action="store_true")
    smoke_installation.set_defaults(handler=command_smoke_installation)

    smoke_bundle = subparsers.add_parser(
        "smoke-bundle", help="Extract and validate a platform-neutral release bundle."
    )
    smoke_bundle.add_argument("--bundle")
    smoke_bundle.add_argument("--skip-build", action="store_true")
    smoke_bundle.add_argument("--skip-telemetry", action="store_true")
    smoke_bundle.add_argument("--keep-extracted", action="store_true")
    smoke_bundle.set_defaults(handler=command_smoke_bundle)
    return parser


def command_line_arguments(arguments: Sequence[str], archive_path: Path | None) -> list[str]:
    return ["install"] if not arguments and archive_path else list(arguments)


def main() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(map(str, MINIMUM_PYTHON))
        raise CopilotValueError(f"Python {required} or later is required.")
    parser = create_parser()
    arguments = parser.parse_args(command_line_arguments(sys.argv[1:], execution_archive()))
    return arguments.handler(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CopilotValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
