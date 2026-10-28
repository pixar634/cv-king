"""The inbox check: read-only tools, answers checked by code, only metadata kept, and status changes only offered."""
import json

from app import engines, mailcheck, tracker
from app.config import output_dir

ROWS = [{"id": "a1", "company": "Acme", "role": "Lead Engineer", "status": "Applied", "applied_on": "2026-10-12", "created": "2026-10-12T09:00:00"},
        {"id": "a2", "company": "Initech", "role": "Staff Engineer", "status": "Rejected", "applied_on": "2026-10-12", "created": "2026-10-12T09:00:00"},
        {"id": "a3", "company": "Hooli", "role": "Engineer", "status": "Generated", "applied_on": "", "created": "2026-10-12T09:00:00"}]
G = "https://mail.google.com/mail/#all/1"
REPLY = {"matches": [
    {"app_id": "a1", "kind": "received", "date": "2026-10-12", "from": "no-reply@greenhouse-mail.io", "subject": "Thanks for applying", "url": G},
    {"app_id": "a1", "kind": "callback", "date": "2026-10-20", "from": "jane@acme.com", "subject": "Quick call?", "url": G},
    {"app_id": "a2", "kind": "rejected", "date": "2026-10-15", "from": "x@initech.com", "subject": "Update", "url": G},
    {"app_id": "a3", "kind": "received", "date": "2026-10-15", "from": "x", "subject": "never applied", "url": G},
    {"app_id": "zz", "kind": "received", "date": "2026-10-15", "from": "x", "subject": "unknown app", "url": G},
    {"app_id": "a1", "kind": "offer!!", "date": "2026-10-15", "from": "x", "subject": "bad kind", "url": G}],
    "untracked": [{"company": "LinkedIn", "role": "Senior Automation Engineer", "date": "2026-10-12", "from": "smartrecruiters", "subject": "Your application", "url": G}],
    "inbound": [{"company": "Palo Alto Networks", "role": "Senior Staff Engineer", "date": "2026-10-26", "from": "r@pan.com",
                 "subject": "Opportunity", "url": "https://evil.example/phish", "body": "secret text"}]}


def test_check_keeps_checked_metadata_and_offers_status(monkeypatch):
    (output_dir() / "applications.json").write_text(json.dumps(ROWS), "utf-8")
    for r in ROWS:
        (output_dir() / r["id"]).mkdir(parents=True, exist_ok=True)
    calls = []

    class Eng:
        def run(self, brief, timeout=0, tools=None):
            calls.append((brief, tools))
            return json.dumps(REPLY)
    monkeypatch.setattr(mailcheck, "get_engine", lambda e: Eng())
    got = mailcheck.check()
    brief, tools = calls[0]
    assert tools == mailcheck.TOOLS and "Hooli" not in brief and "<<<" in brief and "2026/10/12" in brief
    assert got["matched"] == 2 and got["untracked"][0]["company"] == "LinkedIn"
    assert got["inbound"][0]["url"] == "" and "body" not in got["inbound"][0]   # a non-Gmail link and any body dropped
    assert [m["kind"] for m in mailcheck.load("a1")["mails"]] == ["received", "callback"]
    v = mailcheck.verdict("a1", "Applied")
    assert v["kind"] == "callback" and v["suggest"] == "Recruiter call"
    assert mailcheck.verdict("a2", "Rejected")["suggest"] is None          # the tracker already says it
    assert mailcheck.verdict("a3", "Generated") is None                    # not asked about, nothing kept
    mailcheck.check()                                                      # a second check adds no duplicates
    assert len(mailcheck.load("a1")["mails"]) == 2
    assert tracker.load()[0]["status"] == "Applied"                        # never changed by the check itself


def test_the_run_can_read_but_never_send_or_draft():
    assert "mcp__claude_ai_Gmail__create_draft" in engines.GMAIL_BLOCKED
    assert {"mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__search_threads"} <= set(engines.GMAIL_BLOCKED)
    assert not set(mailcheck.TOOLS) & {"mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__create_draft"}
