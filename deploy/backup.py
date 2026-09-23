"""Consistent SQLite backup. Refuses overwrite; never removes old backups."""
import argparse
import sqlite3
from pathlib import Path


def backup(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents overwriting an existing backup.
    with destination.open("xb"):
        pass
    try:
        with sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(destination) as dest:
                src.backup(dest)
                if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
    except Exception:
        raise RuntimeError("Backup failed; inspect the incomplete destination before using it") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--source", default="/data/remote.sqlite3")
    args = parser.parse_args()
    backup(args.source, args.destination)
