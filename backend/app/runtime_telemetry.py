"""Copilot runtime OTel contract: which spans are accepted, how sessions are keyed, and how they are labeled."""
from __future__ import annotations

from typing import Any

RUNTIME_SERVICE_NAMES = frozenset({"github-copilot"})
RUNTIME_SCOPE_NAMES = frozenset({"github.copilot"})
GENAI_OPERATIONS = frozenset({"invoke_agent", "chat", "execute_tool"})

SESSION_KEY_ATTRIBUTE = "algalon.session.key"
SURFACE_ATTRIBUTE = "algalon.source.surface"
MODE_ATTRIBUTE = "algalon.session.mode"
SURFACE_MARKER_ATTRIBUTE = "algalon.surface"
VSCODE_AGENT_HOST_NAMESPACE = "vscode.agent-host"

SURFACES = ("cli", "vscode", "sdk", "app", "other")
MODES = ("interactive", "automated")


def is_runtime_resource(resource_attributes: dict[str, Any], scope_name: Any) -> bool:
    return (
        resource_attributes.get("service.name") in RUNTIME_SERVICE_NAMES
        or scope_name in RUNTIME_SCOPE_NAMES
    )


CONTAINER_TOOL_NAMES = frozenset({"task", "read_agent", "write_agent", "list_agents"})


def tool_name(span_attributes: dict[str, Any], name: Any) -> str | None:
    value = span_attributes.get("gen_ai.tool.name")
    if isinstance(value, str) and value:
        return value
    parts = name.split() if isinstance(name, str) else []
    return parts[1] if len(parts) > 1 and parts[0] == "execute_tool" else None


def span_operation(span_attributes: dict[str, Any], name: Any) -> str | None:
    operation = span_attributes.get("gen_ai.operation.name")
    if isinstance(operation, str) and operation:
        return operation if operation in GENAI_OPERATIONS else None
    span_name = name if isinstance(name, str) else ""
    if span_name == "invoke_agent" or span_name.startswith("invoke_agent "):
        return "invoke_agent"
    if span_name.startswith("chat "):
        return "chat"
    if span_name.startswith("execute_tool "):
        return "execute_tool"
    return None


def resource_surface(resource_attributes: dict[str, Any]) -> str:
    marker = resource_attributes.get(SURFACE_MARKER_ATTRIBUTE)
    if isinstance(marker, str) and marker in SURFACES:
        return marker
    namespace = resource_attributes.get("service.namespace")
    if namespace == VSCODE_AGENT_HOST_NAMESPACE:
        return "vscode"
    return "other" if isinstance(namespace, str) and namespace else "cli"


def resource_mode(resource_attributes: dict[str, Any]) -> str:
    return "automated" if resource_attributes.get(MODE_ATTRIBUTE) == "automated" else "interactive"


def stamped_resource(resource: Any, session_key: str, surface: str, mode: str) -> dict[str, Any]:
    stamped_keys = {SESSION_KEY_ATTRIBUTE, SURFACE_ATTRIBUTE, MODE_ATTRIBUTE}
    source = resource if isinstance(resource, dict) else {}
    attributes = [
        attribute for attribute in source.get("attributes") or []
        if isinstance(attribute, dict) and attribute.get("key") not in stamped_keys
    ]
    attributes.extend(
        {"key": key, "value": {"stringValue": value}}
        for key, value in (
            (SESSION_KEY_ATTRIBUTE, session_key),
            (SURFACE_ATTRIBUTE, surface),
            (MODE_ATTRIBUTE, mode),
        )
    )
    return {**source, "attributes": attributes}
