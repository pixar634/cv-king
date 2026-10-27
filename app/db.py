"""A local SQLite record of the search, one file per profile (`<data dir>/jobmagnet.db`, never committed).

Two kinds of table:
- `status_events` is the record nothing else keeps: every status change, the moment it happens. Never rebuilt.
- The rest are copies of the JSON files (the source of truth), rebuilt whole by `sync()`, so they can be queried
  together: applications, their job descriptions and keyword results, and every job Discover has seen.

Read it with any SQLite tool, or `python scripts/query.py "SELECT ..."` (read-only)."""
import contextvars
import datetime as dt
import hashlib
import json
import sqlite3
import threading
from contextlib import closing

from .config import data_dir, output_dir

SCHEMA_VERSION = 2
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

-- Copies of the JSON files, rebuilt whole by sync().
CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY, created TEXT, company TEXT, role TEXT, status TEXT, applied_on TEXT,
  market TEXT, engine TEXT, layout TEXT, pages TEXT,
  score_before INTEGER, score_after INTEGER, score_ceiling INTEGER,
  discover_match INTEGER,                -- the Discover card's number, when the job came from Discover
  job_url TEXT, job_id TEXT, location TEXT, posted TEXT, domain TEXT, notes TEXT,
  seniority TEXT, job_domain TEXT        -- what the writer read from the posting
);
CREATE TABLE IF NOT EXISTS job_descriptions (app_id TEXT PRIMARY KEY, text TEXT, chars INTEGER, sha1 TEXT);
CREATE TABLE IF NOT EXISTS keywords (
  app_id TEXT, term TEXT, tier TEXT, weight REAL,
  covered INTEGER, covered_before INTEGER, in_profile INTEGER   -- in this CV / in the CV before / in the profile
);
CREATE INDEX IF NOT EXISTS idx_keywords_term ON keywords(term);
CREATE TABLE IF NOT EXISTS jobs_seen (
  id TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, url TEXT, posted TEXT, first_seen TEXT,
  source TEXT, method TEXT, remote INTEGER, match INTEGER, estimate INTEGER, excluded INTEGER, reason TEXT,
  dismissed INTEGER
);
CREATE TABLE IF NOT EXISTS scans (at TEXT, sources INTEGER, read INTEGER, new INTEGER);
"""
SOURCES = ("tailor", "app", "extension", "seed")


def path():
    return data_dir() / "jobmagnet.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(path(), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
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


def _json(path):
    try:
        return json.loads(path.read_text("utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def sync() -> dict:
    """Rebuild the copied tables from the JSON files, in one transaction. Safe to run any time: the status history
    is never touched, and a half-written file is skipped rather than half-copied."""
    from . import tracker
    apps, descs, kws = [], [], []
    for r in tracker.load():
        folder = output_dir() / r["id"]
        rep = _json(folder / "report.json") or {}
        meta, job = rep.get("jd_meta") or {}, rep.get("job") or {}
        apps.append((r["id"], r.get("created"), r.get("company"), r.get("role"), r.get("status"), r.get("applied_on") or None,
                     r.get("market"), r.get("engine"), r.get("layout"), str(r.get("pages", "")),
                     r.get("score_before"), r.get("score_after"), r.get("score_ceiling"), job.get("discover_match"),
                     r.get("job_url"), r.get("job_id"), r.get("location"), r.get("posted"), r.get("domain"), r.get("notes"),
                     meta.get("seniority"), meta.get("domain")))
        jd = folder / "jd.txt"
        if jd.exists():
            text = jd.read_text("utf-8")
            descs.append((r["id"], text, len(text), hashlib.sha1(text.encode()).hexdigest()))
        kws += [(r["id"], k.get("term"), k.get("tier"), k.get("weight"), int(bool(k.get("covered"))),
                 int(bool(k.get("covered_before"))), int(bool(k.get("in_profile")))) for k in rep.get("keywords") or []]
    state = _json(output_dir() / "_jobs" / "jobs.json") or {}
    gone = set(state.get("dismissed") or [])
    seen = []
    for j in (state.get("jobs") or {}).values():
        ranked = j.get("_r") or {}
        reasons = ranked.get("reasons") or j.get("reasons") or []
        seen.append((j["id"], j.get("title"), j.get("company"), j.get("location"), j.get("url"), j.get("posted") or None,
                     j.get("first_seen"), j.get("source"), j.get("method"), None if j.get("remote") is None else int(bool(j["remote"])),
                     ranked.get("match", j.get("match")), int(bool(ranked.get("estimate", j.get("estimate")))),
                     int(bool(ranked.get("excluded", j.get("excluded")))), "; ".join(reasons)[:300], int(j["id"] in gone)))
    scans = [(s.get("at"), s.get("sources"), s.get("read"), s.get("new")) for s in state.get("scans") or []]
    with closing(connect()) as con, con:
        _seed(con)
        for table in ("applications", "job_descriptions", "keywords", "jobs_seen", "scans"):
            con.execute(f"DELETE FROM {table}")
        con.executemany(f"INSERT INTO applications VALUES ({','.join('?' * 22)})", apps)
        con.executemany("INSERT INTO job_descriptions VALUES (?, ?, ?, ?)", descs)
        con.executemany("INSERT INTO keywords VALUES (?, ?, ?, ?, ?, ?, ?)", kws)
        con.executemany(f"INSERT OR REPLACE INTO jobs_seen VALUES ({','.join('?' * 15)})", seen)
        con.executemany("INSERT INTO scans VALUES (?, ?, ?, ?)", scans)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('synced', ?)", (_now(),))
    return {"applications": len(apps), "job_descriptions": len(descs), "keywords": len(kws), "jobs_seen": len(seen), "scans": len(scans)}


_timers: dict[str, threading.Timer] = {}


def sync_soon(delay: float = 2.0) -> None:
    """Sync in the background a moment after a change (a new CV, a scan); changes in quick succession share one."""
    from .profiles import current
    pid = current()
    ctx = contextvars.copy_context()   # the sync belongs to the profile that changed

    def run():
        try:
            ctx.run(sync)
        except Exception:   # a copy that fails is redone by the next change; never break the change itself
            pass
    old = _timers.pop(pid, None)
    if old:
        old.cancel()
    t = _timers[pid] = threading.Timer(delay, run)
    t.daemon = True
    t.start()
