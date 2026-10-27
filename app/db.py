"""A local SQLite record of the search, one file per profile (`<data dir>/jobmagnet.db`, never committed).

The JSON files stay the source of truth for what each application *is*; this records what happened *when*, which
nothing else keeps: every status change, the moment it happens. Read it with any SQLite tool, or later with the
Insights views built on it."""
import datetime as dt
import sqlite3
from contextlib import closing

from .config import data_dir

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id      TEXT NOT NULL,
  company     TEXT NOT NULL DEFAULT '',
  role        TEXT NOT NULL DEFAULT '',
  from_status TEXT,                      -- NULL on an application's first event
  to_status   TEXT NOT NULL,
  at          TEXT NOT NULL,             -- local time, ISO 8601
  source      TEXT NOT NULL,             -- tailor | app | extension | seed
  note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_app ON status_events(app_id);
CREATE INDEX IF NOT EXISTS idx_events_to ON status_events(to_status);
"""
SOURCES = ("tailor", "app", "extension", "seed")


def path():
    return data_dir() / "jobmagnet.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(path(), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return con


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def record_status(app_id: str, company: str, role: str, from_status: str | None, to_status: str,
                  source: str = "app", note: str = "", at: str | None = None) -> None:
    source = source if source in SOURCES else "app"
    with closing(connect()) as con, con:
        con.execute("INSERT INTO status_events (app_id, company, role, from_status, to_status, at, source, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (app_id, company, role, from_status, to_status, at or _now(), source, note))


def moved(old_id: str, new_id: str) -> None:
    """A re-tailor replaces an application with a new one: its history comes along."""
    with closing(connect()) as con, con:
        con.execute("UPDATE status_events SET app_id = ? WHERE app_id = ?", (new_id, old_id))


def ensure_seeded() -> None:
    """Call before changing the tracker, so the seed sees it as it was."""
    with closing(connect()) as con, con:
        _seed(con)


def history(app_id: str) -> list[dict]:
    with closing(connect()) as con, con:
        _seed(con)
        rows = con.execute("SELECT from_status, to_status, at, source, note FROM status_events WHERE app_id = ? ORDER BY at, id",
                           (app_id,)).fetchall()
    return [dict(r) for r in rows]


def _seed(con: sqlite3.Connection) -> None:
    """Once per database: what the tracker already knew before history began. The CV's creation is exact; a later
    status only has the day it was marked applied (or none), so those events say they are approximate."""
    if con.execute("SELECT 1 FROM meta WHERE key = 'seeded'").fetchone():
        return
    from . import tracker
    known = {e["app_id"] for e in con.execute("SELECT DISTINCT app_id FROM status_events")}
    for r in tracker.load():
        if r["id"] in known:
            continue
        ev = [(r["id"], r["company"], r["role"], None, "Generated", r["created"], "seed", "")]
        if r.get("status") and r["status"] != "Generated":
            when = (r.get("applied_on") or r["created"][:10]) + "T00:00:00"
            ev.append((r["id"], r["company"], r["role"], "Generated", r["status"], when, "seed",
                       "recorded before history began; the date is approximate"))
        con.executemany("INSERT INTO status_events (app_id, company, role, from_status, to_status, at, source, note) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ev)
    con.execute("INSERT INTO meta VALUES ('seeded', ?)", (_now(),))
