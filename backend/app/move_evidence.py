"""Point evidence at a new folder after moving the data to another machine.

The database keeps each screenshot's full path, so a database copied from Windows
(E:\\...\\backend\\evidence\\<session>\\shot.png) to a server (/data/evidence/...) still points
at the Windows folder, and the screenshots don't show. This rewrites those paths: whatever
came after the "evidence" folder is kept, under EVIDENCE_DIR (or --to).

    python -m app.move_evidence            # shows what would change
    python -m app.move_evidence --apply    # changes it
"""
import argparse
import re
import sqlite3
from pathlib import Path

from app.config import settings

COLUMNS = [("evidence", "file_path"), ("reproduction_runs", "screenshot_path")]


def moved(old: str | None, root: str, folder: str = "evidence") -> str | None:
    """The same file under root: the part after the last `folder` component, in this machine's
    form. None when the path has no such folder (or is empty), so it is left alone."""
    if not old:
        return None
    parts = [p for p in re.split(r"[\\\\/]+", old) if p]
    if folder not in parts:
        return None
    rest = parts[len(parts) - 1 - parts[::-1].index(folder) + 1:]
    new = str(Path(root, *rest))
    return new if new != old else None


def run(db_file: str, root: str, apply: bool) -> list[tuple[str, str, str]]:
    """(table.column, old, new) for every path that changes; written only with apply."""
    changes = []
    with sqlite3.connect(db_file) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column in COLUMNS:
            if table not in tables:
                continue
            for row_id, old in con.execute(f'SELECT id, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL').fetchall():
                new = moved(old, root)
                if new:
                    changes.append((f"{table}.{column}", old, new))
                    if apply:
                        con.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE id = ?', (new, row_id))
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", default=settings.evidence_dir, help="the evidence folder here (default: EVIDENCE_DIR)")
    ap.add_argument("--apply", action="store_true", help="change the database (without it, only show)")
    args = ap.parse_args()
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise SystemExit("Only SQLite databases are supported.")
    changes = run(settings.database_url[len(prefix):], args.to, args.apply)
    for where, old, new in changes[:10]:
        print(f"{where}: {old} -> {new}")
    if len(changes) > 10:
        print(f"... and {len(changes) - 10} more")
    print(f"{len(changes)} paths {'changed' if args.apply else 'would change (run again with --apply)'}.")


if __name__ == "__main__":
    main()
