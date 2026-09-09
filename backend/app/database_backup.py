from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def backup_database(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination or destination.exists():
        raise FileExistsError("Backup destination must be a new file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
            closing(sqlite3.connect(staging)) as backup,
        ):
            original.backup(backup, pages=1_000)
            if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Backup integrity check failed")
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent local SQLite backup or restore into a new database.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    backup_database(arguments.source, arguments.destination)
    print("SQLite backup verified")


if __name__ == "__main__":
    main()