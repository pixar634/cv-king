"""The eligibility gate: a job that fails a hard screen stops before the writer runs, with the reason."""
import json

import pytest

from app import gate, tailor
from app.profile import load_profile

BODY = ("We build developer tools. You will design, build and ship product features in Python and React, own services "
        "end to end, review code and mentor engineers. ") * 3


def verdict(text, title="Lead Software Engineer", location="Bengaluru, India", market="india", remote=None, **prefs):
    return gate.check(BODY + text, title, location, market, remote, load_profile(), {"max_years_asked": 9} | prefs)


def test_a_plain_job_at_home_passes():
    assert verdict("5+ years of experience in software engineering.")["verdict"] == "ok"


def test_too_many_years_stops_before_writing():
    got = verdict("12+ years of experience in software engineering.")
    assert got["verdict"] == "stop" and "Above your level" in got["reasons"][0]


def test_uk_only_remote_stops_and_worldwide_passes():
    uk = verdict("This role is remote, open to candidates anywhere in the United Kingdom.", location="Remote", market="remote")
    assert uk["verdict"] == "stop" and "not open to where you live" in uk["reasons"][0]
    world = verdict("Fully remote, we hire worldwide through an employer of record.", location="Remote", market="remote")
    assert world["verdict"] == "ok"


def test_abroad_without_sponsorship_stops():
    got = verdict("Unfortunately we are unable to sponsor visas for this role.", location="Berlin, Germany", market="eu")
    assert got["verdict"] == "stop" and "sponsor" in got["reasons"][0]
    assert verdict("Visa sponsorship is available and relocation support.", location="Berlin, Germany", market="eu")["verdict"] == "ok"


def test_contract_and_parked_tracks_stop():
    assert verdict("This is a 6 month contract role, paid at an hourly rate.")["verdict"] == "stop"
    pm = verdict("Own the roadmap.", title="Senior Product Manager", tracks=["ic_lead"])
    assert pm["verdict"] == "stop" and "parked track" in pm["reasons"][0]


def test_immediate_joiner_posting_is_a_stretch_with_a_long_notice():
    p = load_profile()
    p["basics"]["availability"] = "30 days' notice"
    got = gate.check(BODY + "Immediate joiners only.", "Lead Software Engineer", "Bengaluru, India", "india", None, p,
                     {"max_years_asked": 9})
    assert got["verdict"] == "stretch"


def test_tailor_stops_before_the_writer_and_anyway_goes_through(monkeypatch):
    called = []
    monkeypatch.setattr(tailor, "get_engine", lambda *a: called.append(a) or (_ for _ in ()).throw(RuntimeError("writer ran")))
    req = {"jd": BODY + "12+ years of experience in software engineering.", "role": "Lead Software Engineer",
           "company": "Acme", "market": "india"}
    with pytest.raises(gate.NotEligible):
        tailor.run(req)
    assert not called
    with pytest.raises(RuntimeError, match="writer ran"):
        tailor.run(req | {"force": True})
    json.dumps(gate.check(req["jd"], req["role"]))   # the verdict is plain JSON for the UI
