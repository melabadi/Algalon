from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from .indexing import IndexingBlocked
from .prompt_index import read_copilot_turns


LOG_IO_TIMEOUT_SECONDS = 5
MAX_DISCOVERED_LOGS = 10_000


def discover_logs(root: Path | None) -> dict[str, tuple[Path, int, int]]:
    result: dict[str, tuple[Path, int, int]] = {}
    if root is None or not root.exists():
        return result
    for path in sorted(root.glob("*/GitHub.copilot-chat/debug-logs/*/main.jsonl")):
        if path.parent.name in result:
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        result[path.parent.name] = (path, stat.st_mtime_ns, stat.st_size)
        if len(result) > MAX_DISCOVERED_LOGS:
            raise IndexingBlocked("log_discovery_limit")
    return result


def isolated_log_io(operation: str, path: Path, *arguments: int) -> Any:
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-m", __name__, operation, str(path), *(str(value) for value in arguments)],
            capture_output=True, text=True, encoding="utf-8", timeout=LOG_IO_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as failure:
        raise IndexingBlocked("log_io_timeout") from failure
    if result.returncode:
        raise IndexingBlocked("log_unavailable")
    return json.loads(result.stdout)


def main() -> None:
    operation, name, *arguments = sys.argv[1:]
    path = Path(name)
    if operation == "discover":
        result = {
            identifier: [str(candidate), modified, size]
            for identifier, (candidate, modified, size) in discover_logs(path).items()
        }
    elif operation == "turns":
        result = read_copilot_turns(path, *(int(value) for value in arguments))
    else:
        raise ValueError("Unknown log operation")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)