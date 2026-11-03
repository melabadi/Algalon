"""Builders for Copilot runtime OTLP/JSON payloads shared by the backend tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

RUNTIME_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "test" / "fixtures" / "copilot-runtime-otlp.json"
)


def attribute(key: str, value: object, value_type: str = "stringValue") -> dict[str, Any]:
    raw = value if value_type in {"doubleValue", "boolValue"} else str(value)
    return {"key": key, "value": {value_type: raw}}


def runtime_span(
    operation: str,
    *,
    trace_id: str,
    span_id: str,
    start_ns: int,
    end_ns: int | None = None,
    conversation: str | None = None,
    parent_span_id: str | None = None,
    name: str | None = None,
    attributes: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    span_attributes = [attribute("gen_ai.operation.name", operation)]
    if conversation is not None:
        span_attributes.append(attribute("gen_ai.conversation.id", conversation))
    span_attributes.extend(attributes)
    span: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name or operation,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns if end_ns is not None else start_ns + 1_000_000),
        "attributes": span_attributes,
    }
    if parent_span_id:
        span["parentSpanId"] = parent_span_id
    return span


def chat_span(
    *,
    model: str = "model-a",
    input_tokens: int | str | None = None,
    cache_read_tokens: int | str | None = None,
    output_tokens: int | str | None = None,
    reasoning_tokens: int | str | None = None,
    nano_aiu: int | float | str | None = None,
    attributes: Iterable[dict[str, Any]] = (),
    **span_options: Any,
) -> dict[str, Any]:
    usage = [attribute("gen_ai.request.model", model)]
    for key, value in (
        ("gen_ai.usage.input_tokens", input_tokens),
        ("gen_ai.usage.cache_read.input_tokens", cache_read_tokens),
        ("gen_ai.usage.output_tokens", output_tokens),
        ("gen_ai.usage.reasoning.output_tokens", reasoning_tokens),
        ("github.copilot.nano_aiu", nano_aiu),
    ):
        if value is not None:
            usage.append(attribute(key, value, "doubleValue" if isinstance(value, float) else "intValue"))
    return runtime_span(
        "chat", name=f"chat {model}", attributes=[*usage, *attributes], **span_options,
    )


def tool_span(
    tool: str, *, attributes: Iterable[dict[str, Any]] = (), **span_options: Any,
) -> dict[str, Any]:
    return runtime_span(
        "execute_tool",
        name=f"execute_tool {tool}",
        attributes=[attribute("gen_ai.tool.name", tool), *attributes],
        **span_options,
    )


def agent_span(**span_options: Any) -> dict[str, Any]:
    return runtime_span("invoke_agent", **span_options)


def runtime_payload(
    spans: Iterable[dict[str, Any]],
    *,
    resource_attributes: Iterable[dict[str, Any]] = (),
    service_name: str | None = "github-copilot",
    scope: str = "github.copilot",
) -> dict[str, Any]:
    resource = [attribute("service.name", service_name)] if service_name else []
    resource.extend(resource_attributes)
    return {"resourceSpans": [{
        "resource": {"attributes": resource},
        "scopeSpans": [{"scope": {"name": scope}, "spans": list(spans)}],
    }]}


def load_runtime_fixture() -> dict[str, Any]:
    return json.loads(RUNTIME_FIXTURE_PATH.read_text(encoding="utf-8"))


def record_span(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (resource, scope span, span) for a single-span inbox record."""
    resource_spans = record["resourceSpans"]
    assert len(resource_spans) == 1
    scope_spans = resource_spans[0]["scopeSpans"]
    assert len(scope_spans) == 1 and len(scope_spans[0]["spans"]) == 1
    return resource_spans[0]["resource"], scope_spans[0], scope_spans[0]["spans"][0]
