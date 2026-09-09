from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import subprocess
from typing import Sequence

try:
    from scripts.errors import CopilotValueError
except ModuleNotFoundError:
    from errors import CopilotValueError


class DockerRuntime:
    def __init__(self, *, required: bool = True) -> None:
        docker = shutil.which("docker")
        wsl = shutil.which("wsl.exe") if os.name == "nt" else None
        if docker:
            self._prefix = [docker]
            self._wsl = None
        elif wsl:
            self._prefix = [wsl, "--", "docker"]
            self._wsl = wsl
        elif required:
            raise CopilotValueError("Docker with Compose support is required.")
        else:
            self._prefix = []
            self._wsl = None

    @property
    def available(self) -> bool:
        return bool(self._prefix)

    def keep_wsl_alive(self, root: Path) -> None:
        if not self._wsl:
            return
        marker = root / "data" / "wsl-keepalive"
        marker.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        marker.write_text(token, encoding="utf-8")
        marker_path = self.docker_path(marker)
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        subprocess.Popen(
            [
                self._wsl,
                "--",
                "sh",
                "-lc",
                'while [ "$(cat "$1" 2>/dev/null)" = "$2" ]; do sleep 30; done',
                "copilot-value-keepalive",
                marker_path,
                token,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )

    def release_wsl_keepalive(self, root: Path) -> None:
        if self._wsl:
            (root / "data" / "wsl-keepalive").unlink(missing_ok=True)

    def run(
        self,
        arguments: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if not self.available:
            raise CopilotValueError("Docker with Compose support is required.")
        result = subprocess.run(
            [*self._prefix, *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            input=input_text,
        )
        if check and result.returncode != 0:
            raise CopilotValueError(
                f"Docker command failed with exit code {result.returncode}: {' '.join(arguments)}"
            )
        return result

    def require_compose(self) -> None:
        self.run(("version",), capture=True)
        self.run(("compose", "version"), capture=True)

    def docker_path(self, path: Path) -> str:
        resolved = path.resolve()
        if not self._wsl:
            return resolved.as_posix() if os.name == "nt" else str(resolved)
        result = subprocess.run(
            [self._wsl, "wslpath", "-a", "-u", str(resolved).replace("\\", "/")],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        converted = result.stdout.strip()
        if result.returncode != 0 or not converted:
            raise CopilotValueError(f"Could not convert '{resolved}' for the WSL Docker daemon.")
        return converted
