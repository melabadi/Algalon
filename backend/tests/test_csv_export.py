from __future__ import annotations

import csv
from io import StringIO
import unittest

from backend.app.csv_export import build_csv_export


class CsvExportTests(unittest.TestCase):
    def test_flattens_all_evidence_into_readable_prompt_rows(self) -> None:
        sessions = [
            {
                "experiment": "session-1",
                "sourceEvidenceComplete": True,
                "usage": {"models": [{"model": "gpt-5", "requests": 2}]},
                "benchmark": {"scenarios": {"base": {"netValueUsd": 12.5}}},
            },
            {"experiment": "session-without-prompts", "sourceEvidenceComplete": False},
        ]
        prompts = {
            "session-1": [
                {
                    "promptId": "prompt-1",
                    "content": '=SUM(A1:A2)\n"quoted context"',
                    "models": {"gpt-5": 2},
                }
            ]
        }

        payload = build_csv_export(sessions, prompts)

        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        reader = csv.DictReader(StringIO(payload.decode("utf-8-sig")))
        rows = list(reader)
        self.assertEqual(len(rows), 2)
        self.assertEqual(reader.fieldnames[:5], [
            "Record Type",
            "Session > Experiment",
            "Prompt > Prompt ID",
            "Prompt > Content",
            "Prompt > Models > Gpt-5",
        ])
        self.assertLess(
            reader.fieldnames.index("Prompt > Content"),
            reader.fieldnames.index("Session > Source Evidence Complete"),
        )
        self.assertEqual(rows[0]["Record Type"], "Prompt")
        self.assertEqual(rows[0]["Session > Experiment"], "session-1")
        self.assertEqual(rows[0]["Session > Source Evidence Complete"], "Yes")
        self.assertEqual(rows[0]["Session > Usage > Models > 1 > Model"], "gpt-5")
        self.assertEqual(rows[0]["Session > Benchmark > Scenarios > Base > Net Value USD"], "12.5")
        self.assertEqual(rows[0]["Prompt > Models > Gpt-5"], "2")
        self.assertEqual(rows[0]["Prompt > Content"], '\'=SUM(A1:A2)\n"quoted context"')
        self.assertEqual(rows[1]["Record Type"], "Session")
        self.assertEqual(rows[1]["Session > Experiment"], "session-without-prompts")
        self.assertEqual(rows[1]["Prompt > Prompt ID"], "")

    def test_empty_export_still_has_identifiable_headers(self) -> None:
        payload = build_csv_export([], {})

        self.assertEqual(
            payload.decode("utf-8-sig"),
            "Record Type,Session > Experiment,Prompt > Prompt ID\r\n",
        )


if __name__ == "__main__":
    unittest.main()