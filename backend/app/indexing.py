from __future__ import annotations

import sqlite3
from hashlib import sha256
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
from time import time
from typing import Any, Callable

from .session_identity import public_session_digest
from .trace_archive import attributes


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MIN_FREE_DATABASE_BYTES = 64 * 1024 * 1024


class IndexingBlocked(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_storage_capacity(directory: Path) -> None:
    if shutil.disk_usage(directory).free < MIN_FREE_DATABASE_BYTES:
        raise IndexingBlocked("storage_pressure")


def initialize_indexing(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS indexing_work (
            work_key TEXT PRIMARY KEY,
            session_id TEXT UNIQUE,
            session_digest TEXT,
            experiment TEXT,
            requested_version INTEGER NOT NULL DEFAULT 1,
            indexed_version INTEGER NOT NULL DEFAULT 0,
            latest_cursor INTEGER NOT NULL DEFAULT 0,
            indexed_cursor INTEGER NOT NULL DEFAULT 0,
            pending_since REAL,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            last_attempt_at REAL NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            last_success_at REAL
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_indexing_pending
        ON indexing_work(next_attempt_at, last_attempt_at)
        WHERE requested_version > indexed_version
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_indexing_digest ON indexing_work(session_digest)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS indexing_changes (
            work_key TEXT NOT NULL REFERENCES indexing_work(work_key) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            queued_at REAL NOT NULL,
            PRIMARY KEY(work_key, version)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS indexing_artifacts (
            file_name TEXT PRIMARY KEY,
            experiment TEXT NOT NULL,
            signature TEXT NOT NULL,
            payload_json TEXT,
            error_code TEXT
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_indexing_artifact_experiment ON indexing_artifacts(experiment)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS indexing_conversations (
            session_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            PRIMARY KEY(session_id, conversation_id)
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_indexing_conversation
        ON indexing_conversations(conversation_id, session_id)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS indexing_logs (
            conversation_id TEXT PRIMARY KEY,
            signature TEXT NOT NULL
        )
    """)
    if not connection.execute("SELECT 1 FROM otel_records LIMIT 1").fetchone():
        connection.execute(
            "INSERT OR IGNORE INTO store_metadata (key, value) VALUES ('indexing_queue_v1', 'complete')"
        )


def bootstrap_indexing(connection: sqlite3.Connection, page_size: int = 5_000) -> None:
    if connection.execute("SELECT 1 FROM store_metadata WHERE key = 'indexing_queue_v1'").fetchone():
        return
    previous = connection.execute("SELECT value FROM store_metadata WHERE key = 'indexing_bootstrap_cursor'").fetchone()
    cursor = int(previous[0]) if previous else 0
    rows = connection.execute(
        "SELECT cursor, session_id, record_json FROM otel_records WHERE cursor > ? ORDER BY cursor LIMIT ?",
        (cursor, page_size),
    ).fetchall()
    with connection:
        for row in rows:
            if not connection.execute("SELECT 1 FROM indexing_work WHERE session_id = ?", (row["session_id"],)).fetchone():
                latest = connection.execute(
                    "SELECT MAX(cursor) FROM otel_records WHERE session_id = ?", (row["session_id"],)
                ).fetchone()[0]
                enqueue_session(connection, row["session_id"], latest)
            record_conversations(connection, row["session_id"], row["record_json"])
        if rows:
            connection.execute("""
                INSERT INTO store_metadata VALUES ('indexing_bootstrap_cursor', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (str(rows[-1]["cursor"]),))
        if len(rows) < page_size:
            connection.execute("INSERT OR REPLACE INTO store_metadata VALUES ('indexing_queue_v1', 'complete')")


def enqueue_session(
    connection: sqlite3.Connection, session_id: str, latest_cursor: int = 0,
) -> None:
    enqueue_work(
        connection, "trace:" + session_id, session_id=session_id,
        digest=sha256(session_id.encode("utf-8")).hexdigest()[:10],
        latest_cursor=latest_cursor,
    )


def enqueue_work(
    connection: sqlite3.Connection, work_key: str, *, session_id: str | None = None,
    digest: str | None = None, experiment: str | None = None, latest_cursor: int = 0,
) -> None:
    statement = (
        "INSERT INTO indexing_work (work_key, session_id, session_digest, experiment, latest_cursor, pending_since) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(work_key) DO UPDATE SET "
        "requested_version = indexing_work.requested_version + 1, "
        "experiment = COALESCE(excluded.experiment, indexing_work.experiment), "
        "latest_cursor = MAX(indexing_work.latest_cursor, excluded.latest_cursor), "
        "pending_since = CASE WHEN indexing_work.requested_version = indexing_work.indexed_version "
        "THEN excluded.pending_since ELSE COALESCE(indexing_work.pending_since, excluded.pending_since) END, "
        "next_attempt_at = 0, failure_count = 0, error_code = NULL RETURNING requested_version"
    )
    queued_at = time()
    version = connection.execute(
        statement, (work_key, session_id, digest, experiment, latest_cursor, queued_at)
    ).fetchone()[0]
    connection.execute("INSERT INTO indexing_changes VALUES (?, ?, ?)", (work_key, version, queued_at))


def retry_work(connection: sqlite3.Connection, work: sqlite3.Row, code: str) -> None:
    failures = work["failure_count"] + 1
    connection.execute("""
        UPDATE indexing_work SET
            failure_count = CASE WHEN requested_version = ? THEN ? ELSE failure_count END,
            error_code = CASE WHEN requested_version = ? THEN ? ELSE error_code END,
            next_attempt_at = CASE WHEN requested_version = ? THEN ? ELSE next_attempt_at END,
            last_attempt_at = ?
        WHERE work_key = ?
    """, (
        work["requested_version"], failures, work["requested_version"], code,
        work["requested_version"], time() + min(300, 2 ** min(failures, 8)), time(), work["work_key"],
    ))


def complete_work(connection: sqlite3.Connection, work: sqlite3.Row) -> None:
    connection.execute(
        "DELETE FROM indexing_changes WHERE work_key = ? AND version <= ?",
        (work["work_key"], work["requested_version"]),
    )
    connection.execute("""
        UPDATE indexing_work SET indexed_version = ?, indexed_cursor = ?,
            last_success_at = ?, last_attempt_at = ?, failure_count = 0,
            error_code = NULL, next_attempt_at = 0,
            pending_since = (SELECT MIN(queued_at) FROM indexing_changes WHERE work_key = ?)
        WHERE work_key = ?
    """, (
        work["requested_version"], work["latest_cursor"], time(), time(),
        work["work_key"], work["work_key"],
    ))


def record_conversations(connection: sqlite3.Connection, session_id: str, payload: str) -> None:
    try:
        record = json.loads(payload)
    except (ValueError, RecursionError):
        return
    if not isinstance(record, dict) or not isinstance(record.get("resourceSpans"), list):
        return
    for resource in record["resourceSpans"]:
        if not isinstance(resource, dict) or not isinstance(resource.get("scopeSpans"), list):
            continue
        for scope in resource.get("scopeSpans", []):
            if not isinstance(scope, dict) or not isinstance(scope.get("spans"), list):
                continue
            for span in scope.get("spans", []):
                if not isinstance(span, dict) or not isinstance(span.get("attributes"), list):
                    continue
                safe_attributes = [value for value in span["attributes"] if (
                    isinstance(value, dict) and isinstance(value.get("value"), dict)
                )]
                values = attributes(safe_attributes)
                conversation = values.get("gen_ai.conversation.id") or values.get("copilot_chat.chat_session_id")
                if conversation:
                    connection.execute("""
                        INSERT INTO indexing_conversations VALUES (?, ?)
                        ON CONFLICT DO NOTHING
                    """, (session_id, str(conversation)))


def enqueue_artifact(connection: sqlite3.Connection, experiment: str, changed: bool) -> None:
    matches = connection.execute(
        "SELECT work_key FROM indexing_work WHERE session_digest = ? AND session_id IS NOT NULL",
        (public_session_digest(experiment),),
    ).fetchall()
    artifact_key = "artifact:" + experiment
    if len(matches) == 1:
        removed = connection.execute("DELETE FROM indexing_work WHERE work_key = ?", (artifact_key,)).rowcount
        connection.execute("UPDATE indexing_work SET experiment = ? WHERE work_key = ?", (experiment, matches[0][0]))
        if changed or removed:
            enqueue_work(connection, matches[0][0], experiment=experiment)
    elif changed:
        enqueue_work(connection, artifact_key, experiment=experiment)


def stage_artifacts(
    connection: sqlite3.Connection, directory: Path, parse_json: Callable[[str], Any],
) -> None:
    known = {row["file_name"]: row for row in connection.execute(
        "SELECT file_name, experiment, signature, error_code FROM indexing_artifacts"
    )}
    updates: list[tuple[str, str, str, str | None, str | None]] = []
    present: set[str] = set()
    for path in sorted(directory.glob("session-*.json")):
        present.add(path.name)
        previous = known.get(path.name)
        experiment = previous["experiment"] if previous else path.stem
        signature = "unavailable"
        try:
            stat = path.stat()
            signature = json.dumps([stat.st_mtime_ns, stat.st_size])
            if previous and previous["signature"] == signature and not previous["error_code"]:
                continue
            if stat.st_size > MAX_ARTIFACT_BYTES:
                raise IndexingBlocked("artifact_too_large")
            text = path.read_text(encoding="utf-8")
            artifact = parse_json(text)
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                continue
            if not isinstance(artifact, dict) or not isinstance(artifact.get("experiment"), str) or not artifact["experiment"]:
                raise IndexingBlocked("invalid_artifact")
            experiment = artifact["experiment"]
            error = None
        except (OSError, UnicodeError, ValueError, RecursionError, IndexingBlocked) as failure:
            text = None
            error = failure.code if isinstance(failure, IndexingBlocked) else "artifact_unavailable"
        if previous and (signature, error) == (previous["signature"], previous["error_code"]) and error:
            continue
        updates.append((path.name, experiment, signature, text, error))
    for name, previous in known.items():
        if name not in present and previous["error_code"] != "artifact_unavailable":
            updates.append((name, previous["experiment"], "unavailable", None, "artifact_unavailable"))
    with connection:
        for name, experiment, signature, text, error in updates:
            connection.execute("""
                INSERT INTO indexing_artifacts VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_name) DO UPDATE SET experiment = excluded.experiment,
                    signature = excluded.signature, payload_json = excluded.payload_json,
                    error_code = excluded.error_code
            """, (name, experiment, signature, text, error))
            enqueue_artifact(connection, experiment, True)
        for row in connection.execute("SELECT experiment FROM indexing_artifacts").fetchall():
            enqueue_artifact(connection, row[0], False)


def stage_log_changes(connection: sqlite3.Connection, logs: dict[str, tuple[Path, int, int]]) -> None:
    previous = {row[0]: row[1] for row in connection.execute("SELECT conversation_id, signature FROM indexing_logs")}
    current = {identifier: json.dumps([str(path), modified, size]) for identifier, (path, modified, size) in logs.items()}
    changed = {identifier for identifier in previous.keys() | current.keys() if previous.get(identifier) != current.get(identifier)}
    with connection:
        for identifier in changed:
            for row in connection.execute(
                "SELECT session_id FROM indexing_conversations WHERE conversation_id = ?", (identifier,)
            ).fetchall():
                enqueue_session(connection, row[0])
            if identifier in current:
                connection.execute("""
                    INSERT INTO indexing_logs VALUES (?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET signature = excluded.signature
                """, (identifier, current[identifier]))
            else:
                connection.execute("DELETE FROM indexing_logs WHERE conversation_id = ?", (identifier,))


def write_prompts(connection: sqlite3.Connection, experiment: str, groups: list[dict[str, Any]]) -> None:
    from .session_identity import prompt_id

    connection.execute("DELETE FROM prompts WHERE experiment = ?", (experiment,))
    connection.executemany("""
        INSERT INTO prompts (
            prompt_id, experiment, ordinal, started_at, content, captured_content_length,
            model_requests, tool_calls, input_tokens, cache_read_tokens, output_tokens,
            reasoning_tokens, ai_cost_usd, models_json, ai_credits, usage_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        (
            prompt_id(experiment, group["ordinal"], group["started_at"]),
            experiment, group["ordinal"], group["started_at"], group["content"],
            group["captured_content_length"], group["model_requests"], group["tool_calls"],
            group["input_tokens"], group["cache_read_tokens"], group["output_tokens"],
            group["reasoning_tokens"], group["ai_cost_usd"], json.dumps(group["models"], separators=(",", ":")),
            group["ai_credits"], group["usage_source"],
        ) for group in groups
    ))


def indexing_status(connection: sqlite3.Connection, experiment: str | None = None) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT * FROM indexing_work" + (" WHERE experiment = ?" if experiment else ""),
        (experiment,) if experiment else (),
    ).fetchall()
    pending = [row for row in rows if row["requested_version"] > row["indexed_version"]]
    waiting_codes = {"waiting_for_artifact", "waiting_for_worker", "waiting_for_telemetry"}
    blocked = [row for row in pending if row["error_code"] and row["error_code"] not in waiting_codes]
    oldest = max((time() - row["pending_since"] for row in pending if row["pending_since"] is not None), default=0)
    metadata = dict(connection.execute(
        "SELECT key, value FROM store_metadata WHERE key IN ('indexing_last_discovery', 'indexing_discovery_error', 'indexing_queue_v1')"
    ).fetchall())
    last_discovery = float(metadata.get("indexing_last_discovery", 0))
    reason = metadata.get("indexing_discovery_error") or None
    if not reason and blocked:
        reason = blocked[0]["error_code"]
    if not reason and (oldest >= 60 or last_discovery and time() - last_discovery >= 60):
        reason = "stale"
    state = "blocked" if reason else "catching_up" if pending or not last_discovery or "indexing_queue_v1" not in metadata else "current"
    last_success = max((row["last_success_at"] or 0 for row in rows), default=0)
    return {
        "state": state,
        "pendingSessions": len(pending),
        "blockedSessions": len(blocked),
        "oldestPendingSeconds": round(max(0, oldest), 1),
        "lastSuccessfulAt": datetime.fromtimestamp(last_success, timezone.utc).isoformat() if last_success else None,
        "lastDiscoveryAt": datetime.fromtimestamp(last_discovery, timezone.utc).isoformat() if last_discovery else None,
        "reason": reason,
    }