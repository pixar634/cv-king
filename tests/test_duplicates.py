"""The same job twice: found in the tracker by link, posting id or employer and title, and stopped before writing."""
import json

import pytest

from app import tailor, tracker
from app.config import output_dir

APPLIED = {"id": "2026-10-12_Fivetran_Senior-Program-Manager", "created": "2026-10-12T10:00:00", "company": "Fivetran",
           "role": "Senior Program Manager", "status": "Rejected", "applied_on": "2026-10-12",
           "job_url": "https://www.fivetran.com/careers/job?gh_jid=123", "job_id": "123"}
WRITTEN = {"id": "2026-10-20_Okta_Staff", "created": "2026-10-20T10:00:00", "company": "Okta, Inc.",
           "role": "Staff Software Engineer", "status": "Generated", "applied_on": "", "job_url": "", "job_id": ""}


@pytest.fixture
def rows():
    (output_dir() / "applications.json").write_text(json.dumps([APPLIED, WRITTEN]), "utf-8")


def test_matches_by_title_link_and_id(rows):
    assert tracker.find_same("Fivetran Inc", "Sr. Program Manager")["id"] == APPLIED["id"]
    assert tracker.find_same("", "", "https://fivetran.com/careers/job?gh_jid=123&utm_source=x")["id"] == APPLIED["id"]
    assert tracker.find_same("", "", "https://fivetran.com/careers/job?gh_jid=999") is None   # another job, same page
    assert tracker.find_same("Fivetran", "Analytics Engineer", job_id="123")["id"] == APPLIED["id"]
    assert tracker.find_same("Okta", "Staff Software Engineer")["id"] == WRITTEN["id"]
    assert tracker.find_same("Fivetran", "Staff Software Engineer") is None


def test_story_says_what_happened(rows):
    assert tracker.story(APPLIED).startswith("Written 12 Oct, Applied 12 Oct, Rejected")


def test_a_sent_application_stops_even_with_anyway(rows, monkeypatch):
    monkeypatch.setattr(tailor, "get_engine", lambda *a: (_ for _ in ()).throw(RuntimeError("writer ran")))
    req = {"jd": "We are hiring a program manager to run programs across teams. " * 10, "company": "Fivetran",
           "role": "Senior Program Manager", "market": "india"}
    for force in (False, True):
        with pytest.raises(tailor.AlreadyApplied, match="Rejected"):
            tailor.run(req | {"force": force})


def test_a_cv_only_written_can_be_written_again_with_anyway(rows, monkeypatch):
    monkeypatch.setattr(tailor, "get_engine", lambda *a: (_ for _ in ()).throw(RuntimeError("writer ran")))
    req = {"jd": "We are hiring a staff engineer to build Python and React products. " * 10, "company": "Okta",
           "role": "Staff Software Engineer", "market": "india"}
    with pytest.raises(tailor.AlreadyApplied):
        tailor.run(req)
    with pytest.raises(RuntimeError, match="writer ran"):
        tailor.run(req | {"force": True})
