from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


class PublicSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        cls.paths = sorted({
            name for name in result.stdout.split("\0")
            if name and (ROOT / name).is_file()
        })

    def test_source_excludes_private_and_generated_files(self) -> None:
        forbidden_parts = {
            ".copilot-value", ".tools", ".ci-artifacts", ".vscode",
            "node_modules", "dist", "coverage", "__pycache__",
        }
        forbidden_roots = {"data", "artifacts", "_site"}
        forbidden_names = {"value-model.local.json", ".npmrc", ".pypirc"}
        forbidden_suffixes = {".pyz", ".zip", ".db", ".sqlite", ".sqlite3", ".log", ".jsonl"}
        forbidden_prefixes = (
            ".github/acl/", ".github/compliance/", ".github/policies/",
        )
        for name in self.paths:
            with self.subTest(path=name):
                path = Path(name)
                self.assertFalse(forbidden_parts.intersection(path.parts))
                self.assertNotIn(path.parts[0], forbidden_roots)
                self.assertNotIn(path.name, forbidden_names)
                self.assertNotIn(path.suffix.lower(), forbidden_suffixes)
                self.assertFalse(name.startswith(forbidden_prefixes))
                self.assertFalse(path.name.startswith(".env") and path.name != ".env.example")

    def test_lockfiles_use_only_public_registry_urls(self) -> None:
        for name in ("package-lock.json", "web/package-lock.json"):
            lock = json.loads((ROOT / name).read_text(encoding="utf-8"))
            for package, metadata in lock["packages"].items():
                if resolved := metadata.get("resolved"):
                    with self.subTest(path=name, package=package):
                        url = urlsplit(resolved)
                        self.assertEqual(url.scheme, "https")
                        self.assertEqual(url.netloc, "registry.npmjs.org")
                        self.assertFalse(url.query or url.fragment)

    def test_text_has_no_corporate_endpoints_or_personal_contacts(self) -> None:
        patterns = {
            "corporate endpoint": re.compile(
                r"(?i)\b[A-Za-z0-9.-]+\.(?:visualstudio\.com|sharepoint\.com|microsoft\.io)\b"
            ),
            "email address": re.compile(
                r"(?i)\b[A-Z0-9._%+-]+@(?!(?:example\.(?:com|org|net)|users\.noreply\.github\.com)\b)"
                r"[A-Z0-9.-]+\.[A-Z]{2,}\b"
            ),
            "personal workstation path": re.compile(
                r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+|/Users/|/home/)(?!example\b|test\b|user\b|runner\b)"
                r"[A-Za-z0-9_.-]+"
            ),
            "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        }
        for name in self.paths:
            payload = (ROOT / name).read_bytes()
            if b"\0" in payload:
                continue
            text = payload.decode("utf-8", errors="replace")
            for category, pattern in patterns.items():
                with self.subTest(path=name, category=category):
                    self.assertIsNone(pattern.search(text), f"{category} in {name}")


if __name__ == "__main__":
    unittest.main()