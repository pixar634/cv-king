"""Another role at an employer you've applied to: the job card, the extension and Discover say so."""
import json

from fastapi.testclient import TestClient

from app import discover, tracker
from app.config import output_dir
from app.main import app

ROWS = [{"id": "a1", "company": "Okta, Inc.", "role": "Staff Software Engineer", "status": "Rejected",
         "applied_on": "2026-10-12", "created": "2026-10-12T09:00:00"},
        {"id": "a2", "company": "Okta", "role": "Senior Product Manager", "status": "Generated", "applied_on": "",
         "created": "2026-10-13T09:00:00"}]
DESC = "Build Python and React services end to end, FastAPI APIs, PostgreSQL. 5+ years of experience. " * 4


def _rows():
    (output_dir() / "applications.json").write_text(json.dumps(ROWS), "utf-8")


def test_lists_only_applications_that_went_out():
    _rows()
    got = tracker.at_company("Okta")
    assert [x["role"] for x in got] == ["Staff Software Engineer"] and "Rejected" in got[0]["story"]
    assert tracker.at_company("Initech") == []


def test_job_card_and_discover_say_so():
    _rows()
    fit = TestClient(app, base_url="http://127.0.0.1:8765").post(
        "/api/fit", json={"jd": DESC, "role": "Senior Software Engineer", "company": "Okta", "location": "Bengaluru, India"}).json()
    assert [x["role"] for x in fit["at_company"]] == ["Staff Software Engineer"]
    prefs = discover.with_profile({"regions": ["city", "country"], "candidate_years": 7, "include_senior": True})
    r = discover.rank({"title": "Senior Software Engineer", "company": "Okta", "location": "Bengaluru, India",
                       "description": DESC}, {"Python", "React"}, prefs)
    assert r["applied_here"] == "Staff Software Engineer (Rejected)"
