from contextlib import ExitStack
import json
import os
from pathlib import Path
import threading
from time import monotonic
import unittest
from uuid import uuid4

from scripts.docker_runtime import DockerRuntime
from scripts.indexing_reliability import trace_payload


ROOT = Path(__file__).resolve().parents[1]


class CollectorDurabilityTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ALGALON_DOCKER_TESTS") == "1", "Enable isolated Docker durability tests explicitly")
    def test_scrubbed_retry_survives_collector_process_loss(self) -> None:
        runtime = DockerRuntime()
        name = "algalon-retry-test-" + uuid4().hex[:12]
        receiver = name + "-receiver"
        collector = name + "-collector"
        volume = name + "-data"
        with ExitStack() as cleanup:
            runtime.run(("network", "create", name), capture=True)
            cleanup.callback(runtime.run, ("network", "rm", name), capture=True, check=False)
            runtime.run(("volume", "create", volume), capture=True)
            cleanup.callback(runtime.run, ("volume", "rm", volume), capture=True, check=False)
            cleanup.callback(runtime.run, ("rm", "-f", receiver), capture=True, check=False)
            cleanup.callback(runtime.run, ("rm", "-f", collector), capture=True, check=False)
            runtime.run((
                "run", "-d", "--name", receiver, "--network", name, "--network-alias", "app",
                "--mount", f"type=bind,source={runtime.docker_path(ROOT / 'test/fixtures/collector_retry_receiver.py')},target=/receiver.py,readonly",
                "python:3.14-slim", "python", "-B", "/receiver.py", "serve",
            ), capture=True)
            runtime.run((
                "run", "-d", "--name", collector, "--network", name, "--network-alias", "collector",
                "--user", "0:0", "--mount", f"type=volume,source={volume},target=/data",
                "--mount", f"type=bind,source={runtime.docker_path(ROOT / 'config/otel-collector.yaml')},target=/config.yaml,readonly",
                "-e", "VALUE_DATA_DIR=/data", "-e", "OTEL_GRPC_ENDPOINT=0.0.0.0:4317",
                "-e", "OTEL_HTTP_ENDPOINT=0.0.0.0:4318", "-e", "OTEL_HEALTH_ENDPOINT=0.0.0.0:13133",
                "-e", "VICTORIA_METRICS_OTLP_ENDPOINT=http://app:8000/metrics",
                "otel/opentelemetry-collector-contrib:0.157.0", "--config=/config.yaml",
            ), capture=True)

            def action(command: str, payload: str | None = None):
                return runtime.run(
                    ("exec", "-i", receiver, "python", "-B", "/receiver.py", command),
                    input_text=payload, capture=True, check=False,
                )

            def wait_for(predicate) -> None:
                deadline = monotonic() + 60
                while monotonic() < deadline:
                    if predicate():
                        return
                    threading.Event().wait(0.5)
                self.fail("Isolated Collector retry test did not converge")

            wait_for(lambda: action("collector-ready").returncode == 0)
            payload = trace_payload(0, 0, 1_789_000_000_000, prompt=True)
            payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
                {"key": "command", "value": {"stringValue": "synthetic private command"}}
            )
            self.assertEqual(action("send", json.dumps(payload)).returncode, 0)
            runtime.run(("kill", collector), capture=True)
            runtime.run(("start", collector), capture=True)
            self.assertEqual(action("allow").returncode, 0)
            wait_for(lambda: json.loads(action("status").stdout)["receivedSpans"] == 1)
            final = json.loads(action("status").stdout)
            self.assertFalse(final["privateAttributeReceived"])


if __name__ == "__main__":
    unittest.main()