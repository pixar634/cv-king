"""Jobs listed without a description have their full postings read in the background, so the checks that read the
text (languages, visa, level) run without anyone opening them."""
import datetime as dt
import time

import pytest

from app import discover

START_READING = discover._start_reading     # the real one, before the sandbox turns it off


@pytest.fixture(autouse=True)
def reading_on(monkeypatch):
    monkeypatch.setattr(discover, "_start_reading", START_READING)

PREFS = {"regions": ["city", "country"], "candidate_years": 7, "include_senior": True}
JAVA = ("Requirements: 5+ years of strong, hands-on Java and Spring Boot experience. You will build payment services "
        "on the JVM with Kafka and PostgreSQL. ") * 6
URL = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Bengaluru/Lead-Software-Engineer_R1"


def store(**extra):
    job = {"id": "w1", "title": "Lead Software Engineer", "company": "Acme", "location": "Bengaluru, India",
           "description": "", "posted": (dt.date.today() - dt.timedelta(days=2)).isoformat(), "first_seen": "",
           "url": URL, "method": "workday"} | extra
    discover._save_jobs({"jobs": {"w1": job}, "dismissed": []})


def settle():
    for _ in range(200):
        if not discover.rescore_progress()["running"]:
            return
        time.sleep(0.05)


def test_a_posting_read_in_the_background_drops_out_when_it_fails_a_check(monkeypatch):
    asked = []
    monkeypatch.setattr(discover, "_json", lambda url, timeout=15: asked.append(url) or
                        {"jobPostingInfo": {"jobDescription": f"<p>{JAVA}</p>"}})
    store()
    assert [j["id"] for j in discover.saved(14, PREFS)] == ["w1"]       # listed at once, unscored
    settle()
    assert asked == ["https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Bengaluru/Lead-Software-Engineer_R1"]
    assert "Java" in discover._load_jobs()["jobs"]["w1"]["description"]
    assert discover.saved(14, PREFS) == []                               # built on Java: hidden on the next load


def test_a_posting_that_wont_load_is_not_tried_again_at_once(monkeypatch):
    def down(url, timeout=15):
        raise OSError("site down")
    monkeypatch.setattr(discover, "_json", down)
    store()
    discover.saved(14, PREFS)
    settle()
    assert discover._load_jobs()["jobs"]["w1"]["_read_tried"].startswith(dt.date.today().isoformat())
    discover.saved(14, PREFS)
    assert not discover.rescore_progress()["running"]


def test_reading_a_posting_keeps_what_a_scan_saved_meanwhile(monkeypatch):
    def slow(url, timeout=15):
        state = discover._load_jobs()                                    # a scan stores a new job mid-read
        state["jobs"]["new"] = {"id": "new", "title": "Engineer", "posted": "", "url": "https://example.com/new"}
        discover._save_jobs(state)
        return {"jobPostingInfo": {"jobDescription": JAVA}}
    monkeypatch.setattr(discover, "_json", slow)
    store()
    discover.job_detail("w1")
    assert set(discover._load_jobs()["jobs"]) == {"w1", "new"}


def test_a_failed_read_is_tried_again_after_a_few_hours():
    now = dt.datetime.now()
    job = {"description": "", "_read_tried": (now - dt.timedelta(hours=1)).isoformat(timespec="seconds")}
    assert not discover.unread(job)
    assert discover.unread(job | {"_read_tried": (now - dt.timedelta(hours=7)).isoformat(timespec="seconds")})
