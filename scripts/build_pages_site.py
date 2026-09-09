from __future__ import annotations

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


def build(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(ROOT / "site", output)
    assets = output / "assets"
    data = output / "data"
    assets.mkdir()
    data.mkdir()
    for name in ("algalon-overall.png", "algalon-methodology.png"):
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