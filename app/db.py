"""A local SQLite record of the search, one file per profile (`<data dir>/jobmagnet.db`, never committed).

Two kinds of table:
- `status_events` is the record nothing else keeps: every status change, the moment it happens. Never rebuilt.
- The rest are copies of the JSON files (the source of truth), rebuilt whole by `sync()`, so they can be queried
  together: applications, their job descriptions and keyword results, every job Discover has seen, the profile with
  its memory and settings, sources, the career map, goal, salary and growth logs, form answers, cold mails, contacts
  and interview prep. Contact details of the candidate stay out.

Read it with any SQLite tool, or `python scripts/query.py "SELECT ..."` (read-only)."""
import contextvars
import datetime as dt
import hashlib
import json
import sqlite3
import threading
from contextlib import closing

from .config import data_dir, output_dir

SCHEMA_VERSION = 4
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
  seniority TEXT, job_domain TEXT,       -- what the writer read from the posting
  channel TEXT, posting_age INTEGER,     -- how you applied; the posting's age in days when you did
  outreach_on TEXT, replied_on TEXT      -- outreach sent, and a reply to it
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

-- The profile and everything around it (contact details stay out). Nested files go in documents as JSON:
-- query them with json_extract, e.g. json_extract(json, '$.goal') FROM documents WHERE name = 'career_map'.
CREATE TABLE IF NOT EXISTS profile_basics (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY, position INTEGER, title TEXT, company TEXT, client TEXT,
  start_month TEXT, end_month TEXT, location TEXT, stack TEXT
);
CREATE TABLE IF NOT EXISTS bullets (role_id TEXT, id TEXT, position INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS projects (name TEXT, role TEXT, start_month TEXT, end_month TEXT, stack TEXT, bullets TEXT);
CREATE TABLE IF NOT EXISTS education (degree TEXT, institution TEXT, year TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS certifications (name TEXT, issuer TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS skills (grp TEXT, skill TEXT, confirmed INTEGER);
CREATE TABLE IF NOT EXISTS memory (id TEXT PRIMARY KEY, role_id TEXT, text TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS memory_facts (entry_id TEXT, role_id TEXT, position INTEGER, fact TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);   -- value is JSON
CREATE TABLE IF NOT EXISTS tracks (id TEXT PRIMARY KEY, label TEXT, titles TEXT, lateral INTEGER);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT, kind TEXT, label TEXT, value TEXT, favourite INTEGER, method TEXT,
  last_scan TEXT, last_count INTEGER, last_error TEXT
);
CREATE TABLE IF NOT EXISTS career_paths (id TEXT, name TEXT, fit TEXT, reaches_goal TEXT, why TEXT);
CREATE TABLE IF NOT EXISTS career_goal (role TEXT, salary REAL, unit TEXT, year INTEGER);
CREATE TABLE IF NOT EXISTS salary_log (id TEXT, year INTEGER, amount REAL, unit TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS growth_log (id TEXT, kind TEXT, text TEXT, date TEXT, use_on_cv INTEGER);
CREATE TABLE IF NOT EXISTS form_answers (question TEXT, answer TEXT);      -- answer is JSON
CREATE TABLE IF NOT EXISTS cold_mails (
  id TEXT, app_id TEXT, created TEXT, to_name TEXT, to_email TEXT, to_role TEXT, tone TEXT,
  subject TEXT, body TEXT, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
  app_id TEXT, name TEXT, title TEXT, team TEXT, location TEXT, email TEXT, profile_url TEXT, confidence TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS interviews (app_id TEXT, generated_at TEXT, engine TEXT, needs_coding INTEGER, rounds INTEGER, questions INTEGER);
CREATE TABLE IF NOT EXISTS documents (name TEXT, app_id TEXT, json TEXT);        -- strategy, career_map
CREATE TABLE IF NOT EXISTS app_documents (name TEXT, app_id TEXT, json TEXT);    -- interview, codetest, contacts per application
"""
SOURCES = ("tailor", "app", "extension", "seed", "mail")


def path():
    return data_dir() / "jobmagnet.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(path(), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # The copied tables are rebuilt by sync(): one made by an older version is dropped and made again.
    if "channel" not in {r[1] for r in con.execute("PRAGMA table_info(applications)")}:
        con.execute("DROP TABLE applications")
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
                     meta.get("seniority"), meta.get("domain"), r.get("channel") or None, r.get("posting_age"),
                     (r.get("outreach") or {}).get("sent_on"), (r.get("outreach") or {}).get("replied_on")))
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
    tables = {"applications": apps, "job_descriptions": descs, "keywords": kws, "jobs_seen": seen, "scans": scans}
    tables |= _profile_tables() | _career_tables() | _per_application_tables([r["id"] for r in tracker.load()])
    with closing(connect()) as con, con:
        _seed(con)
        for table, rows in tables.items():
            con.execute(f"DELETE FROM {table}")
            if rows:
                verb = "INSERT OR REPLACE" if table == "jobs_seen" else "INSERT"
                con.executemany(f"{verb} INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('synced', ?)", (_now(),))
    return {table: len(rows) for table, rows in tables.items()}


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _profile_tables() -> dict[str, list[tuple]]:
    """The master profile: roles, bullets, skills, memory, and the settings saved with it. Contact details stay out."""
    p = _json(data_dir() / "base_cv.json") or {}
    if not p:
        return {}
    b = p.get("basics") or {}
    basics = [(k, str(v)) for k, v in {"name": b.get("name"), "headline": b.get("headline"), "location": b.get("location"),
                                       "availability": b.get("availability"), "summary": p.get("summary"),
                                       "interests": p.get("interests")}.items() if v]
    roles, bullets = [], []
    for i, r in enumerate(p.get("experience") or []):
        roles.append((r["id"], i, r.get("title"), r.get("company"), r.get("client"), r.get("start"), r.get("end"),
                      r.get("location"), ", ".join(r.get("stack") or [])))
        bullets += [(r["id"], x.get("id"), j, x.get("text")) for j, x in enumerate(r.get("bullets") or [])]
    confirmed = set(p.get("confirmed_skills") or [])
    skills = [(g, s, int(s in confirmed)) for g, items in (p.get("skills") or {}).items() for s in items]
    skills += [("Confirmed", s, 1) for s in sorted(confirmed)]
    memory = [(m["id"], m.get("role", ""), m.get("text"), m.get("created")) for m in p.get("memory") or []]
    facts = [(m["id"], m.get("role", ""), i, f) for m in p.get("memory") or [] for i, f in enumerate(m.get("facts") or [])]
    prefs = p.get("preferences") or {}
    settings = [(f"defaults.{k}", _dumps(v)) for k, v in (prefs.get("defaults") or {}).items()]
    settings += [(k, _dumps(v)) for k, v in prefs.items() if k != "defaults"]
    return {
        "profile_basics": basics, "roles": roles, "bullets": bullets, "skills": skills, "memory": memory,
        "memory_facts": facts, "settings": settings,
        "projects": [(x.get("name"), x.get("role"), x.get("start"), x.get("end"), ", ".join(x.get("stack") or []),
                      _dumps([y.get("text") for y in x.get("bullets") or []])) for x in p.get("projects") or []],
        "education": [(e.get("degree"), e.get("institution"), e.get("year"), _dumps(e.get("details") or [])) for e in p.get("education") or []],
        "certifications": [(c.get("name"), c.get("issuer"), c.get("detail")) for c in p.get("certifications") or []],
    }


def _career_tables() -> dict[str, list[tuple]]:
    """Strategy, sources, the career map, goal, salary and growth logs, and saved form answers."""
    d = data_dir()
    strategy = _json(d / "strategy.json") or {}
    cmap = _json(d / "career_map.json") or {}
    goal = _json(d / "career_goal.json") or {}
    docs = [(name, "", _dumps(v)) for name, v in (("strategy", strategy), ("career_map", cmap)) if v]
    return {
        "tracks": [(k, t.get("label"), _dumps(t.get("titles") or []), int(bool(t.get("lateral"))))
                   for k, t in (strategy.get("tracks") or {}).items()],
        "sources": [(s.get("id"), s.get("kind"), s.get("label"), s.get("value"), int(bool(s.get("favourite"))), s.get("method"),
                     s.get("last_scan"), s.get("last_count"), s.get("last_error")) for s in _json(d / "sources.json") or []],
        "career_paths": [(x.get("id"), x.get("name"), x.get("fit"), _dumps(x.get("reaches_goal")), x.get("why"))
                         for x in cmap.get("paths") or []],
        "career_goal": [(goal.get("role"), goal.get("salary"), goal.get("unit"), goal.get("year"))] if goal else [],
        "salary_log": [(s.get("id"), s.get("year"), s.get("amount"), s.get("unit"), s.get("note")) for s in _json(d / "salary.json") or []],
        "growth_log": [(g.get("id"), g.get("kind"), g.get("text"), g.get("date"), int(bool(g.get("use_on_cv"))))
                       for g in _json(d / "growth_log.json") or []],
        "form_answers": [(a.get("q"), _dumps(a.get("a"))) for a in _json(d / "answers.json") or []],
        "documents": docs,
    }


def _per_application_tables(app_ids: list[str]) -> dict[str, list[tuple]]:
    """Cold mails, contacts found and interview prep, per application; the full files go in `documents` as JSON."""
    mails, people, prep, docs = [], [], [], []
    folders = [(a, output_dir() / a) for a in app_ids] + [("", f) for f in sorted((output_dir() / "_coldmail").glob("*.json"))]
    for app_id, where in folders:
        cm = _json(where / "coldmail.json" if where.is_dir() else where) or {}
        mails += [(m.get("id"), app_id, m.get("created"), m.get("to_name"), m.get("to_email"), m.get("to_role"), m.get("tone"),
                   m.get("subject"), m.get("body"), m.get("sent_at") or None) for m in cm.get("mails") or []]
        if not where.is_dir():
            continue
        ct = _json(where / "contacts.json") or {}
        people += [(app_id, x.get("name"), x.get("title"), x.get("team"), x.get("location"), x.get("email"), x.get("profile_url"),
                    x.get("confidence"), x.get("source")) for x in ct.get("people") or []]
        iv = _json(where / "interview.json")
        if iv:
            rounds = iv.get("rounds") or []
            prep.append((app_id, iv.get("generated_at"), iv.get("engine"), int(bool(iv.get("needs_coding"))), len(rounds),
                         sum(len(r.get("questions") or []) for r in rounds)))
        for name in ("interview", "codetest", "contacts"):
            v = iv if name == "interview" else _json(where / f"{name}.json")
            if v:
                docs.append((name, app_id, _dumps(v)))
    return {"cold_mails": mails, "contacts": people, "interviews": prep, "app_documents": docs}


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
