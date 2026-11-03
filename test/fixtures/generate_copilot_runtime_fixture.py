"""Generate the sanitized Copilot runtime OTLP contract fixture used by Python and TypeScript tests."""
from __future__ import annotations

import json
from pathlib import Path

BASE_SECONDS = 1_793_296_800  # 2026-10-29T18:00:00Z


def nanos(offset_seconds: float) -> str:
    return str(int(round((BASE_SECONDS + offset_seconds) * 1_000_000_000)))


def attribute(key: str, value: object) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def span(trace: str, span_id: str, parent: str | None, name: str, start: float, end: float,
         kind: int, attributes: dict, events: list | None = None) -> dict:
    payload = {
        "traceId": trace,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": nanos(start),
        "endTimeUnixNano": nanos(end),
        "attributes": [attribute(key, value) for key, value in attributes.items()],
        "status": {},
    }
    if parent:
        payload["parentSpanId"] = parent
    if events:
        payload["events"] = events
    return payload


def chat(trace, span_id, parent, conversation, model, start, end, *, nano_aiu, input_tokens, cache_read,
         output_tokens, reasoning=None, initiator="agent", turn_id=None, interaction=None, cache_write=0):
    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "github",
        "gen_ai.conversation.id": conversation,
        "gen_ai.request.model": model,
        "gen_ai.response.model": model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.cache_read.input_tokens": cache_read,
        "gen_ai.usage.cache_write.input_tokens": cache_write,
        "gen_ai.usage.output_tokens": output_tokens,
        "github.copilot.nano_aiu": nano_aiu,
        "github.copilot.cost": 1.0,
        "github.copilot.initiator": initiator,
    }
    if reasoning is not None:
        attributes["gen_ai.usage.reasoning.output_tokens"] = str(reasoning)
    if turn_id is not None:
        attributes["github.copilot.turn_id"] = str(turn_id)
    if interaction is not None:
        attributes["github.copilot.interaction_id"] = interaction
    return span(trace, span_id, parent, f"chat {model}", start, end, 3, attributes)


def tool(trace, span_id, parent, conversation, tool_name, start, end, *, span_name=None, extra=None):
    attributes = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.provider.name": "github",
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.type": "function",
    }
    if conversation is not None:
        attributes["gen_ai.conversation.id"] = conversation
    attributes.update(extra or {})
    return span(trace, span_id, parent, span_name or f"execute_tool {tool_name}", start, end, 1, attributes)


def agent(trace, span_id, parent, conversation, start, end, *, nano_aiu, input_tokens, output_tokens,
          agent_name=None, interaction=None):
    attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": "github",
        "gen_ai.conversation.id": conversation,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "github.copilot.nano_aiu": nano_aiu,
        "github.copilot.cost": 1.0,
        "github.copilot.agent.type": "builtin",
    }
    if agent_name:
        attributes["gen_ai.agent.name"] = agent_name
    events = None
    if interaction:
        events = [{
            "timeUnixNano": nanos(start),
            "name": "github.copilot.user.message",
            "attributes": [
                attribute("github.copilot.user.message.interaction_id", interaction),
                attribute("github.copilot.user.message.source", "user"),
                attribute("github.copilot.user.message.turn_id", "0"),
            ],
        }]
    name = f"invoke_agent {agent_name}" if agent_name else "invoke_agent"
    return span(trace, span_id, parent, name, start, end, 1, attributes, events)


def resource_spans(resource: dict, scope: str, spans: list[dict]) -> dict:
    return {
        "resource": {"attributes": [attribute(key, value) for key, value in resource.items()]},
        "scopeSpans": [{"scope": {"name": scope, "version": "1.0.105"}, "spans": spans}],
    }


