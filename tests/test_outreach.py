"""Outreach on each application: sent, follow-up due on day 5, overdue after day 7, followed up, replied."""
import datetime as dt
import json

from app import tracker
from app.config import output_dir

ROW = {"id": "2026-10-20_Acme_Lead", "company": "Acme", "role": "Lead", "status": "Applied", "created": "2026-10-20T10:00:00"}


def _rows(**outreach):
    (output_dir() / ROW["id"]).mkdir(parents=True, exist_ok=True)
    (output_dir() / "applications.json").write_text(json.dumps([ROW | ({"outreach": outreach} if outreach else {})]), "utf-8")
    return tracker.load()[0]


def test_follow_up_due_dates():
    day = dt.date(2026, 10, 20)
    row = _rows(sent_on="2026-10-20")
    assert tracker.outreach_state(row, day + dt.timedelta(days=4))["state"] == "sent"
    assert tracker.outreach_state(row, day + dt.timedelta(days=5)) == {"state": "followup_due", "on": "2026-10-20", "due": "2026-10-25"}
    assert tracker.outreach_state(row, day + dt.timedelta(days=8))["state"] == "overdue"
    assert tracker.outreach_state(_rows(sent_on="2026-10-20", followed_up_on="2026-10-26"))["state"] == "followed_up"
    assert tracker.outreach_state(_rows())["state"] == "none"


def test_steps_and_undo():
    _rows()
    assert tracker.outreach(ROW["id"], "sent")["outreach"]["sent_on"] == dt.date.today().isoformat()
    tracker.outreach(ROW["id"], "replied")
    assert tracker.outreach_state(tracker.load()[0])["state"] == "replied"
    tracker.outreach(ROW["id"], "undo")
    assert tracker.outreach_state(tracker.load()[0])["state"] == "sent"


def test_a_sent_cold_email_counts_as_outreach():
    row = _rows()
    (output_dir() / ROW["id"] / "coldmail.json").write_text(json.dumps({"mails": [{"id": "m", "sent_at": "2026-10-21"}]}), "utf-8")
    assert tracker.outreach_state(row, dt.date(2026, 10, 26))["state"] == "followup_due"
