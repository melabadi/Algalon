#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Iterable


STABLE_VERSION = re.compile(r"^(?:v)?(\d+)\.(\d+)\.(\d+)$")


def parse_stable_version(value: str) -> tuple[int, int, int] | None:
    match = STABLE_VERSION.fullmatch(value.strip())
    return tuple(map(int, match.groups())) if match else None


def next_patch_version(package_version: str, tags: Iterable[str]) -> str:
    package = parse_stable_version(package_version)
    if package is None:
        raise ValueError(f"package.json version '{package_version}' is not stable semantic versioning.")
    current = max(
        [package, *(version for tag in tags if (version := parse_stable_version(tag)) is not None)]
    )
    return f"{current[0]}.{current[1]}.{current[2] + 1}"


def repository_tags(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return []
    result = subprocess.run(
        ("git", "tag", "--list", "v*"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def next_repository_patch(root: Path) -> str:
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    return next_patch_version(str(package["version"]), repository_tags(root))


def main() -> None:
    print(next_repository_patch(Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    main()