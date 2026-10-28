"""Warm paths from your own LinkedIn export: matched to employers, counted by Discover, and a checked referral ask."""
import json

import pytest

from app import discover, warm
from app.config import output_dir

CSV = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Asha,Iyer,https://www.linkedin.com/in/asha,,"Okta, Inc.",Senior Engineer,01 Jan 2024
Ravi,K,https://www.linkedin.com/in/ravi,,Acme Labs,Engineering Manager,02 Feb 2025
No,Company,https://www.linkedin.com/in/nobody,,,,03 Mar 2025
"""


def test_import_and_match():
    assert warm.import_csv(CSV) == {"people": 2, "companies": 2, "imported": warm.summary()["imported"]}
    assert [p["name"] for p in warm.at("Okta")] == ["Asha Iyer"]
    assert warm.at("Acme") == warm.at("Acme Labs")   # "Labs" is a suffix that doesn't tell employers apart
    with pytest.raises(ValueError):
        warm.import_csv("name,company\nx,y")


def test_discover_counts_a_warm_path():
    warm.import_csv(CSV)
    prefs = discover.with_profile({"regions": ["city", "country"], "candidate_years": 7, "include_senior": True})
    job = lambda company: {"title": "Lead Software Engineer", "company": company, "location": "Bengaluru, India",
                           "description": "Build product features in Python and React end to end. " * 8}
    known, cold = discover.rank(job("Okta"), {"Python"}, prefs), discover.rank(job("Initech"), {"Python"}, prefs)
    assert known["warm"] and "you know someone there" in known["fit_why"] and known["order"] > cold["order"]


def test_referral_ask_is_checked_and_saved(monkeypatch):
    warm.import_csv(CSV)
    folder = output_dir() / "2026-10-28_Okta_Lead"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"company": "Okta", "role": "Lead Engineer", "options": {"header_line": ""},
                                                     "experience_years": "8", "jd_meta": {}}), "utf-8")
    (folder / "jd.txt").write_text("We need Python and React.", "utf-8")
    (folder / "plan.json").write_text("{}", "utf-8")

    class Eng:
        def run(self, brief, timeout=0, tools=None):
            assert "Asha Iyer" in brief and "<<<" in brief
            return json.dumps({"message": "Hi Asha, I cut costs by 93% at Acme. Would you refer me for the Lead Engineer role?"})
    monkeypatch.setattr(warm, "get_engine", lambda e: Eng())
    ask = warm.referral(folder.name, "https://www.linkedin.com/in/asha")
    assert any(f["kind"] == "numbers" for f in ask["flags"])   # 93% isn't in the profile
    assert json.loads((folder / "referrals.json").read_text("utf-8"))[0]["id"] == ask["id"]
    with pytest.raises(ValueError):
        warm.referral(folder.name, "https://www.linkedin.com/in/stranger")
