from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

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
        if tag == "source" and (source := values.get("srcset")):
            self.local_links.extend(candidate.strip().split()[0] for candidate in source.split(","))
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
        cls.methodology = (SITE / "methodology.html").read_text(encoding="utf-8")
        cls.parser = SiteParser()
        cls.parser.feed(cls.html)
        cls.parser.close()
        cls.methodology_parser = SiteParser()
        cls.methodology_parser.feed(cls.methodology)
        cls.methodology_parser.close()

    def test_has_accessible_structure_and_valid_anchor_targets(self) -> None:
        for parser in (self.parser, self.methodology_parser):
            self.assertEqual(parser.main_count, 1)
            self.assertEqual(parser.h1_count, 1)
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            self.assertTrue(all(alt for alt in parser.image_alts))
            for link in parser.hash_links:
                self.assertIn(link.removeprefix("#"), parser.ids)

    def test_references_only_staged_local_assets(self) -> None:
        expected = {
            "styles.css",
            "assets/favicon.ico",
            "assets/algalon-portfolio-desktop.png",
            "assets/algalon-portfolio-mobile.png",
            "assets/algalon-insights.png",
            "methodology.html",
        }
        self.assertEqual(set(self.parser.local_links), expected)
        self.assertEqual(set(self.methodology_parser.local_links), {
            "./", "./#install", "./#features", "methodology.html", "styles.css", "app.js",
            "assets/favicon.ico", "assets/algalon-methodology-overview.png",
        })
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
            self.assertIn(phrase, self.methodology)
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

    def test_home_prioritizes_setup_and_links_to_detailed_methodology(self) -> None:
        self.assertLess(self.html.index('id="install"'), self.html.index('id="features"'))
        self.assertIn('href="methodology.html"', self.html)
        self.assertNotIn('id="break-even-form"', self.html)
        self.assertNotIn('class="scenario-table"', self.html)
        self.assertNotIn('<script src="app.js"', self.html)
        self.assertTrue((SITE / "methodology.html").is_file())

    def test_product_screenshots_are_high_resolution_without_private_metadata(self) -> None:
        for name in ("algalon-portfolio-desktop", "algalon-portfolio-mobile", "algalon-insights", "algalon-methodology-overview"):
            with self.subTest(image=name):
                content = (ROOT / "docs" / "images" / f"{name}.png").read_bytes()
                self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
                width, height = struct.unpack(">II", content[16:24])
                self.assertGreaterEqual(width, 750 if name.endswith("mobile") else 2_000)
                self.assertGreaterEqual(height, 600)
                offset = 8
                while offset < len(content):
                    length = struct.unpack(">I", content[offset:offset + 4])[0]
                    self.assertNotIn(content[offset + 4:offset + 8], (b"tEXt", b"zTXt", b"iTXt", b"eXIf"))
                    offset += length + 12

    def test_pages_workflow_stages_only_required_runtime_inputs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        for required in (
            "actions/configure-pages@",
            "actions/upload-pages-artifact@",
            "actions/deploy-pages@",
            "python scripts/build_pages_site.py",
        ):
            self.assertIn(required, workflow)

    def test_built_pages_refresh_the_stylesheet_when_its_content_changes(self) -> None:
        original = (SITE / "styles.css").read_bytes()
        changed = original + b"\n.quick-start { background: #17241e; }\n"
        copytree = shutil.copytree

        def copy_with_changed_styles(source: Path, destination: Path) -> None:
            copytree(source, destination)
            (destination / "styles.css").write_bytes(changed)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "site"
            build(output)
            for content in (original, changed):
                if content != original:
                    with patch("scripts.build_pages_site.shutil.copytree", side_effect=copy_with_changed_styles):
                        build(output)
                digest = hashlib.sha256(content).hexdigest()[:12]
                for name in ("index.html", "methodology.html"):
                    parser = SiteParser()
                    parser.feed((output / name).read_text(encoding="utf-8"))
                    self.assertIn(f"styles.{digest}.css", parser.local_links)
                    self.assertNotIn("styles.css", parser.local_links)
                self.assertEqual((output / f"styles.{digest}.css").read_bytes(), content)
                self.assertEqual(len(list(output.glob("styles.*.css"))), 1)
                self.assertEqual((output / "styles.css").read_bytes(), content)

    def test_builds_the_complete_static_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "site"
            build(output)
            expected = {
                ".nojekyll",
                "app.js",
                "assets/favicon.ico",
                "assets/algalon-methodology-overview.png",
                "assets/algalon-portfolio-desktop.png",
                "assets/algalon-portfolio-mobile.png",
                "assets/algalon-insights.png",
                "data/value-model.example.json",
                "index.html",
                "methodology.html",
                "styles.css",
                f"styles.{hashlib.sha256((SITE / 'styles.css').read_bytes()).hexdigest()[:12]}.css",
            }
            actual = {
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()