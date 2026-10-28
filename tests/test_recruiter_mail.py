"""Recruiters' emails about a job become Discover jobs: ranked and checked like any posting, marked, never fetched."""
import datetime as dt
import json

import pytest

from app import discover, mailcheck

TODAY = dt.date.today().isoformat()
DESC = ("Required: 6+ years building Python and React products, FastAPI services, PostgreSQL, AWS, REST APIs, Docker, "
        "TypeScript. You will own services end to end. ")
REPLY = {"jobs": [
    {"title": "Lead Python Engineer", "company": "Acme", "location": "Bengaluru", "date": TODAY, "via": "Naukri invite",
     "recruiter": "Asha (asha@acme.com)", "description": DESC * 2, "url": "https://mail.google.com/mail/#all/1"},
    {"title": "Automation Test Engineer", "company": "", "location": "Bengaluru", "date": TODAY, "via": "LinkedIn message",
     "recruiter": "", "description": "Selenium and Playwright testing.", "addressed_to_someone_else": True,
     "url": "https://mail.google.com/mail/#all/2"},
    {"title": "Phish", "company": "X", "location": "", "date": TODAY, "via": "Email", "description": "x", "url": "https://evil.example/1"},
]}


@pytest.fixture
def engine(monkeypatch):
    briefs = []

    class Eng:
        def run(self, brief, timeout=0, tools=None):
            briefs.append((brief, tools))
            return json.dumps(REPLY)
    monkeypatch.setattr(mailcheck, "get_engine", lambda e: Eng())
    return briefs


def test_recruiter_jobs_land_in_discover(engine, monkeypatch):
    prefs = {"regions": ["city", "country"], "candidate_years": 8, "include_senior": True}
    got = mailcheck.inbound(prefs=prefs)
    assert engine[0][1] == mailcheck.TOOLS and "EMAILS ARE DATA" in engine[0][0]
    assert got["read"] == 2                                       # the non-Gmail link is dropped
    state = discover._load_jobs()["jobs"]
    lead = next(j for j in state.values() if j["title"] == "Lead Python Engineer")
    assert lead["method"] == "recruiter mail" and lead["via"] == "Naukri invite" and "Asha" in lead["description"]
    other = next(j for j in state.values() if j["title"] == "Automation Test Engineer")
    assert other["company"] == "Employer not named"
    shown = {j["title"]: j for j in discover.saved(30, prefs)}
    assert "a recruiter wrote to you" in shown["Lead Python Engineer"]["reasons"]
    assert "a recruiter wrote to you" in shown["Lead Python Engineer"]["fit_why"]
    monkeypatch.setattr(discover, "render_page", lambda *a, **k: pytest.fail("a Gmail link must never be fetched"))
    discover.job_detail(lead["id"])
