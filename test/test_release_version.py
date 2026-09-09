from __future__ import annotations

from pathlib import Path
import json
import re
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
            "uses: ./.github/workflows/security.yml",
            "needs: [test, security]",
            "needs: [bundle, linux-bundle-smoke]",
            "ALGALON_DOCKER_TESTS: '1'",
            "test_collector_durability.py",
            "smoke-bundle --skip-build --bundle artifacts/copilot-value-dashboard-${{ needs.bundle.outputs.version }}.pyz",
            "contents: write",
            "github.ref == 'refs/heads/main'",
            "gh release create",
            "actions/attest-build-provenance@",
            "attestations: write",
            "--draft=false --latest",
            "artifacts/copilot-value-dashboard-${version}.pyz",
            "artifacts/copilot-value-dashboard-${version}.zip",
            "sha256sum --check *.sha256",
        )
        for expected in required_contract:
            with self.subTest(expected=expected):
                self.assertIn(expected, workflow)
        self.assertNotIn("git commit", workflow)
        self.assertNotIn("git push", workflow)

    def test_external_workflow_actions_are_immutable(self) -> None:
        directory = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for path in directory.glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            self.assertNotIn("pull_request_target:", workflow)
            for action in re.findall(r"(?m)^\s+(?:-\s+)?uses:\s*(\S+)", workflow):
                if not action.startswith("./"):
                    with self.subTest(workflow=path.name, action=action):
                        self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_large_indexing_reliability_check_is_repeatable(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/indexing-reliability.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("python -m scripts.indexing_reliability --spans 730000 --sessions 250 --updates 40", workflow)
        self.assertNotIn("continue-on-error", workflow)

    def test_security_scans_fail_closed(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "security.yml").read_text(
            encoding="utf-8"
        )
        for required in (
            "javascript-typescript, python",
            "queries: security-extended",
            "if findings:",
            "gitleaks/gitleaks-action@",
            "fetch-depth: 0",
            "npm audit --audit-level=low",
            "npm audit --prefix web --audit-level=low",
            "pip_audit -r requirements-dev.txt --strict",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("|| true", workflow)
        codeql_revisions = re.findall(r"uses: github/codeql-action/(?:init|analyze)@([0-9a-f]{40})", workflow)
        self.assertEqual(len(codeql_revisions), 2)
        self.assertEqual(len(set(codeql_revisions)), 1)


if __name__ == "__main__":
    unittest.main()