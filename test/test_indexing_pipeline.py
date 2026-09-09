from pathlib import Path
import shutil
import unittest

from scripts.indexing_reliability import run_reliability


class IndexingPipelineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node") and (Path(__file__).resolve().parents[1] / "dist/src/continuous-cli.js").exists(), "Build the worker before running the pipeline test")
    def test_committed_updates_survive_api_and_worker_restart(self) -> None:
        result = run_reliability(1_200, 4, 4, interval=0.2, timeout=90)
        self.assertTrue(result["promptCountersMatch"])
        self.assertEqual(result["finalOutputTokens"], 12_050)
        self.assertLessEqual(result["p99Seconds"], 60)


if __name__ == "__main__":
    unittest.main()