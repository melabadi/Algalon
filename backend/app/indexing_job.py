from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from .indexing import IndexingBlocked


PREPARATION_TIMEOUT_SECONDS = 15


def prepare_in_process(
    database: Path, work: dict[str, Any], content_enabled: bool,
    log_index: dict[str, tuple[Path, int, int]], log_error: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    payload = {
        "database": str(database), "work": work, "contentEnabled": content_enabled,
        "logs": {identifier: [str(path), modified, size] for identifier, (path, modified, size) in log_index.items()},
        "logError": log_error,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-m", __name__], input=json.dumps(payload),
            text=True, encoding="utf-8", capture_output=True,
            timeout=PREPARATION_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as failure:
        raise IndexingBlocked("preparation_timeout") from failure
    if result.returncode:
        raise IndexingBlocked("preparation_failed")
    output = json.loads(result.stdout)
    if "error" in output:
        raise IndexingBlocked(output["error"])
    return output["artifact"], output["groups"]


def main() -> None:
    from .store import ValueStore

    payload = json.load(sys.stdin)
    database = Path(payload["database"]).resolve()

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    store = ValueStore.__new__(ValueStore)
    store._connect = connect
    store.isolate_log_io = False
    store._log_discovery_error = payload["logError"]
    store._chat_log_index = {
        identifier: (Path(entry[0]), entry[1], entry[2])
        for identifier, entry in payload["logs"].items()
    }
    try:
        artifact, groups = store._prepare_index_work(payload["work"], payload["contentEnabled"])
        result = {"artifact": artifact, "groups": groups}
    except IndexingBlocked as failure:
        result = {"error": failure.code}
    except Exception:
        result = {"error": "preparation_failed"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()