from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re


PUBLIC_SESSION_PATTERN = re.compile(r"session-\d{8}-\d{6}-([a-f0-9]{10})")


def session_digest(raw_session_id: str) -> str:
    return hashlib.sha256(raw_session_id.encode()).hexdigest()[:10]


def public_session_id(raw_session_id: str, started_milliseconds: int) -> str:
    started = datetime.fromtimestamp(started_milliseconds / 1000, timezone.utc)
    return f"session-{started:%Y%m%d-%H%M%S}-{session_digest(raw_session_id)}"


def public_session_digest(experiment: str) -> str | None:
    match = PUBLIC_SESSION_PATTERN.fullmatch(experiment)
    return match.group(1) if match else None


def prompt_id(experiment: str, ordinal: int, started_at: str) -> str:
    digest = hashlib.sha256(f"{experiment}:{ordinal}:{started_at}".encode()).hexdigest()[:16]
    return f"prompt-{ordinal:03d}-{digest}"
