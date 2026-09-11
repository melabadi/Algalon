from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from scripts import copilot_value, release_smoke
from test_copilot_value_release import FakeResponse


class ReleaseSmokeTransportTests(unittest.TestCase):
    def test_retries_transient_otlp_connection_failure(self) -> None:
        with (
            patch.object(
                release_smoke,
                "urlopen",
                side_effect=[URLError("starting"), FakeResponse(202)],
            ) as urlopen,
            patch.object(release_smoke.time, "sleep") as sleep,
        ):
            release_smoke.post_json(
                "http://collector/v1/traces",
                {"resourceSpans": []},
                retry_seconds=1,
            )

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_pyz_smoke_uses_one_installation_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            (root / "package.json").write_text(
                json.dumps({"version": "1.0.0"}), encoding="utf-8"
            )
            bundle = root / "release.pyz"
            bundle.write_text("application", encoding="utf-8")
            extraction = Path(temporary_directory) / "extraction"
            runtime = Mock()
            runtime.run.return_value = subprocess.CompletedProcess([], 0, stdout="")
            user_journey = Mock()
            arguments = argparse.Namespace(
                bundle=str(bundle),
                skip_build=True,
                skip_telemetry=False,
                keep_extracted=False,
            )

            with (
                patch.object(copilot_value, "install_root", return_value=root),
                patch.object(copilot_value.tempfile, "mkdtemp", return_value=str(extraction)),
                patch.object(copilot_value, "DockerRuntime", return_value=runtime),
                patch.object(copilot_value, "run_checked") as run_checked,
                patch.object(copilot_value, "compose"),
                patch.object(copilot_value.time, "sleep"),
            ):
                self.assertEqual(
                    copilot_value.command_smoke_bundle(arguments, user_journey),
                    0,
                )

            commands = [invocation.args[0] for invocation in run_checked.call_args_list]
            self.assertEqual(len(commands), 3)
            self.assertEqual(
                sum(command[1] == str(bundle.resolve()) for command in commands), 1
            )
            self.assertIn("--no-start", commands[0])
            self.assertEqual(commands[1][-1], "start")
            self.assertEqual(commands[2][-1], "--telemetry")
            user_journey.assert_called_once_with()
            runtime.release_wsl_keepalive.assert_called_once_with(
                extraction / "repository" / ".copilot-value"
            )

    def test_user_journey_requires_telemetry(self) -> None:
        arguments = argparse.Namespace(skip_telemetry=True)
        with self.assertRaisesRegex(
            copilot_value.CopilotValueError,
            "requires the Docker stack",
        ):
            copilot_value.command_smoke_bundle(arguments, Mock())


if __name__ == "__main__":
    unittest.main()
