"""The local record: every status change is kept with its time, and history survives a re-tailor."""
import pytest

from app import db, tracker


def add_app(app_id, status="Generated", applied_on=""):
    rows = tracker.load() + [{"id": app_id, "created": "2026-10-01T10:00:00", "company": "Acme", "role": "Lead Engineer",
                              "status": status, "applied_on": applied_on, "domain": "", "logo": False}]
    tracker._save(rows)


def report(app_id):
    return {"id": app_id, "created": "2026-10-20T09:00:00", "company": "Acme", "role": "Lead Engineer",
            "market_label": "India", "options": {"variant": "flowcv", "max_pages": 2}, "engine": "claude:opus",
            "scores": {"before": 40, "after": 70, "ceiling": 75}, "pdf": "cv.pdf", "jd_meta": {}}


def test_existing_applications_are_seeded_once():
    add_app("a1")
    add_app("a2", status="Applied", applied_on="2026-10-05")
    h = db.history("a2")
    assert [(e["from_status"], e["to_status"], e["source"]) for e in h] == [(None, "Generated", "seed"), ("Generated", "Applied", "seed")]
    assert h[1]["at"].startswith("2026-10-05") and "approximate" in h[1]["note"]
    assert len(db.history("a1")) == 1
    db.ensure_seeded()
    assert len(db.history("a2")) == 2   # seeding never runs twice


def test_status_changes_are_recorded_with_their_source():
    add_app("a1")
    tracker.update("a1", {"status": "Applied"}, "extension")
    tracker.update("a1", {"notes": "spoke to the recruiter"})       # not a status change: nothing recorded
    tracker.update("a1", {"status": "Interviewing"})
    h = db.history("a1")
    assert [(e["from_status"], e["to_status"], e["source"]) for e in h] == [
        (None, "Generated", "seed"), ("Generated", "Applied", "extension"), ("Applied", "Interviewing", "app")]


def test_a_new_cv_starts_its_history(monkeypatch):
    monkeypatch.setattr(tracker, "fetch_logo", lambda *a: False)
    tracker.add_from_report(report("n1"), {})
    assert [(e["to_status"], e["source"]) for e in db.history("n1")] == [("Generated", "tailor")]


def test_a_re_tailor_carries_the_history_over(monkeypatch):
    monkeypatch.setattr(tracker, "fetch_logo", lambda *a: False)
    add_app("old")
    tracker.update("old", {"status": "Applied"})
    tracker.add_from_report(report("new"), {"replace_id": "old"})
    h = db.history("new")
    assert [e["to_status"] for e in h] == ["Generated", "Applied", "Applied"] and "replacing old" in h[-1]["note"]
    assert db.history("old") == []


def test_the_database_lives_in_the_profile_folder(sandbox):
    add_app("a1")
    db.history("a1")
    assert db.path().exists() and sandbox in db.path().parents


# ---------- the copied tables ----------

def write_app(app_id, keywords=(("Python", True), ("Kubernetes", False))):
    import json

    from app.config import output_dir
    add_app(app_id)
    folder = output_dir() / app_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "jd.txt").write_text("We need Python and Kubernetes.", "utf-8")
    (folder / "report.json").write_text(json.dumps({
        "jd_meta": {"seniority": "Lead", "domain": "Fintech"}, "job": {"discover_match": 64},
        "keywords": [{"term": t, "tier": "required", "weight": 3, "covered": c, "covered_before": False, "in_profile": c}
                     for t, c in keywords]}), "utf-8")


def store_jobs():
    import json

    from app.config import output_dir
    f = output_dir() / "_jobs" / "jobs.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"jobs": {
        "j1": {"id": "j1", "title": "Lead Engineer", "company": "Acme", "location": "Amsterdam", "url": "https://x/1",
               "posted": "2026-10-20", "first_seen": "2026-10-21T09:00:00", "source": "Jobicy", "method": "jobicy",
               "remote": True, "_r": {"match": 71, "estimate": False, "excluded": False, "reasons": ["target role"]}},
        "j2": {"id": "j2", "title": "Sales Lead", "company": "Beta", "location": "Berlin", "url": "https://x/2",
               "posted": "", "first_seen": "2026-10-21T09:00:00", "source": "Arbeitnow", "method": "arbeitnow",
               "remote": None, "match": 0, "excluded": True, "reasons": ["not a target role"]}},
        "dismissed": ["j1"], "scans": [{"at": "2026-10-21T09:00:00", "sources": 4, "read": 900, "new": 12}]}), "utf-8")


def rows(sql):
    from contextlib import closing
    with closing(db.connect()) as con:
        return [tuple(r) for r in con.execute(sql)]


def test_sync_copies_applications_descriptions_keywords_and_jobs():
    write_app("a1")
    store_jobs()
    assert db.sync() == {"applications": 1, "job_descriptions": 1, "keywords": 2, "jobs_seen": 2, "scans": 1}
    assert rows("SELECT company, discover_match, seniority FROM applications") == [("Acme", 64, "Lead")]
    assert rows("SELECT term FROM keywords WHERE covered = 0") == [("Kubernetes",)]
    assert rows("SELECT id, match, excluded, dismissed FROM jobs_seen ORDER BY id") == [("j1", 71, 0, 1), ("j2", 0, 1, 0)]


def test_sync_rebuilds_the_copies_but_never_the_history():
    write_app("a1")
    write_app("a2")
    db.sync()
    tracker.update("a1", {"status": "Applied"})
    tracker._save([r for r in tracker.load() if r["id"] != "a2"])   # a2 deleted from the tracker
    db.sync()
    assert rows("SELECT id, status FROM applications") == [("a1", "Applied")]
    assert ("Generated", "Applied") in rows("SELECT from_status, to_status FROM status_events WHERE app_id = 'a1'")
    assert rows("SELECT COUNT(*) FROM status_events WHERE app_id = 'a2'") == [(1,)]   # its seeded history stays


def test_the_query_script_is_read_only(monkeypatch, capsys):
    import sqlite3
    import sys
    sys.path.insert(0, "scripts")
    import query
    write_app("a1")
    db.sync()
    monkeypatch.setattr(sys, "argv", ["query.py", "SELECT company FROM applications"])
    assert query.main() == 0 and "Acme" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["query.py", "DELETE FROM applications"])
    with pytest.raises(sqlite3.OperationalError):
        query.main()
