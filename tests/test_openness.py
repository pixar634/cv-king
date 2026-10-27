"""Is a job open to someone at home: full-time, a normal working day, remote that hires here, abroad with a visa."""
import pytest

from app import discover
from app.gate import openness

D = "Build product features in Python and React. "


@pytest.mark.parametrize("text, location, remote, state", [
    (D, "Bengaluru, India", False, "open"),
    (D + "We hire worldwide through an employer of record.", "Remote", True, "open"),
    (D + "Open to candidates in APAC.", "Remote", True, "open"),
    (D, "Remote", True, "unclear"),
    (D + "You must be based in the United States.", "Remote", True, "closed"),
    (D, "Remote - US", True, "closed"),
    (D + "Overlap with US Pacific time zones is required.", "Remote", True, "closed"),
    (D + "Overlap with US Pacific hours; we work async otherwise.", "Remote", True, "unclear"),
    (D + "This is a 6 month contract role.", "Remote", True, "closed"),
    (D + "We offer visa sponsorship is available and relocation support.", "Berlin, Germany", False, "open"),
    (D + "We are unable to sponsor visas.", "Berlin, Germany", False, "closed"),
    (D, "Berlin, Germany", False, "unclear"),
    (D, "Remote, Germany", True, "closed"),
])
def test_openness(text, location, remote, state):
    assert openness(text, "Senior Software Engineer", location, remote)["state"] == state


def test_discover_hides_closed_and_labels_unclear():
    prefs = discover.with_profile({"regions": ["city", "country", "remote", "world"], "candidate_years": 7,
                                   "include_senior": True})
    job = lambda loc, text: {"title": "Software Engineer", "company": "Acme", "location": loc, "remote": True,
                             "description": (D * 8) + text}
    closed = discover.rank(job("Remote", "Candidates must be located in the US."), {"Python", "React"}, prefs)
    assert closed["excluded"] and closed["open"] == "closed"
    unclear = discover.rank(job("Remote", ""), {"Python", "React"}, prefs)
    assert not unclear["excluded"] and unclear["open"] == "unclear"


def test_himalayas_asks_for_the_home_country():
    assert discover._himalayas_country() == "&country=IN"
