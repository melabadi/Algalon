from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from time import monotonic, time
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend.app.session_identity import public_session_id
from backend.app.store import ValueStore


ROOT = Path(__file__).resolve().parents[1]


class MetricsSink(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_arguments) -> None:
        pass


def request_json(url: str, payload: dict | None = None):
    request = Request(
        url, data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def trace_payload(session: int, ordinal: int, started: int, *, prompt: bool = False) -> dict:
    values = {
        "gen_ai.conversation.id": f"synthetic-conversation-{session}",
        "gen_ai.request.model": "synthetic-model",
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.cache_read.input_tokens": 80,
        "gen_ai.usage.output_tokens": 10,
        "copilot_chat.copilot_usage_nano_aiu": 1_000_000,
    }
    if prompt or ordinal == 0:
        values["copilot_chat.user_request"] = f"Synthetic prompt {ordinal}"
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "copilot-chat"}},
            {"key": "session.id", "value": {"stringValue": f"synthetic-session-{session}"}},
        ]},
        "scopeSpans": [{"spans": [{
            "traceId": f"{session + 1:016x}{ordinal + 1:016x}" if prompt else f"{session + 1:032x}",
            "spanId": f"{session + 1:08x}{ordinal + 1:08x}",
            "name": "chat synthetic-model",
            "startTimeUnixNano": str((started + ordinal * 100) * 1_000_000),
            "endTimeUnixNano": str((started + ordinal * 100 + 50) * 1_000_000),
            "attributes": [
                {"key": key, "value": {"intValue" if isinstance(value, int) else "stringValue": str(value)}}
                for key, value in values.items()
            ],
        }]}],
    }]}


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


