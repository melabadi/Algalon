from __future__ import annotations

import json
from pathlib import Path
import unittest

from backend.app.intervals import union_interval_duration


ROOT = Path(__file__).resolve().parents[2]


class IntervalContractTests(unittest.TestCase):
    def test_matches_shared_interval_union_contract(self) -> None:
        fixtures = json.loads(
            (ROOT / "test" / "fixtures" / "interval-union.json").read_text(
                encoding="utf-8"
            )
        )
        for fixture in fixtures:
            with self.subTest(fixture["name"]):
                intervals = [tuple(interval) for interval in fixture["intervals"]]
                self.assertEqual(
                    union_interval_duration(intervals),
                    fixture["expected"],
                )


if __name__ == "__main__":
    unittest.main()
