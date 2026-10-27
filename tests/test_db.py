"""The local record: every status change is kept with its time, and history survives a re-tailor."""
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
