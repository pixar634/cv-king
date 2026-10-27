"""Insights: the funnel, replies by match score, and who hasn't replied, from the local database."""
import datetime as dt

from app import insights, tracker

TODAY = dt.date(2026, 11, 20)


def add(app_id, ceiling, history):
    """history: (status, day) pairs after the CV was made on 2026-10-01."""
    tracker._save(tracker.load() + [{"id": app_id, "created": "2026-10-01T10:00:00", "company": f"Co {app_id}", "role": "Lead Engineer",
                                     "status": "Generated", "applied_on": "", "domain": "", "logo": False, "score_ceiling": ceiling}])
    from app import db
    db.ensure_seeded()
    was = "Generated"
    for status, day in history:
        db.record_status(app_id, f"Co {app_id}", "Lead Engineer", was, status, "app", at=f"{day}T09:00:00")
        rows = tracker.load()
        next(r for r in rows if r["id"] == app_id)["status"] = was = status
        tracker._save(rows)


def test_funnel_and_reply_rate():
    add("a", 82, [("Applied", "2026-10-02"), ("Recruiter call", "2026-10-09"), ("Interviewing", "2026-10-15")])
    add("b", 81, [("Applied", "2026-10-02"), ("Rejected", "2026-10-06")])
    add("c", 55, [("Applied", "2026-10-03")])
    add("d", 45, [])
    d = insights.compute(TODAY)
    assert d["funnel"] == {"tailored": 4, "applied": 3, "replied": 2, "interviews": 1, "offers": 0}
    assert d["reply_rate"] is None                     # 3 applied: too few to give a rate
    assert d["days_to_reply"] == {"median": 5.5, "n": 2}   # 7 days and 4 days


def test_rates_by_band_show_from_five_applications():
    for i in range(5):
        add(f"h{i}", 75, [("Applied", "2026-10-02")] + ([("Recruiter call", "2026-10-08")] if i < 2 else []))
    band = next(b for b in insights.compute(TODAY)["bands"] if b["band"] == "70 to 79%")
    assert (band["applied"], band["replied"], band["reply_rate"]) == (5, 2, 40)


def test_applications_waiting_for_a_reply():
    add("old", 70, [("Applied", "2026-10-01")])
    add("new", 70, [("Applied", "2026-11-15")])
    add("gone", 70, [("Applied", "2026-10-01"), ("Withdrawn", "2026-10-05")])
    d = insights.compute(TODAY)
    assert [w["id"] for w in d["waiting"]] == ["old"] and d["waiting"][0]["days"] == 50


def test_missing_skills_are_counted_across_jobs():
    import json

    from app.config import output_dir
    for app_id, terms in {"a": ["Kubernetes", "Go"], "b": ["Kubernetes"]}.items():
        add(app_id, 60, [])
        folder = output_dir() / app_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps({"keywords": [
            {"term": t, "tier": "required", "weight": 3, "covered": False, "covered_before": False, "in_profile": False} for t in terms]
            + [{"term": "Python", "tier": "required", "weight": 3, "covered": True, "covered_before": True, "in_profile": True}]}), "utf-8")
    assert insights.compute(TODAY)["missing"] == [{"term": "Kubernetes", "jobs": 2}, {"term": "Go", "jobs": 1}]


def test_a_reply_from_before_the_history_has_no_date():
    tracker._save([{"id": "s", "created": "2026-10-01T10:00:00", "company": "Co", "role": "Lead Engineer", "status": "Rejected",
                    "applied_on": "2026-10-02", "domain": "", "logo": False, "score_ceiling": 70}])
    d = insights.compute(TODAY)
    assert d["funnel"]["replied"] == 1 and d["days_to_reply"] == {"median": None, "n": 0} and d["approximate"]


def test_a_skill_the_profile_has_now_is_not_listed_missing():
    import json

    from app.config import output_dir
    add("a", 60, [])
    folder = output_dir() / "a"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps({"keywords": [   # React was missing then; the test profile has it now
        {"term": t, "tier": "required", "weight": 3, "covered": False, "covered_before": False, "in_profile": False}
        for t in ("React", "Kubernetes")]}), "utf-8")
    assert [m["term"] for m in insights.compute(TODAY)["missing"]] == ["Kubernetes"]
