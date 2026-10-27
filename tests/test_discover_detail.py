"""A Discover card scored on a search snippet is scored again once the full posting is read."""
from app import discover, main
from app.profile import load_profile

SNIPPET = "Lead Software Engineer building our platform with Python. " * 6        # ~350 characters
FULL = ("Lead Software Engineer. You will build our platform with Python, FastAPI, React and TypeScript, "
        "and work with Java, Kubernetes, AWS, Terraform, Kafka and Spark. ") * 8


def stored_job():
    prefs = discover.with_profile(main._prefs(load_profile()))
    evidence = discover.strong_skills(load_profile())
    job = {"id": "j1", "title": "Lead Software Engineer", "company": "Acme", "location": "Bengaluru, India",
           "url": "https://jobs.example.com/acme/1", "posted": "", "remote": False, "description": SNIPPET,
           "detail_api": "https://api.example.com/jobs/1"}
    job = discover.rank(job, evidence, prefs, discover.rank_signature(prefs, evidence))
    discover._save_jobs({"jobs": {"j1": job}, "dismissed": []})
    return job


def test_snippet_scores_are_marked_as_estimates():
    assert stored_job()["estimate"]


def test_reading_the_full_posting_scores_it_again(monkeypatch):
    before = stored_job()
    monkeypatch.setattr(discover, "_json", lambda url: {"jobAd": {"sections": {"a": {"title": "", "text": FULL}}}})
    after = discover.job_detail("j1", main._prefs(load_profile()))
    assert not before["excluded"] and not after["excluded"]
    assert not after["estimate"] and after["match"] != before["match"]
    assert discover._load_jobs()["jobs"]["j1"]["match"] == after["match"]   # the card shows it on the next load


def test_without_settings_the_stored_rank_is_dropped_for_the_next_load(monkeypatch):
    stored_job()
    monkeypatch.setattr(discover, "_json", lambda url: {"jobAd": {"sections": {"a": {"title": "", "text": FULL}}}})
    discover.job_detail("j1")
    assert "_r" not in discover._load_jobs()["jobs"]["j1"]
