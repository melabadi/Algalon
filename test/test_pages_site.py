from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_pages_site import build


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.local_links: list[str] = []
        self.hash_links: list[str] = []
        self.image_alts: list[str | None] = []
        self.h1_count = 0
        self.main_count = 0

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        values = dict(attributes)
        if identifier := values.get("id"):
            self.ids.append(identifier)
        if tag == "a" and (href := values.get("href")):
            if href.startswith("#"):
                self.hash_links.append(href)
            elif not href.startswith(("https://", "http://", "mailto:")):
                self.local_links.append(href)
        if tag in {"link", "script"}:
            reference = values.get("href") or values.get("src")
            if reference and not reference.startswith(("https://", "http://")):
                self.local_links.append(reference)
        if tag == "img":
            self.image_alts.append(values.get("alt"))
            if source := values.get("src"):
                if not source.startswith(("https://", "http://")):
                    self.local_links.append(source)
        if tag == "h1":
            self.h1_count += 1
        if tag == "main":
            self.main_count += 1


class PagesSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (SITE / "index.html").read_text(encoding="utf-8")
        cls.script = (SITE / "app.js").read_text(encoding="utf-8")
        cls.styles = (SITE / "styles.css").read_text(encoding="utf-8")
        cls.parser = SiteParser()
        cls.parser.feed(cls.html)
        cls.parser.close()

    def test_has_accessible_structure_and_valid_anchor_targets(self) -> None:
        self.assertEqual(self.parser.main_count, 1)
        self.assertEqual(self.parser.h1_count, 1)
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))
        self.assertTrue(all(alt for alt in self.parser.image_alts))
        for link in self.parser.hash_links:
            self.assertIn(link.removeprefix("#"), self.parser.ids)

    def test_references_only_staged_local_assets(self) -> None:
        expected = {
            "styles.css",
            "app.js",
            "assets/favicon.ico",
            "assets/algalon-overall.png",
            "assets/algalon-methodology.png",
        }
        self.assertEqual(set(self.parser.local_links), expected)
        self.assertIn('url("assets/algalon-overall.png")', self.styles)
        self.assertIn("data/value-model.example.json", self.script)
        self.assertTrue((SITE / ".nojekyll").is_file())

    def test_explains_claim_boundary_and_current_formula(self) -> None:
        for phrase in (
            "not causal proof",
            "Evidence boundary",
            "Modeled return on observed AI usage cost",
            "Interactive break-even explorer",
            "Inputs that still require local evidence",
            "Research-anchored shipped defaults",
            "One loopback stack for every local repository",
        ):
            self.assertIn(phrase, self.html)
        self.assertIn("prefers-reduced-motion", self.styles)

    def test_uses_the_checked_in_evidence_register(self) -> None:
        config = json.loads((ROOT / "config" / "value-model.example.json").read_text(encoding="utf-8"))
        sources = config["benchmark"]["calibrationSources"]
        self.assertEqual(len(sources), 10)
        self.assertEqual(config["loadedHourlyRateUsd"], 92)
        self.assertEqual(config["benchmark"]["typingWordsPerMinute"], 52)
        self.assertEqual(
            config["benchmark"]["scenarios"]["pessimistic"]["planning"]["interactionMinutesPerTool"],
            0.12,
        )
        support_levels = {
            level
            for source in sources
            for level in source.get("supportLevels", {}).values()
        }
        self.assertEqual(support_levels, {"direct", "proxy", "context"})
        self.assertIn("calibrationSources", self.script)

    def test_introduces_features_and_verifiable_installation(self) -> None:
        for required in (
            'id="features"',
            "Portfolio comparisons",
            "Session Insights",
            "Prompt evidence",
            "Local CSV export",
            "Windows PowerShell",
            "macOS and Linux",
            "ZIP: fresh manual installation",
            "Get-FileHash",
            "shasum -a 256 --check",
            "gh attestation verify",
            "Content-Security-Policy",
            "https://github.com/melabadi/Algalon/releases/latest",
            "https://github.com/melabadi/Algalon/security/policy",
            "http://127.0.0.1:3000/",
        ):
            self.assertIn(required, self.html)

    def test_pages_workflow_stages_only_required_runtime_inputs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        for required in (
            "actions/configure-pages@",
            "actions/upload-pages-artifact@",
            "actions/deploy-pages@",
            "python scripts/build_pages_site.py",
        ):
            self.assertIn(required, workflow)

    def test_builds_the_complete_static_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "site"
            build(output)
            expected = {
                ".nojekyll",
                "app.js",
                "assets/favicon.ico",
                "assets/algalon-methodology.png",
                "assets/algalon-overall.png",
                "data/value-model.example.json",
                "index.html",
                "styles.css",
            }
            actual = {
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()