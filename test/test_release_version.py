from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.next_patch_version import next_patch_version, next_repository_patch, parse_stable_version


class ReleaseVersionTests(unittest.TestCase):
    def test_parses_only_stable_semantic_versions(self) -> None:
        self.assertEqual(parse_stable_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_stable_version("1.2.3"), (1, 2, 3))
        self.assertIsNone(parse_stable_version("v1.2.3-beta.1"))
        self.assertIsNone(parse_stable_version("release-1.2.3"))

    def test_increments_the_highest_package_or_tag_patch(self) -> None:
        self.assertEqual(next_patch_version("0.2.0", []), "0.2.1")
        self.assertEqual(next_patch_version("0.2.0", ["v0.2.9", "v0.2.3"]), "0.2.10")
        self.assertEqual(next_patch_version("0.3.0", ["v0.2.9", "invalid"]), "0.3.1")

    def test_rejects_an_invalid_package_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "not stable semantic versioning"):
            next_patch_version("0.2.0-beta.1", [])

    def test_reads_package_and_repository_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "package.json").write_text(json.dumps({"version": "1.4.2"}), encoding="utf-8")
            (root / ".git").mkdir()
            completed = subprocess.CompletedProcess([], 0, stdout="v1.4.4\nv1.4.3\n", stderr="")
            with patch("scripts.next_patch_version.subprocess.run", return_value=completed) as run:
                self.assertEqual(next_repository_patch(root), "1.4.5")
            run.assert_called_once_with(
                ("git", "tag", "--list", "v*"),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_uses_package_version_when_git_metadata_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "package.json").write_text(json.dumps({"version": "2.1.7"}), encoding="utf-8")
            with patch("scripts.next_patch_version.subprocess.run") as run:
                self.assertEqual(next_repository_patch(root), "2.1.8")
            run.assert_not_called()

    def test_ci_publishes_smoke_tested_assets_on_main(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        required_contract = (
            "fetch-depth: 0",
            "python scripts/next_patch_version.py",
            "npm version $version --no-git-tag-version",
            "needs: [bundle, linux-bundle-smoke]",
            "smoke-bundle --skip-build --bundle artifacts/copilot-value-dashboard-${{ needs.bundle.outputs.version }}.pyz",
            "contents: write",
            "github.ref == 'refs/heads/main'",
            "gh release create",
            "artifacts/copilot-value-dashboard-${version}.pyz",
            "artifacts/copilot-value-dashboard-${version}.zip",
            "sha256sum --check *.sha256",
        )
        for expected in required_contract:
            with self.subTest(expected=expected):
                self.assertIn(expected, workflow)
        self.assertNotIn("git commit", workflow)
        self.assertNotIn("git push", workflow)


if __name__ == "__main__":
    unittest.main()