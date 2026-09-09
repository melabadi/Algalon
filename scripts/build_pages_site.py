from __future__ import annotations

import argparse
import hashlib
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


class StylesheetLinks(HTMLParser):
    def __init__(self, filename: str) -> None:
        super().__init__()
        self.filename = filename
        self.replacements: dict[str, str] = {}

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        values = dict(attributes)
        if tag != "link" or values.get("rel") != "stylesheet" or values.get("href") != "styles.css":
            return
        values["href"] = self.filename
        rendered = " ".join(
            f'{name}="{escape(value, quote=True)}"' if value is not None else name
            for name, value in values.items()
        )
        self.replacements[self.get_starttag_text()] = f"<link {rendered}>"


def build(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(ROOT / "site", output)
    stylesheet = output / "styles.css"
    digest = hashlib.sha256(stylesheet.read_bytes()).hexdigest()[:12]
    stylesheet_name = f"styles.{digest}.css"
    shutil.copy2(stylesheet, output / stylesheet_name)
    for page in output.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        links = StylesheetLinks(stylesheet_name)
        links.feed(text)
        links.close()
        for original, replacement in links.replacements.items():
            text = text.replace(original, replacement)
        page.write_text(text, encoding="utf-8")
    assets = output / "assets"
    data = output / "data"
    assets.mkdir()
    data.mkdir()
    for name in ("algalon-portfolio-desktop.png", "algalon-portfolio-mobile.png", "algalon-insights.png", "algalon-methodology-overview.png"):
        shutil.copy2(ROOT / "docs" / "images" / name, assets / name)
    shutil.copy2(ROOT / "web" / "public" / "favicon.ico", assets / "favicon.ico")
    shutil.copy2(ROOT / "config" / "value-model.example.json", data / "value-model.example.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static Algalon GitHub Pages artifact.")
    parser.add_argument("--output", default="_site", help="Output directory relative to the repository root.")
    arguments = parser.parse_args()
    output = Path(arguments.output)
    if not output.is_absolute():
        output = ROOT / output
    build(output)
    print(output)


if __name__ == "__main__":
    main()