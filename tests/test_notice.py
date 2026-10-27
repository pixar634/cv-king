"""Availability stays true: the notice period everywhere, a sooner start only when the profile says it can happen, and
never "immediately available" while the current role is ongoing."""
import json

from app import apply, tailor
from app.config import output_dir
from app.profile import availability_for, load_profile, save_profile, wants_immediate

RUSH = "We are hiring a Lead Engineer. Immediate joiners preferred; notice period 0-15 days."
CALM = "We are hiring a Lead Engineer to build our platform."


def _set(**basics):
    p = load_profile()
    p["basics"] |= basics
    save_profile(p)
    return p


def test_only_rushed_postings_get_the_release_line_and_only_when_true():
    assert wants_immediate(RUSH) and not wants_immediate(CALM)
    p = _set(availability="30 days' notice")
    assert availability_for(p, RUSH) == "30 days' notice"            # not known: the plain notice period
    p = _set(notice_release="buyout")
    assert availability_for(p, RUSH) == "30 days' notice, can join sooner with a notice buyout"
    assert availability_for(p, CALM) == "30 days' notice"
    assert tailor.default_header_line("india", p, RUSH).startswith("30 days' notice, can join sooner")


def test_immediately_available_is_an_error_while_employed():
    p = _set(availability="Immediately Available")
    flags = tailor.availability_flags(p, "Test Person · Immediately Available · Bengaluru", "I can join immediately.")
    assert [f["kind"] for f in flags] == ["availability", "availability"]
    p["experience"][0]["end"] = "2026-09"
    assert tailor.availability_flags(p, "Immediately Available") == []   # between jobs, it can be true


def test_form_notice_answer_carries_the_release_line(sandbox):
    _set(availability="30 days' notice", notice_release="early")
    folder = output_dir() / "2026-10-28_Acme_Lead"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"company": "Acme", "role": "Lead", "market": "india"}), "utf-8")
    (folder / "jd.txt").write_text(RUSH, "utf-8")
    fields = [{"id": "0:n", "kind": "text", "label": "Notice period"},
              {"id": "0:s", "kind": "select", "label": "Notice period (days)", "options": ["0-15", "30", "60"]}]
    got = apply.answer(folder.name, fields)["answers"]
    assert got["0:n"] == "30 days (early release negotiable)"
