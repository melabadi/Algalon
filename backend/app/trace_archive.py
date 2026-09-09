from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def attributes(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for attribute in items or []:
        key = attribute.get("key")
        value = attribute.get("value") or {}
        if not key:
            continue
        for value_key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if value_key in value:
                result[key] = value[value_key]
                break
    return result


def milliseconds(value: Any) -> int:
    try:
        return int(str(value or "0")) // 1_000_000
    except (TypeError, ValueError):
        return 0


def iter_json_lines(path: Path, *, skip_invalid: bool = False) -> Iterable[dict[str, Any]]:
    try:
        source = path.open(encoding="utf-8")
    except FileNotFoundError:
        return
    pending: str | None = None
    with source:
        for line in source:
            if not line.strip():
                continue
            if pending is not None:
                try:
                    yield json.loads(pending)
                except json.JSONDecodeError:
                    if not skip_invalid:
                        raise
            pending = line
    if pending is not None:
        try:
            yield json.loads(pending)
        except json.JSONDecodeError:
            pass


def read_json_lines(path: Path, *, skip_invalid: bool = False) -> list[dict[str, Any]]:
    return list(iter_json_lines(path, skip_invalid=skip_invalid))


def archive_paths(path: Path) -> list[Path]:
    rotated = sorted(
        candidate
        for candidate in path.parent.glob(f"{path.stem}-*{path.suffix}")
        if candidate.is_file()
    )
    return [*rotated, *([path] if path.exists() else [])]


def archive_snapshot(path: Path) -> tuple[tuple[Path, int, int], ...]:
    snapshot: list[tuple[Path, int, int]] = []
    for candidate in archive_paths(path):
        try:
            candidate_stat = candidate.stat()
        except FileNotFoundError:
            continue
        snapshot.append((candidate, candidate_stat.st_mtime_ns, candidate_stat.st_size))
    return tuple(snapshot)


def archive_signature(
    snapshot: tuple[tuple[Path, int, int], ...],
) -> tuple[tuple[str, int, int], ...]:
    return tuple((candidate.name, modified, size) for candidate, modified, size in snapshot)


def collect_trace_sessions(trace_paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    def records() -> Iterable[dict[str, Any]]:
        for trace_path in trace_paths:
            yield from iter_json_lines(trace_path)

    return collect_trace_sessions_from_records(records())


def collect_trace_sessions_from_records(
    records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = {}
    seen_spans: set[str] = set()
    for record in records:
        for resource_span in record.get("resourceSpans") or []:
            resource_attributes = attributes(
                (resource_span.get("resource") or {}).get("attributes")
            )
            if resource_attributes.get("service.name") != "copilot-chat":
                continue
            raw_session_id = resource_attributes.get("session.id")
            if not raw_session_id:
                continue
            session = sessions.setdefault(
                str(raw_session_id), {"start": 2**63 - 1, "spans": []}
            )
            for scope_span in resource_span.get("scopeSpans") or []:
                for span in scope_span.get("spans") or []:
                    identity = str(
                        span.get("spanId")
                        or f"{span.get('traceId')}:{span.get('name')}:{span.get('startTimeUnixNano')}"
                    )
                    if identity in seen_spans:
                        continue
                    seen_spans.add(identity)
                    started = milliseconds(span.get("startTimeUnixNano"))
                    if started <= 0:
                        continue
                    session["start"] = min(session["start"], started)
                    session["spans"].append({**span, "_started": started})
    return sessions