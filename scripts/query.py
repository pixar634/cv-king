"""Read-only SQL against a profile's local database, or across all of them.

    .venv\\Scripts\\python scripts\\query.py "SELECT status, COUNT(*) FROM applications GROUP BY status"
    .venv\\Scripts\\python scripts\\query.py --tables
    .venv\\Scripts\\python scripts\\query.py --profile new-profile-5 "SELECT * FROM memory_facts"
    .venv\\Scripts\\python scripts\\query.py --all "SELECT profile_name, COUNT(*) FROM applications GROUP BY 1"

Databases open read-only: a query can't change them. --sync first copies the JSON files in (the app does this by
itself after every CV, status change and scan). Without --profile, the profile last used in the app. With --all,
every table gets two more columns, profile and profile_name, and holds the rows of every profile."""
import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, profiles  # noqa: E402


def _ro(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def _lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def open_one(pid: str | None, sync: bool) -> sqlite3.Connection:
    profiles.activate(pid)
    if sync or not db.path().exists():
        print(f"synced: {db.sync()}", file=sys.stderr)
    return sqlite3.connect(_ro(db.path()), uri=True)


def open_all(sync: bool) -> sqlite3.Connection:
    """One in-memory connection with each profile's database attached, and a view per table over all of them."""
    con = sqlite3.connect("file::memory:", uri=True)
    attached = []
    for i, p in enumerate(profiles.all_profiles()[:10]):   # SQLite attaches at most 10
        profiles.activate(p["id"])
        if sync and p.get("status", "ready") == "ready":
            print(f"{p['id']}: synced {sum(db.sync().values())} rows", file=sys.stderr)
        if db.path().exists():
            con.execute(f"ATTACH DATABASE ? AS p{i}", (_ro(db.path()),))
            attached.append((f"p{i}", p["id"], p.get("name", "")))
    tables: dict[str, list[tuple[str, str, str]]] = {}
    for a in attached:
        for (name,) in con.execute(f"SELECT name FROM {a[0]}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
            tables.setdefault(name, []).append(a)
    for name, where in tables.items():
        union = " UNION ALL ".join(f"SELECT {_lit(pid)} AS profile, {_lit(pname)} AS profile_name, * FROM {s}.{name}"
                                   for s, pid, pname in where)
        try:
            con.execute(f"CREATE TEMP VIEW {name} AS {union}")
        except sqlite3.OperationalError:   # columns differ between versions: that table is queried per profile
            pass
    return con


def names(con: sqlite3.Connection, everyone: bool) -> list[str]:
    sql = ("SELECT name FROM sqlite_temp_master WHERE type = 'view' ORDER BY name" if everyone else
           "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return [n for (n,) in con.execute(sql)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sql", nargs="?", help="one SELECT statement")
    ap.add_argument("--profile", help="profile id (see profiles/index.json); default: the last used")
    ap.add_argument("--all", action="store_true", help="query every profile's database together")
    ap.add_argument("--tables", action="store_true", help="list the tables and their columns")
    ap.add_argument("--sync", action="store_true", help="copy the JSON files into the database first")
    args = ap.parse_args()
    with closing(open_all(args.sync) if args.all else open_one(args.profile, args.sync)) as con:
        if args.tables or not args.sql:
            for name in names(con, args.all):
                cols = [c[1] for c in con.execute(f"PRAGMA table_info({name})")]
                n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                print(f"{name} ({n} rows): {', '.join(cols)}")
            return 0
        cur = con.execute(args.sql)
        print("\t".join(d[0] for d in cur.description or []))
        for row in cur:
            print("\t".join("" if v is None else str(v) for v in row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