def main() -> None:
    cli = "cli-session-0001"
    t1, t2 = "11111111111111111111111111111111", "22222222222222222222222222222222"
    root1, task, sub = "1000000000000001", "1000000000000020", "1000000000000021"
    cli_message_one = [
        agent(t1, root1, None, cli, 0.0, 51.0, nano_aiu=6828250000.0, input_tokens=139558,
              output_tokens=519, interaction="interaction-cli-1"),
        chat(t1, "1000000000000002", root1, cli, "gpt-6.1-sol", 0.5, 4.3, nano_aiu=4897600000,
             input_tokens=19515, cache_read=0, cache_write=18000, output_tokens=19, initiator="user",
             turn_id=0, interaction="interaction-cli-1"),
        tool(t1, "1000000000000003", root1, cli, "glob", 4.4, 5.1),
        chat(t1, "1000000000000004", root1, cli, "gpt-6.1-sol", 5.2, 9.5, nano_aiu=241970000.0,
             input_tokens=19512, cache_read=18000, output_tokens=37, reasoning=12, turn_id=1,
             interaction="interaction-cli-1"),
        tool(t1, "1000000000000005", root1, cli, "rg", 9.6, 9.8),
        chat(t1, "1000000000000006", root1, cli, "gpt-6.1-sol", 9.9, 13.7, nano_aiu=394090000,
             input_tokens=19549, cache_read=19000, output_tokens=172, turn_id=2,
             interaction="interaction-cli-1"),
        tool(t1, "1000000000000007", root1, cli, "view", 13.8, 14.3),
        tool(t1, "1000000000000008", root1, cli, "powershell", 14.4, 16.0,
             span_name="execute_tool powershell pwsh.exe",
             extra={"process.executable.name": "pwsh.exe", "process.exit.code": 1}),
        chat(t1, "1000000000000009", root1, cli, "gpt-6.1-sol", 16.1, 20.9, nano_aiu=363380000,
             input_tokens=19653, cache_read=19500, output_tokens=89, turn_id=3,
             interaction="interaction-cli-1"),
        tool(t1, "1000000000000010", root1, cli, "apply_patch", 21.0, 21.2),
        chat(t1, "1000000000000011", root1, cli, "gpt-6.1-sol", 21.3, 25.6, nano_aiu=266970000,
             input_tokens=19962, cache_read=19600, output_tokens=28, turn_id=4,
             interaction="interaction-cli-1"),
        tool(t1, "1000000000000012", root1, cli, "powershell", 25.7, 27.9,
             span_name="execute_tool powershell pwsh.exe",
             extra={"process.executable.name": "pwsh.exe", "process.exit.code": 0}),
        chat(t1, "1000000000000013", root1, cli, "gpt-6.1-sol", 28.0, 32.2, nano_aiu=367270000,
             input_tokens=20117, cache_read=19900, output_tokens=123, turn_id=5,
             interaction="interaction-cli-1"),
        tool(t1, task, root1, cli, "task", 32.3, 46.6),
        agent(t1, sub, task, cli, 32.4, 46.4, nano_aiu=218716000.0, input_tokens=13163,
              output_tokens=53, agent_name="explore"),
        chat(t1, "1000000000000022", sub, cli, "gpt-5.6-luna", 32.5, 35.4, nano_aiu=190795000,
             input_tokens=6586, cache_read=0, output_tokens=12, initiator="sub-agent"),
        tool(t1, "1000000000000023", sub, cli, "view", 35.5, 41.2, extra={"gen_ai.agent.name": "explore"}),
        chat(t1, "1000000000000024", sub, cli, "gpt-5.6-luna", 41.3, 42.8, nano_aiu=27921000,
             input_tokens=6583, cache_read=6400, output_tokens=41, initiator="sub-agent"),
        chat(t1, "1000000000000025", root1, cli, "gpt-6.1-sol", 46.7, 50.7, nano_aiu=296970000,
             input_tokens=20287, cache_read=20100, output_tokens=51, turn_id=6,
             interaction="interaction-cli-1"),
    ]
    root2 = "2000000000000001"
    cli_resumed = [
        chat(t2, "2000000000000002", root2, cli, "gpt-5.6-luna", 600.5, 604.5, nano_aiu=516845000,
             input_tokens=20600, cache_read=0, output_tokens=28, initiator="user", turn_id=0,
             interaction="interaction-cli-2"),
        agent(t2, root2, None, cli, 600.0, 605.0, nano_aiu=516845000, input_tokens=20600,
              output_tokens=28, interaction="interaction-cli-2"),
    ]
    vscode = "vscode-session-0001"
    t3, root3 = "33333333333333333333333333333333", "3000000000000001"
    vscode_spans = [
        chat(t3, "3000000000000002", root3, vscode, "claude-sonnet-5.5", 100.2, 104.0, nano_aiu=400000000,
             input_tokens=12000, cache_read=8000, output_tokens=300, reasoning=40, initiator="user",
             turn_id=0, interaction="interaction-vscode-1"),
        tool(t3, "3000000000000003", root3, None, "edit", 104.1, 104.6),
        tool(t3, "3000000000000004", root3, vscode, "view", 104.7, 104.9),
        chat(t3, "3000000000000005", root3, vscode, "claude-sonnet-5.5", 105.0, 108.0, nano_aiu=200000000,
             input_tokens=12500, cache_read=11000, output_tokens=80, turn_id=1,
             interaction="interaction-vscode-1"),
        agent(t3, root3, None, vscode, 100.0, 108.2, nano_aiu=600000000, input_tokens=24500,
              output_tokens=380, interaction="interaction-vscode-1"),
    ]
    sdk = "sdk-session-0001"
    t4, root4 = "44444444444444444444444444444444", "4000000000000001"
    sdk_spans = [
        span(t4, "4000000000000009", None, "session.timing.model_resolution", 199.0, 199.4, 1, {}),
        chat(t4, "4000000000000002", root4, sdk, "gpt-5.6-luna", 200.5, 203.0, nano_aiu=184840000,
             input_tokens=10400, cache_read=0, output_tokens=40, reasoning=23, initiator="user", turn_id=0,
             interaction="interaction-sdk-1"),
        tool(t4, "4000000000000003", root4, sdk, "view", 203.1, 203.4),
        chat(t4, "4000000000000004", root4, sdk, "gpt-5.6-luna", 203.5, 205.0, nano_aiu=32300000,
             input_tokens=10700, cache_read=10300, output_tokens=6, turn_id=1,
             interaction="interaction-sdk-1"),
        agent(t4, root4, None, sdk, 200.0, 205.2, nano_aiu=217140000, input_tokens=21100,
              output_tokens=46, interaction="interaction-sdk-1"),
    ]
    legacy = [
        span("55555555555555555555555555555555", "5000000000000001", None, "invoke_agent copilotcli", 300.0, 310.0, 1, {
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "copilotcli",
            "gen_ai.conversation.id": "legacy-chat-1",
        }),
        span("55555555555555555555555555555555", "5000000000000002", "5000000000000001", "chat gpt-4o", 300.5, 303.0, 3, {
            "gen_ai.operation.name": "chat", "gen_ai.conversation.id": "legacy-chat-1",
            "gen_ai.request.model": "gpt-4o", "copilot_chat.copilot_usage_nano_aiu": 900000000,
        }),
    ]
    foreign = [
        span("66666666666666666666666666666666", "6000000000000001", None, "chat other-model", 400.0, 401.0, 3, {
            "gen_ai.operation.name": "chat", "gen_ai.conversation.id": "foreign-1",
            "github.copilot.nano_aiu": 123000000,
        }),
    ]
    cli_resource = {"service.name": "github-copilot", "service.version": "1.0.105",
                    "service.instance.id": "copilot-otel-1000-0"}
    payloads = [
        {"resourceSpans": [resource_spans(cli_resource, "github.copilot", cli_message_one[1:8])]},
        {"resourceSpans": [resource_spans(cli_resource, "github.copilot", cli_message_one[8:])]},
        {"resourceSpans": [resource_spans(cli_resource, "github.copilot", cli_message_one[:1])]},
        {"resourceSpans": [resource_spans({**cli_resource, "service.instance.id": "copilot-otel-1001-0"},
                                          "github.copilot", cli_resumed)]},
        {"resourceSpans": [resource_spans({
            "service.name": "github-copilot", "service.namespace": "vscode.agent-host",
            "service.version": "1.0.95-0", "service.instance.id": "copilot-otel-2000-1",
        }, "github.copilot", vscode_spans)]},
        {"resourceSpans": [resource_spans({
            "service.name": "github-copilot", "service.version": "1.0.105",
            "service.instance.id": "copilot-otel-3000-0", "algalon.session.mode": "automated",
            "algalon.surface": "sdk",
        }, "spike.sdk.scope", sdk_spans)]},
        {"resourceSpans": [
            resource_spans({"service.name": "copilot-chat", "session.id": "vscode-window-1"}, "copilot-chat", legacy),
            resource_spans({"service.name": "other-otel-app"}, "other.scope", foreign),
        ]},
    ]

    def sums(spans: list[dict]) -> dict:
        totals = {"chatSpans": 0, "toolSpans": 0, "agentSpans": 0, "nanoAiu": 0, "inputTokens": 0,
                  "cacheReadTokens": 0, "outputTokens": 0, "reasoningTokens": 0}
        for item in spans:
            values = {entry["key"]: next(iter(entry["value"].values())) for entry in item["attributes"]}
            operation = values.get("gen_ai.operation.name")
            if operation == "chat":
                totals["chatSpans"] += 1
                totals["nanoAiu"] += int(float(values["github.copilot.nano_aiu"]))
                totals["inputTokens"] += int(values["gen_ai.usage.input_tokens"])
                totals["cacheReadTokens"] += int(values["gen_ai.usage.cache_read.input_tokens"])
                totals["outputTokens"] += int(values["gen_ai.usage.output_tokens"])
                totals["reasoningTokens"] += int(values.get("gen_ai.usage.reasoning.output_tokens", 0))
            elif operation == "execute_tool":
                totals["toolSpans"] += 1
            elif operation == "invoke_agent":
                totals["agentSpans"] += 1
        return totals

    expected = {
        "sessions": {
            cli: {**sums(cli_message_one + cli_resumed), "surface": "cli", "mode": "interactive", "prompts": 2,
                  "startedAt": "2026-10-29T18:00:00.000Z", "endedAt": "2026-10-29T18:10:05.000Z"},
            vscode: {**sums(vscode_spans), "surface": "vscode", "mode": "interactive", "prompts": 1,
                     "startedAt": "2026-10-29T18:01:40.000Z", "endedAt": "2026-10-29T18:01:48.200Z"},
            sdk: {**sums(sdk_spans), "surface": "sdk", "mode": "automated", "prompts": 1,
                  "startedAt": "2026-10-29T18:03:20.000Z", "endedAt": "2026-10-29T18:03:25.200Z"},
        },
        "acceptedSpans": len(cli_message_one) + len(cli_resumed) + len(vscode_spans) + len(sdk_spans) - 1,
        "droppedSpans": len(legacy) + len(foreign) + 1,
    }
    fixture = {
        "description": "Sanitized Copilot runtime OTLP/JSON payloads modeled on CLI 1.0.105, VS Code Agent Host "
                       "1.0.95-0 and SDK 1.0.28 captures. Collector-scrubbed: no enduser.pseudo.id or content.",
        "payloads": payloads,
        "expected": expected,
    }
    output = Path(__file__).resolve().parent / "copilot-runtime-otlp.json"
    output.write_text(json.dumps(fixture, indent=1) + "\n", encoding="utf-8")
    print(output, json.dumps(expected, indent=1))


if __name__ == "__main__":
    main()
