"""Read-only SQL against a profile's local database.

    .venv\\Scripts\\python scripts\\query.py "SELECT status, COUNT(*) FROM applications GROUP BY status"
    .venv\\Scripts\\python scripts\\query.py --tables
    .venv\\Scripts\\python scripts\\query.py --sync --profile new-profile-5 "SELECT ..."

The database opens read-only: a query can't change it. --sync first copies the JSON files in (the app does this
by itself after every CV and scan). Without --profile, the profile last used in the app."""
import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, profiles  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sql", nargs="?", help="one SELECT statement")
    ap.add_argument("--profile", help="profile id (see profiles/index.json); default: the last used")
    ap.add_argument("--tables", action="store_true", help="list the tables and their columns")
    ap.add_argument("--sync", action="store_true", help="copy the JSON files into the database first")
    args = ap.parse_args()
    profiles.activate(args.profile)
    if args.sync or not db.path().exists():
        print(f"synced: {db.sync()}", file=sys.stderr)
    with closing(sqlite3.connect(f"file:{db.path().as_posix()}?mode=ro", uri=True)) as con:
        if args.tables or not args.sql:
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
                cols = [c[1] for c in con.execute(f"PRAGMA table_info({name})")]
                n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                print(f"{name} ({n} rows): {', '.join(cols)}")
            return 0
        cur = con.execute(args.sql)
        names = [d[0] for d in cur.description or []]
        print("\t".join(names))
        for row in cur:
            print("\t".join("" if v is None else str(v) for v in row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
