from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import time
from typing import Sequence
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

try:
    from scripts.errors import CopilotValueError
except ModuleNotFoundError:
    from errors import CopilotValueError


OTLP_READY_TIMEOUT_SECONDS = 90


def post_json(url: str, payload: object, retry_seconds: float = 0) -> None:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    deadline = time.monotonic() + max(0, retry_seconds)
    last_error: TimeoutError | URLError | None = None
    while True:
        try:
            with urlopen(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise CopilotValueError(f"OTLP request failed ({response.status}) at {url}.")
            return
        except TimeoutError as error:
            if retry_seconds <= 0:
                return
            last_error = error
        except URLError as error:
            last_error = error
        if time.monotonic() >= deadline:
            raise CopilotValueError(f"OTLP request failed at {url}: {last_error}") from last_error
        time.sleep(0.25)


def query_victoria_metrics(query: str) -> dict[str, object]:
    parameters = urlencode({"nocache": "1", "query": query})
    url = f"http://127.0.0.1:8428/prometheus/api/v1/query?{parameters}"
    try:
        with urlopen(url, timeout=5) as response:
            return json.load(response)
    except (OSError, URLError, json.JSONDecodeError):
        return {}


def wait_for_metric(query: str, timeout_seconds: int = 90) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = query_victoria_metrics(query)
        data = response.get("data")
        if isinstance(data, dict) and data.get("result"):
            return response
        time.sleep(0.5)
    raise CopilotValueError(f"Timed out waiting for VictoriaMetrics query: {query}")


def otlp_attribute(key: str, value: str | int, value_type: str = "stringValue") -> dict[str, object]:
    return {"key": key, "value": {value_type: str(value)}}


def smoke_metric_payload(probe: str, forbidden: str) -> dict[str, object]:
    return {
        "resourceMetrics": [{
            "resource": {"attributes": [otlp_attribute("service.name", "copilot-value-bundle-smoke")]},
            "scopeMetrics": [{
                "scope": {"name": "copilot-value-bundle-smoke"},
                "metrics": [{
                    "name": "copilot.value.bundle.smoke",
                    "gauge": {"dataPoints": [{
                        "timeUnixNano": str(time.time_ns()),
                        "asDouble": 1,
                        "attributes": [
                            otlp_attribute("probe", probe),
                            otlp_attribute("file_path", forbidden),
                        ],
                    }]},
                }],
            }],
        }]
    }


def smoke_trace_payload(
    session_id: str,
    conversation_id: str,
    prompt: str,
    started_milliseconds: int,
    *,
    include_tool: bool,
) -> dict[str, object]:
    to_nanos = lambda milliseconds: str(milliseconds * 1_000_000)
    spans: list[dict[str, object]] = [{
        "traceId": secrets.token_hex(16),
        "spanId": secrets.token_hex(8),
        "name": "chat portable-smoke-model",
        "startTimeUnixNano": to_nanos(started_milliseconds),
        "endTimeUnixNano": to_nanos(started_milliseconds + 200),
        "attributes": [
            otlp_attribute("copilot_chat.user_request", prompt),
            otlp_attribute("gen_ai.conversation.id", conversation_id),
            otlp_attribute("gen_ai.request.model", "portable-smoke-model"),
            otlp_attribute("gen_ai.usage.input_tokens", 100, "intValue"),
            otlp_attribute("gen_ai.usage.cache_read.input_tokens", 0, "intValue"),
            otlp_attribute("gen_ai.usage.output_tokens", 50, "intValue"),
            otlp_attribute("gen_ai.usage.reasoning_tokens", 10, "intValue"),
            otlp_attribute("copilot_chat.copilot_usage_nano_aiu", 1_000_000_000, "intValue"),
        ],
    }]
    if include_tool:
        spans.append({
            "traceId": secrets.token_hex(16),
            "spanId": secrets.token_hex(8),
            "name": "execute_tool apply_patch",
            "startTimeUnixNano": to_nanos(started_milliseconds + 300),
            "endTimeUnixNano": to_nanos(started_milliseconds + 400),
            "status": {"code": "STATUS_CODE_OK"},
            "attributes": [
                otlp_attribute("gen_ai.operation.name", "execute_tool"),
                otlp_attribute("gen_ai.tool.name", "apply_patch"),
            ],
        })
    return {
        "resourceSpans": [{
            "resource": {"attributes": [
                otlp_attribute("service.name", "copilot-chat"),
                otlp_attribute("session.id", session_id),
            ]},
            "scopeSpans": [{"scope": {"name": "copilot-chat"}, "spans": spans}],
        }]
    }


def wait_for_app_session(
    experiment: str,
    expected_prompt_contents: Sequence[str] = ("Synthetic portable smoke prompt",),
    expected_ai_credits: float = 1,
    expected_input_tokens: int = 100,
    expected_output_tokens: int = 50,
    expected_started_milliseconds: int | None = None,
    timeout_seconds: int = 90,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    session_url = f"http://127.0.0.1:3000/api/sessions/{experiment}"
    prompts_url = f"{session_url}/prompts"
    last_validation_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(session_url, timeout=5) as response:
                session = json.load(response)
            with urlopen(prompts_url, timeout=5) as response:
                prompts = json.load(response)
            if session.get("experiment") == experiment and len(prompts) == len(expected_prompt_contents):
                if [prompt.get("content") for prompt in prompts] != list(expected_prompt_contents):
                    raise CopilotValueError("FastAPI prompt index returned unexpected content.")
                if (
                    abs(sum(float(prompt.get("aiCredits") or 0) for prompt in prompts) - expected_ai_credits) > 1e-9
                    or any(prompt.get("usageSource") != "otel_trace" for prompt in prompts)
                ):
                    raise CopilotValueError("FastAPI OTel prompt fallback returned unexpected usage evidence.")
                if (
                    abs(float(session.get("aiCredits") or 0) - expected_ai_credits) > 1e-9
                    or abs(float(session.get("aiCostUsd") or 0) - expected_ai_credits / 100) > 1e-9
                    or (session.get("tokens") or {}).get("input") != expected_input_tokens
                    or (session.get("tokens") or {}).get("output") != expected_output_tokens
                ):
                    raise CopilotValueError("FastAPI session response returned incomplete cumulative usage.")
                if expected_started_milliseconds is not None:
                    try:
                        started_at = datetime.fromisoformat(
                            str(session.get("startedAt") or "").replace("Z", "+00:00")
                        )
                        actual_started_milliseconds = round(started_at.timestamp() * 1000)
                    except ValueError as error:
                        raise CopilotValueError(
                            "FastAPI session response returned an invalid start time."
                        ) from error
                    elapsed_seconds = float((session.get("usage") or {}).get("elapsedSeconds") or 0)
                    duration_seconds = float(session.get("durationSeconds") or 0)
                    if actual_started_milliseconds != expected_started_milliseconds:
                        raise CopilotValueError(
                            "FastAPI session response did not expand to late-arriving earlier evidence."
                        )
                    if abs(elapsed_seconds - duration_seconds) > 1e-3:
                        raise CopilotValueError(
                            "FastAPI session duration did not reconcile with retained usage."
                        )
                if session.get("modelingStatus") != "available":
                    raise CopilotValueError("FastAPI session modeling remained unavailable.")
                if (
                    session.get("sourceEvidenceComplete") is not False
                    or session.get("retainedSourceCharacters") != 0
                    or (session.get("source") or {}).get("source") != "otel_only"
                ):
                    raise CopilotValueError("FastAPI session response returned unsafe central source evidence.")
                comparison = session.get("scenarioResult") or {}
                comparison_fields = (
                    "estimatedManualLaborCostUsd",
                    "estimatedAiAssistedLaborCostUsd",
                    "estimatedAiAssistedTotalCostUsd",
                    "estimatedGrossCostSavingsUsd",
                )
                if any(not isinstance(comparison.get(field), (int, float)) for field in comparison_fields):
                    raise CopilotValueError("FastAPI session response omitted cost comparison fields.")
                expected_total = comparison["estimatedAiAssistedLaborCostUsd"] + session["aiCostUsd"]
                if abs(comparison["estimatedAiAssistedTotalCostUsd"] - expected_total) > 1e-9:
                    raise CopilotValueError("FastAPI session response returned inconsistent assisted cost.")
                print(f"FastAPI session and prompt indexing passed: {experiment}")
                return
        except CopilotValueError as error:
            last_validation_error = str(error)
        except (OSError, URLError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    detail = f" Last validation error: {last_validation_error}" if last_validation_error else ""
    raise CopilotValueError(
        f"FastAPI did not index session and prompt evidence for '{experiment}'.{detail}"
    )


def wait_for_app_overview(experiment: str, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    overview_url = "http://127.0.0.1:3000/api/overview?scenario=base&days=30"
    while time.monotonic() < deadline:
        try:
            with urlopen(overview_url, timeout=5) as response:
                overview = json.load(response)
            sessions = overview.get("sessions") or []
            if (
                overview.get("modelingStatus") == "available"
                and any(
                    session.get("experiment") == experiment
                    and session.get("modelingStatus") == "available"
                    for session in sessions
                )
                and isinstance((overview.get("totals") or {}).get("estimatedManualMinutes"), (int, float))
            ):
                print(f"FastAPI portfolio modeling passed: {experiment}")
                return
        except (OSError, URLError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    raise CopilotValueError(f"FastAPI portfolio modeling remained unavailable for '{experiment}'.")


def run_telemetry_smoke(root: Path | None = None) -> str:
    collector_endpoint = "http://127.0.0.1:4318"
    smoke_id = uuid.uuid4().hex
    probe = f"bundle-{smoke_id}"
    forbidden = f"forbidden-{smoke_id}"
    post_json(
        f"{collector_endpoint}/v1/metrics",
        smoke_metric_payload(probe, forbidden),
        retry_seconds=OTLP_READY_TIMEOUT_SECONDS,
    )
    metric_response = wait_for_metric(f'{{probe="{probe}"}}', 30)
    serialized_metric = json.dumps(metric_response, separators=(",", ":"))
    if forbidden in serialized_metric or "file_path" in serialized_metric:
        raise CopilotValueError("Collector privacy processing did not remove the synthetic file path.")

    session_id = f"portable-smoke-{uuid.uuid4()}"
    conversation_id = f"conversation-{smoke_id}"
    now_milliseconds = int(time.time() * 1000)
    started_at = datetime.fromtimestamp((now_milliseconds - 500) / 1000, timezone.utc)
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()[:10]
    expected_experiment = f"session-{started_at:%Y%m%d-%H%M%S}-{session_hash}"
    first_trace = smoke_trace_payload(
        session_id,
        conversation_id,
        "Synthetic portable smoke prompt",
        now_milliseconds - 500,
        include_tool=True,
    )
    post_json(
        f"{collector_endpoint}/v1/traces",
        first_trace,
        retry_seconds=OTLP_READY_TIMEOUT_SECONDS,
    )
    wait_for_metric(
        f'last_over_time(copilot_value_experiment_info{{experiment="{expected_experiment}"}}[5m])'
    )
    wait_for_metric(
        "last_over_time("
        f'copilot_value_experiment_estimated_manual_labor_cost_usd{{experiment="{expected_experiment}",scenario="base"}}'
        "[5m])"
    )
    print(f"Automatic session measurement passed: {expected_experiment}")
    if root is not None:
        wait_for_app_session(expected_experiment)
        post_json(
            f"{collector_endpoint}/v1/traces",
            first_trace,
            retry_seconds=OTLP_READY_TIMEOUT_SECONDS,
        )
        wait_for_app_session(expected_experiment)
        post_json(
            f"{collector_endpoint}/v1/traces",
            smoke_trace_payload(
                session_id,
                conversation_id,
                "Synthetic portable smoke prompt from late-arriving earlier span",
                now_milliseconds - 1_500,
                include_tool=False,
            ),
            retry_seconds=OTLP_READY_TIMEOUT_SECONDS,
        )
        wait_for_app_session(
            expected_experiment,
            (
                "Synthetic portable smoke prompt from late-arriving earlier span",
                "Synthetic portable smoke prompt",
            ),
            expected_ai_credits=2,
            expected_input_tokens=200,
            expected_output_tokens=100,
            expected_started_milliseconds=now_milliseconds - 1_500,
        )
        wait_for_app_overview(expected_experiment)
    return expected_experiment
