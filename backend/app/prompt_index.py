from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
import re
from typing import Any

from .runtime_telemetry import CONTAINER_TOOL_NAMES, span_operation, tool_name
from .trace_archive import attributes


MAX_COUNTER = 2**63 - 1
MAX_EXACT_FLOAT_INTEGER = 2**53 - 1


def number(value: Any) -> float:
    try:
        candidate = float(value or 0)
        return candidate if isfinite(candidate) else 0
    except (OverflowError, TypeError, ValueError):
        return 0


def counter(value: Any) -> int | None:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and re.fullmatch(r"\+?\d+", value.strip()):
        try:
            candidate = int(value)
        except ValueError:
            return None
    elif (
        isinstance(value, float)
        and value.is_integer()
        and 0 <= value <= MAX_EXACT_FLOAT_INTEGER
    ):
        candidate = int(value)
    else:
        return None
    return candidate if 0 <= candidate <= MAX_COUNTER else None


def add_counter(total: Any, value: Any) -> int | None:
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return None
    parsed = counter(value)
    if parsed is None:
        return None
    result = total + parsed
    return result if result <= MAX_COUNTER else None


def finalize_prompt_group(group: dict[str, Any]) -> dict[str, Any]:
    nano_aiu = group.pop("_nano_aiu", None)
    if isinstance(nano_aiu, int) and not isinstance(nano_aiu, bool) and 0 <= nano_aiu <= MAX_COUNTER:
        ai_credits = nano_aiu / 1_000_000_000
        group["ai_credits"] = ai_credits
        group["ai_cost_usd"] = ai_credits / 100
    else:
        group["ai_credits"] = None
        group["ai_cost_usd"] = None
    return group


def prompt_group_counters_valid(group: Any) -> bool:
    fields = (
        "ordinal", "captured_content_length", "model_requests", "tool_calls",
        "input_tokens", "cache_read_tokens", "output_tokens", "reasoning_tokens",
    )
    if not isinstance(group, dict) or any(
        not isinstance(group.get(field), int)
        or isinstance(group.get(field), bool)
        or not 0 <= group[field] <= MAX_COUNTER
        for field in fields
    ):
        return False
    models = group.get("models")
    ai_credits = group.get("ai_credits")
    ai_cost = group.get("ai_cost_usd")
    return (
        isinstance(ai_credits, (int, float)) and not isinstance(ai_credits, bool)
        and isfinite(float(ai_credits)) and ai_credits >= 0
        and isinstance(ai_cost, (int, float)) and not isinstance(ai_cost, bool)
        and isfinite(float(ai_cost)) and ai_cost == ai_credits / 100
        and isinstance(models, dict) and all(
        isinstance(model, str) and model
        and isinstance(requests, int) and not isinstance(requests, bool)
        and 0 <= requests <= MAX_COUNTER
        for model, requests in models.items()
        )
    )


def extract_prompt_content(raw_value: str) -> str:
    patterns = (
        r"<userRequest>(.*?)</userRequest>",
        r"<user_request>(.*?)</user_request>",
        r"<user>(.*?)</user>",
    )

    def tagged_content(value: str) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, value, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).replace("\\r\\n", "\n").replace("\\n", "\n").strip()
        return None

    direct = tagged_content(raw_value)
    if direct:
        return direct
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        parsed = None

    candidates: list[str] = []

    def visit(value: Any, role: str | None = None) -> None:
        if isinstance(value, dict):
            current_role = str(value.get("role", role or "")).lower() or role
            if value.get("type") == "input_text" and isinstance(value.get("text"), str):
                candidates.append(value["text"])
            for key, child in value.items():
                if key in {"content", "text"} and isinstance(child, str) and current_role in {"user", "human"}:
                    candidates.append(child)
                else:
                    visit(child, current_role)
        elif isinstance(value, list):
            for child in value:
                visit(child, role)

    if parsed is not None:
        if isinstance(parsed, str):
            raw_value = parsed
        visit(parsed)
        if candidates:
            candidate = candidates[-1].strip()
            return tagged_content(candidate) or candidate

    tagged = tagged_content(raw_value)
    if tagged:
        return tagged
    return raw_value.replace("\\r\\n", "\n").replace("\\n", "\n").strip()


def nano_counter(value: Any) -> int | None:
    """The runtime reports nano-AIU as an integer or a double; keep it as an exact integer count."""
    parsed = counter(value)
    if parsed is not None:
        return parsed
    candidate = number(value)
    return int(round(candidate)) if candidate >= 0 else None


def group_prompts(trace_session: dict[str, Any]) -> list[dict[str, Any]]:
    """Group runtime spans into one text-less prompt row per user message; each message is its own trace."""
    groups: dict[str, dict[str, Any]] = {}
    for span in sorted(trace_session["spans"], key=lambda item: item["_started"]):
        span_attributes = attributes(span.get("attributes"))
        name = span.get("name")
        operation = span_operation(span_attributes, name)
        if operation is None:
            continue
        key = str(span.get("traceId") or "") or f"span:{span.get('spanId') or span['_started']}"
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "ordinal": 0,
                "started_at": datetime.fromtimestamp(span["_started"] / 1000, timezone.utc).isoformat(),
                "content": "",
                "captured_content_length": 0,
                "model_requests": 0,
                "tool_calls": 0,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "_nano_aiu": 0,
                "models": {},
                "usage_source": "otel_trace",
            }
        if operation == "chat":
            model = str(
                span_attributes.get("gen_ai.request.model")
                or span_attributes.get("gen_ai.response.model")
                or (name[len("chat "):].strip() if isinstance(name, str) else "")
                or "unknown"
            )
            group["model_requests"] = add_counter(group["model_requests"], 1)
            group["models"][model] = add_counter(group["models"].get(model, 0), 1)
            group["input_tokens"] = add_counter(
                group["input_tokens"], span_attributes.get("gen_ai.usage.input_tokens")
            )
            group["cache_read_tokens"] = add_counter(
                group["cache_read_tokens"], span_attributes.get("gen_ai.usage.cache_read.input_tokens")
            )
            group["output_tokens"] = add_counter(
                group["output_tokens"], span_attributes.get("gen_ai.usage.output_tokens")
            )
            group["reasoning_tokens"] = add_counter(
                group["reasoning_tokens"],
                span_attributes.get("gen_ai.usage.reasoning.output_tokens")
                or span_attributes.get("gen_ai.usage.reasoning_tokens"),
            )
            group["_nano_aiu"] = add_counter(
                group["_nano_aiu"], nano_counter(span_attributes.get("github.copilot.nano_aiu"))
            )
        elif operation == "execute_tool" and tool_name(span_attributes, name) not in CONTAINER_TOOL_NAMES:
            group["tool_calls"] = add_counter(group["tool_calls"], 1)
    ordered = sorted(groups.values(), key=lambda group: group["started_at"])
    for ordinal, group in enumerate(ordered, start=1):
        group["ordinal"] = ordinal
        finalize_prompt_group(group)
    return ordered