def run_reliability(history_spans: int, sessions: int, updates: int, *, interval: float = 1, timeout: float = 1_800) -> dict:
    if history_spans < sessions or sessions < 1 or updates < 1:
        raise ValueError("Use at least one span per session and one update")
    with tempfile.TemporaryDirectory(prefix="algalon-indexing-") as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        config = json.loads((ROOT / "config/value-model.example.json").read_text(encoding="utf-8"))
        config["benchmark"]["acknowledgedAssumptions"] = True
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        output = root / "sessions"
        store = ValueStore(root / "value.db", output, root / "traces.json", config_path)
        started = int(time() * 1_000) - 3_600_000
        counts: dict[int, int] = defaultdict(int)
        seed_started = monotonic()
        for first in range(0, history_spans, 1_000):
            resources = []
            for offset in range(first, min(first + 1_000, history_spans)):
                session = offset % sessions
                resources.extend(trace_payload(session, counts[session], started)["resourceSpans"])
                counts[session] += 1
            store.ingest_otlp_traces({"resourceSpans": resources})
        seed_seconds = monotonic() - seed_started
        sink = ThreadingHTTPServer(("127.0.0.1", 0), MetricsSink)
        cleanup.callback(sink.server_close)
        cleanup.callback(sink.shutdown)
        threading.Thread(target=sink.serve_forever, daemon=True).start()
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            api_port = available.getsockname()[1]
        api = f"http://127.0.0.1:{api_port}"
        sink_url = f"http://127.0.0.1:{sink.server_port}"
        environment = {
            **os.environ,
            "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
            "COPILOT_VALUE_DB": str(root / "value.db"),
            "COPILOT_VALUE_CONFIG": str(config_path),
            "COPILOT_VALUE_SESSION_DIR": str(output),
            "COPILOT_VALUE_OUTPUT_DIR": str(output),
            "COPILOT_VALUE_TRACE_ARCHIVE": str(root / "traces.json"),
            "COPILOT_VALUE_CHAT_LOG_ROOT": str(root / "empty-logs"),
            "COPILOT_VALUE_STATIC_DIR": str(root / "static"),
            "COPILOT_VALUE_OTEL_INBOX_URL": api + "/api/internal/otel/records",
            "COPILOT_VALUE_VICTORIA_URL": sink_url,
            "COPILOT_VALUE_OTEL_URL": sink_url,
            "COPILOT_VALUE_POLL_SECONDS": "1",
        }
        logs = cleanup.enter_context((root / "processes.log").open("w+", encoding="utf-8"))

        def start(command: list[str]) -> subprocess.Popen:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=logs, stderr=logs)
            cleanup.callback(stop_process, process)
            return process

        api_command = [sys.executable, "-B", "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(api_port)]
        worker_command = ["node", str(ROOT / "dist/src/continuous-cli.js")]
        api_process = start(api_command)
        worker = start(worker_command)
        pause = threading.Event()

        def await_condition(condition, seconds: float = timeout) -> None:
            deadline = monotonic() + seconds
            while monotonic() < deadline:
                if api_process.poll() is not None or worker.poll() is not None:
                    logs.flush()
                    logs.seek(0)
                    raise AssertionError("An isolated reliability-test process exited. " + logs.read()[-2_000:])
                try:
                    if condition():
                        return
                except (URLError, TimeoutError, ConnectionError):
                    pass
                pause.wait(0.2)
            logs.flush()
            logs.seek(0)
            raise AssertionError("Indexing did not converge before the deadline. " + logs.read()[-2_000:])

        def totals() -> dict[str, int]:
            return {
                row["experiment"]: row["tokens"]["output"]
                for row in request_json(api + "/api/overview?days=30")["sessions"]
            }

        def experiment(session: int) -> str:
            return public_session_id(f"synthetic-session-{session}", started)

        warm_started = monotonic()
        await_condition(lambda: sum(totals().values()) == history_spans * 10)
        await_condition(lambda: request_json(api + "/api/indexing")["state"] == "current")
        warm_seconds = monotonic() - warm_started
        receipts: list[tuple[int, int, float]] = []
        producer_failures: list[Exception] = []

        def produce() -> None:
            try:
                for ordinal in range(updates):
                    session = ordinal % min(sessions, 5)
                    payload = trace_payload(session, counts[session], started, prompt=True)
                    accepted_at = monotonic()
                    request_json(api + "/api/internal/otel/v1/traces", payload)
                    counts[session] += 1
                    receipts.append((session, counts[session] * 10, accepted_at))
                    pause.wait(interval)
            except Exception as failure:
                producer_failures.append(failure)

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        latencies: dict[int, float] = {}

        def updates_visible() -> bool:
            if producer_failures:
                raise producer_failures[0]
            visible = totals()
            for ordinal, (session, expected, accepted_at) in enumerate(receipts):
                if ordinal not in latencies and visible.get(experiment(session), 0) >= expected:
                    latencies[ordinal] = monotonic() - accepted_at
            return len(latencies) == updates

        await_condition(updates_visible, max(90, updates * interval + 60))
        producer.join(timeout=5)
        request_json(api + "/api/internal/otel/v1/traces", trace_payload(0, counts[0], started, prompt=True))
        counts[0] += 1
        stop_process(worker)
        stop_process(api_process)
        api_process = start(api_command)
        worker = start(worker_command)
        recovery_started = monotonic()
        await_condition(lambda: sum(totals().values()) == (history_spans + updates + 1) * 10, 120)
        await_condition(lambda: request_json(api + "/api/indexing")["state"] == "current", 120)
        for session in range(sessions):
            prompts = request_json(api + f"/api/sessions/{experiment(session)}/prompts")
            if sum(prompt["outputTokens"] for prompt in prompts) != counts[session] * 10:
                raise AssertionError("Prompt counters differ from committed synthetic evidence")
        ordered = sorted(latencies.values())
        percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
        report = {
            "historySpans": history_spans, "sessions": sessions, "updates": updates,
            "updateIntervalSeconds": interval,
            "seedSeconds": round(seed_seconds, 2), "warmupSeconds": round(warm_seconds, 2),
            "p95Seconds": round(percentile(0.95), 2), "p99Seconds": round(percentile(0.99), 2),
            "restartRecoverySeconds": round(monotonic() - recovery_started, 2),
            "finalOutputTokens": (history_spans + updates + 1) * 10,
            "promptCountersMatch": True,
        }
        if report["p95Seconds"] > 30 or report["p99Seconds"] > 60:
            raise AssertionError("Freshness target failed: " + json.dumps(report))
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the isolated API/worker indexing pipeline with synthetic retained history.")
    parser.add_argument("--spans", type=int, default=730_000)
    parser.add_argument("--sessions", type=int, default=250)
    parser.add_argument("--updates", type=int, default=40)
    parser.add_argument("--interval", type=float, default=1)
    arguments = parser.parse_args()
    print(json.dumps(run_reliability(arguments.spans, arguments.sessions, arguments.updates, interval=arguments.interval), indent=2))


if __name__ == "__main__":
    main()