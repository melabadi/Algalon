from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Iterable

from .trace_archive import attributes, iter_json_lines


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


def read_copilot_turns(
    path: Path,
    start_milliseconds: int | None = None,
    end_milliseconds: int | None = None,
) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in iter_json_lines(path, skip_invalid=True):
        if not isinstance(event, dict):
            raise ValueError("Direct log event must be an object")
        event_type = str(event.get("type") or "")
        raw_attributes = event.get("attrs")
        if event_type in {"user_message", "llm_request", "tool_call"} and not isinstance(raw_attributes, dict):
            raise ValueError("Direct log event attributes must be an object")
        event_attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
        if event_type == "user_message":
            if current:
                turns.append(finalize_prompt_group(current))
            raw_content = str(event_attributes.get("content") or "")
            content = extract_prompt_content(raw_content)
            timestamp = number(event.get("ts"))
            current = {
                "_started_milliseconds": timestamp,
                "started_at": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(),
                "content": content,
                "captured_content_length": len(raw_content),
                "model_requests": 0,
                "tool_calls": 0,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "_nano_aiu": 0,
                "models": {},
                "usage_source": "copilot_turn_log",
            }
            continue
        if current is None:
            continue
        if event_type == "llm_request":
            model = str(event_attributes.get("model") or "unknown")
            current["model_requests"] = add_counter(current["model_requests"], 1)
            current["models"][model] = add_counter(current["models"].get(model, 0), 1)
            current["input_tokens"] = add_counter(
                current["input_tokens"], event_attributes.get("inputTokens")
            )
            current["cache_read_tokens"] = add_counter(
                current["cache_read_tokens"], event_attributes.get("cachedTokens")
            )
            current["output_tokens"] = add_counter(
                current["output_tokens"], event_attributes.get("outputTokens")
            )
            current["reasoning_tokens"] = add_counter(
                current["reasoning_tokens"], event_attributes.get("reasoningTokens")
            )
            current["_nano_aiu"] = add_counter(
                current["_nano_aiu"], event_attributes.get("copilotUsageNanoAiu")
            )
        elif event_type == "tool_call":
            current["tool_calls"] = add_counter(current["tool_calls"], 1)
    if current:
        turns.append(finalize_prompt_group(current))
    selected: list[dict[str, Any]] = []
    for turn in turns:
        started = turn.pop("_started_milliseconds")
        if not turn["content"]:
            continue
        if start_milliseconds is not None and started < start_milliseconds:
            continue
        if end_milliseconds is not None and started > end_milliseconds:
            continue
        selected.append(turn)
    return selected


def conversation_ids(trace_session: dict[str, Any]) -> list[str]:
    return sorted({
        str(value)
        for span in trace_session["spans"]
        for span_attributes in [attributes(span.get("attributes"))]
        for value in [
            span_attributes.get("gen_ai.conversation.id")
            or span_attributes.get("copilot_chat.chat_session_id")
        ]
        if value
    })


def group_prompts(
    trace_session: dict[str, Any],
    direct_turns: Iterable[dict[str, Any]],
    content_enabled: bool,
    *,
    include_unmatched_otel: bool = False,
) -> list[dict[str, Any]]:
    exact_groups = [
        {
            **turn,
            "ordinal": 0,
            "content": turn["content"] if content_enabled else "",
        }
        for turn in direct_turns
    ]
    if exact_groups and not include_unmatched_otel:
        exact_groups.sort(key=lambda group: group["started_at"])
        for ordinal, group in enumerate(exact_groups, start=1):
            group["ordinal"] = ordinal
        return exact_groups
    exact_conversations = {
        str(group.get("conversation_id") or "")
        for group in exact_groups
        if group.get("conversation_id")
    }
    exact_trace_ids = {
        str(span.get("traceId") or "")
        for span in trace_session["spans"]
        for span_attributes in [attributes(span.get("attributes"))]
        if (
            span_attributes.get("gen_ai.conversation.id")
            or span_attributes.get("copilot_chat.chat_session_id")
        ) in exact_conversations
        and span.get("traceId")
    }

    otel_groups_by_trace: dict[str, dict[str, Any]] = {}
    untraced_groups: list[dict[str, Any]] = []
    untraced_current: dict[str, Any] | None = None
    for span in sorted(trace_session["spans"], key=lambda item: item["_started"]):
        span_attributes = attributes(span.get("attributes"))
        span_conversation = str(
            span_attributes.get("gen_ai.conversation.id")
            or span_attributes.get("copilot_chat.chat_session_id")
            or ""
        )
        if (
            span_conversation in exact_conversations
            or str(span.get("traceId") or "") in exact_trace_ids
        ):
            continue
        name = str(span.get("name") or "")
        trace_id = str(span.get("traceId") or "")
        current = otel_groups_by_trace.get(trace_id) if trace_id else untraced_current
        raw_request = span_attributes.get("copilot_chat.user_request")
        starts_prompt = (
            name.startswith("chat ")
            and isinstance(raw_request, str)
            and bool(span_attributes.get("gen_ai.conversation.id"))
            and (
                current is None
                or not trace_id
            )
        )
        if starts_prompt:
            if not trace_id and untraced_current:
                untraced_groups.append(finalize_prompt_group(untraced_current))
            extracted_content = extract_prompt_content(raw_request)
            current = {
                "ordinal": 0,
                "trace_id": trace_id,
                "conversation_id": str(
                    span_attributes.get("gen_ai.conversation.id")
                    or span_attributes.get("copilot_chat.chat_session_id")
                    or ""
                ),
                "started_at": datetime.fromtimestamp(span["_started"] / 1000, timezone.utc).isoformat(),
                "match_content": extracted_content,
                "content": extracted_content if content_enabled else "",
                "captured_content_length": len(raw_request),
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
            if trace_id:
                otel_groups_by_trace[trace_id] = current
            else:
                untraced_current = current
        if current is None:
            continue
        if name.startswith("chat "):
            model = str(span_attributes.get("gen_ai.request.model") or name[5:] or "unknown")
            current["model_requests"] = add_counter(current["model_requests"], 1)
            current["models"][model] = add_counter(current["models"].get(model, 0), 1)
            current["input_tokens"] = add_counter(
                current["input_tokens"], span_attributes.get("gen_ai.usage.input_tokens")
            )
            current["cache_read_tokens"] = add_counter(
                current["cache_read_tokens"],
                span_attributes.get("gen_ai.usage.cache_read.input_tokens"),
            )
            current["output_tokens"] = add_counter(
                current["output_tokens"], span_attributes.get("gen_ai.usage.output_tokens")
            )
            current["reasoning_tokens"] = add_counter(
                current["reasoning_tokens"], span_attributes.get("gen_ai.usage.reasoning_tokens")
            )
            current["_nano_aiu"] = add_counter(
                current["_nano_aiu"],
                span_attributes.get("copilot_chat.copilot_usage_nano_aiu"),
            )
        elif name.startswith("execute_tool "):
            current["tool_calls"] = add_counter(current["tool_calls"], 1)
    otel_groups = [finalize_prompt_group(group) for group in otel_groups_by_trace.values()]
    otel_groups.extend(untraced_groups)
    if untraced_current:
        otel_groups.append(finalize_prompt_group(untraced_current))

    groups = exact_groups + [
        group for group in otel_groups
        if group["conversation_id"] not in exact_conversations
    ]
    groups.sort(key=lambda group: group["started_at"])
    for ordinal, group in enumerate(groups, start=1):
        group["ordinal"] = ordinal
    return groups
