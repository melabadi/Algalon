from __future__ import annotations

import csv
from io import StringIO
import re
from typing import Any, Mapping


_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYMS = {
    "ai": "AI",
    "id": "ID",
    "otel": "OTel",
    "roi": "ROI",
    "url": "URL",
    "usd": "USD",
}


def _label(value: str) -> str:
    words = _WORD_BOUNDARY.sub(" ", value).replace("_", " ").split()
    return " ".join(_ACRONYMS.get(word.lower(), word.title()) for word in words)


def _cell(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def _flatten(prefix: str, value: Any, output: dict[str, str | int | float]) -> None:
    if isinstance(value, Mapping):
        if not value and prefix:
            output[prefix] = ""
            return
        for key, nested_value in value.items():
            nested_prefix = f"{prefix} > {_label(str(key))}" if prefix else _label(str(key))
            _flatten(nested_prefix, nested_value, output)
        return
    if isinstance(value, (list, tuple)):
        if not value and prefix:
            output[prefix] = ""
            return
        for index, nested_value in enumerate(value, start=1):
            _flatten(f"{prefix} > {index}", nested_value, output)
        return
    output[prefix] = _cell(value)


def build_csv_export(
    sessions: list[dict[str, Any]],
    prompts_by_session: Mapping[str, list[dict[str, Any]]],
) -> bytes:
    rows: list[dict[str, str | int | float]] = []

    for session in sessions:
        session_values: dict[str, str | int | float] = {}
        _flatten("Session", session, session_values)
        prompts = prompts_by_session.get(str(session.get("experiment", "")), [])
        if not prompts:
            rows.append({"Record Type": "Session", **session_values})
            continue
        for prompt in prompts:
            row = {"Record Type": "Prompt", **session_values}
            _flatten("Prompt", prompt, row)
            rows.append(row)

    discovered_fields: list[str] = []
    for row in rows:
        for fieldname in row:
            if fieldname not in discovered_fields:
                discovered_fields.append(fieldname)

    prompt_fields = [field for field in discovered_fields if field.startswith("Prompt >")]
    session_fields = [
        field
        for field in discovered_fields
        if field.startswith("Session >") and field != "Session > Experiment"
    ]
    other_fields = [
        field
        for field in discovered_fields
        if field != "Record Type" and not field.startswith(("Prompt >", "Session >"))
    ]
    fieldnames = ["Record Type", "Session > Experiment"]
    fieldnames.extend(prompt_fields or ["Prompt > Prompt ID"])
    fieldnames.extend(session_fields)
    fieldnames.extend(other_fields)

    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")