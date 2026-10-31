"""The review reads the jobs that pass your filters against your CV and says apply, stretch or skip; a verdict is
kept until the posting or the CV changes, yours always stands, and a usage limit stops the run at once."""
import datetime as dt
import json

import pytest

from app import discover, review
from app.engines import EngineError

TODAY = dt.date.today().isoformat()
DESC = ("Requirements: Python, React, FastAPI, PostgreSQL, Docker, AWS, TypeScript, REST APIs, CI/CD. "
        "You will build product features end to end. ") * 3
PREFS = {"days": 5, "fit_filter": "all", "min_match": 0, "regions": ["city"], "candidate_years": 8, "include_senior": True}


class FakeEngine:
    def __init__(self, replies):
        self.replies, self.briefs = list(replies), []

    def available(self):
        return True

    def run(self, brief, timeout=0, tools=None):
        self.briefs.append(brief)
        reply = self.replies.pop(0) if self.replies else {"verdict": "apply", "why": "Meets every must-have"}
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)


def jobs(*titles, desc=DESC):
    state = {"jobs": {}, "dismissed": []}
    for i, t in enumerate(titles):
        state["jobs"][f"j{i}"] = {"id": f"j{i}", "title": t, "company": f"Co{i}", "location": "Bengaluru, India",
                                  "url": f"https://example.com/{i}", "posted": TODAY, "description": desc, "source": "test",
                                  "first_seen": TODAY + "T08:00:00"}
    discover._save_jobs(state)


@pytest.fixture
def engine(monkeypatch):
    def use(*replies):
        fake = FakeEngine(replies)
        monkeypatch.setattr(review, "get_engine", lambda *_: fake)
        return fake
    return use


def verdicts():
    return {k: (v.get("review") or {}).get("verdict") for k, v in discover._load_jobs()["jobs"].items()}


def test_each_listed_job_gets_a_verdict_and_the_brief_carries_cv_and_posting(engine):
    jobs("Lead Software Engineer", "Senior Software Engineer")
    fake = engine({"verdict": "skip", "why": "Asks for 10+ years"}, {"verdict": "stretch", "why": "One gap"})
    out = review.run(PREFS)
    assert out["reviewed"] == 2 and sorted(v for v in verdicts().values()) == ["skip", "stretch"]
    assert all("CANDIDATE" in b and "POSTING:" in b and "FastAPI" in b for b in fake.briefs)
    assert "Test Person" in fake.briefs[0] or "Lead engineer who builds" in fake.briefs[0]   # the sandbox CV


def test_a_kept_verdict_is_not_read_again_until_the_posting_changes(engine):
    jobs("Lead Software Engineer")
    engine({"verdict": "skip", "why": "x"})
    review.run(PREFS)
    again = engine()
    assert review.run(PREFS)["of"] == 0 and not again.briefs
    jobs("Lead Software Engineer", desc=DESC + " Now also Kubernetes.")
    state = discover._load_jobs()
    state["jobs"]["j0"]["review"] = {"verdict": "skip", "why": "x", "sig": "old", "by": "model"}
    discover._save_jobs(state)
    assert review.run(PREFS)["of"] == 1


def test_your_call_stands(engine):
    jobs("Lead Software Engineer")
    review.overrule("j0", "apply")
    fake = engine({"verdict": "skip", "why": "x"})
    assert review.run(PREFS)["of"] == 0 and not fake.briefs and verdicts()["j0"] == "apply"
    with pytest.raises(ValueError):
        review.overrule("j0", "maybe")


def test_a_usage_limit_stops_the_run(engine, monkeypatch):
    monkeypatch.setattr(review, "WORKERS", 1)
    jobs("Lead Software Engineer", "Senior Software Engineer", "Tech Lead")
    fake = engine(EngineError("Claude CLI error: You've hit your 5-hour limit · resets 3:20pm"))
    out = review.run(PREFS)
    assert out["reviewed"] == 0 and "usage limit" in out["paused"] and len(fake.briefs) == 1


def test_a_reply_without_a_verdict_is_an_error(engine):
    engine({"why": "no verdict here"})
    with pytest.raises(EngineError):
        review.judge({"title": "Lead Software Engineer", "description": DESC},
                     {"cv": "cv", "level_note": "", "history": "", "years": 8})


def test_only_jobs_the_list_shows_are_read():
    assert review.wanted({"fit": "Strong", "match": 90}, {"fit_filter": "strong", "min_match": 70})
    assert not review.wanted({"fit": "Possible", "match": 90}, {"fit_filter": "strong", "min_match": 70})
    assert not review.wanted({"fit": "Strong", "match": 60}, {"fit_filter": "strong", "min_match": 70})
    assert not review.wanted({"fit": "Strong", "match": 90, "unscored": True}, {"fit_filter": "all"})


def test_a_rescan_keeps_the_verdict(engine):
    jobs("Lead Software Engineer")
    engine({"verdict": "skip", "why": "x"})
    review.run(PREFS)
    prev = discover._load_jobs()["jobs"]["j0"]
    seen_again = {k: v for k, v in prev.items() if k not in ("review", "first_seen")}
    got = discover.carry_over(seen_again, prev, "2099-01-01T00:00:00")
    assert got["review"]["verdict"] == "skip" and got["first_seen"] == TODAY + "T08:00:00"
    assert discover.carry_over({"id": "new"}, None, "2099-01-01T00:00:00") == {"id": "new", "first_seen": "2099-01-01T00:00:00"}
