"""Messages that fit each person (hiring manager, referral, recruiter, introduction, follow-up) and a per-person
follow-up clock."""
import datetime as dt
import json

import pytest

from app import warm
from app.config import output_dir

CSV = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Mia,M,https://www.linkedin.com/in/mia,,Okta,Engineering Manager - Platform,01 Jan 2024
Rec,R,https://www.linkedin.com/in/rec,,Okta,Technical Recruiter,01 Jan 2024
"""


@pytest.fixture
def app_id(monkeypatch):
    warm.import_csv(CSV)
    folder = output_dir() / "2026-10-28_Okta_Platform"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"company": "Okta", "role": "Senior Software Engineer, Platform",
                                                     "options": {"header_line": ""}, "experience_years": "8",
                                                     "jd_meta": {}}), "utf-8")
    (folder / "jd.txt").write_text("We need Python.", "utf-8")
    (folder / "plan.json").write_text("{}", "utf-8")
    briefs = []

    class Eng:
        def run(self, brief, timeout=0, tools=None):
            briefs.append(brief)
            return json.dumps({"message": "Hi, about the platform role."})
    monkeypatch.setattr(warm, "get_engine", lambda e: Eng())
    return folder.name, briefs


def test_the_message_fits_the_person(app_id):
    aid, briefs = app_id
    assert warm.referral(aid, "https://www.linkedin.com/in/mia")["kind"] == "hiring_manager"
    assert "if it's on your team" in briefs[-1]
    assert warm.referral(aid, "https://www.linkedin.com/in/rec")["kind"] == "recruiter"
    assert warm.referral(aid, "https://www.linkedin.com/in/rec", kind="referral")["label"] == "Referral ask"


def test_follow_up_clock_per_person(app_id):
    aid, briefs = app_id
    with pytest.raises(ValueError):
        warm.referral(aid, "https://www.linkedin.com/in/mia", kind="follow_up")   # nothing to follow up on yet
    with pytest.raises(ValueError):
        warm.mark(aid, "https://www.linkedin.com/in/mia", "sent")                 # nothing written yet
    warm.referral(aid, "https://www.linkedin.com/in/mia")
    assert warm.people_state(aid)["https://www.linkedin.com/in/mia"]["state"] == "none"
    warm.mark(aid, "https://www.linkedin.com/in/mia", "sent")
    st = warm.people_state(aid)["https://www.linkedin.com/in/mia"]
    assert st["state"] == "sent" and st["due"] == (dt.date.today() + dt.timedelta(days=5)).isoformat()
    warm.referral(aid, "https://www.linkedin.com/in/mia", kind="follow_up")
    assert "THE EARLIER MESSAGE" in briefs[-1]
    assert warm.people_state(aid)["https://www.linkedin.com/in/mia"]["state"] == "sent"   # the clock stays on the first
    warm.mark(aid, "https://www.linkedin.com/in/mia", "replied")
    assert warm.people_state(aid)["https://www.linkedin.com/in/mia"]["state"] == "replied"
    warm.mark(aid, "https://www.linkedin.com/in/mia", "undo")
    assert warm.people_state(aid)["https://www.linkedin.com/in/mia"]["state"] == "sent"
