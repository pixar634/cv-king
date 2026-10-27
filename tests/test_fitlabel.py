"""One fit label from what predicts a callback; the keyword percentage shows only with enough skills named."""
import datetime as dt

from app import discover, fitlabel

TODAY = dt.date.today().isoformat()


def test_core_title_and_held():
    assert fitlabel.core("Senior Software Engineer, Payments") == "software engineer"
    assert fitlabel.held("Sr. Full Stack Developer", ["Full-Stack Engineer"])
    assert not fitlabel.held("Product Manager", ["Lead Software Engineer"])


def test_labels():
    base = dict(target=True, held_titles=["Software Engineer"], level="ok", at_level=True, must=(5, 6), terms=10,
                match=85, pay={"label": "unknown"}, open_state="open")
    strong = fitlabel.compute({"title": "Senior Software Engineer", "posted": TODAY, "method": "Greenhouse API"}, **base)
    assert strong["label"] == "Strong" and "a title you've held" in strong["why"]
    vp = fitlabel.compute({"title": "VP Engineering", "posted": "2026-01-01"}, **base | {
        "target": False, "level": "stretch", "at_level": False, "must": (1, 6)})
    assert vp["label"] == "Long shot"


def test_discover_card_carries_the_label_and_terms():
    prefs = discover.with_profile({"regions": ["city", "country"], "candidate_years": 7, "include_senior": True})
    desc = ("Requirements: Python, React, FastAPI, PostgreSQL, Docker, AWS, TypeScript, REST APIs, CI/CD. "
            "You will build product features end to end. ") * 3
    r = discover.rank({"title": "Lead Software Engineer", "company": "Acme", "location": "Bengaluru, India",
                       "description": desc, "posted": TODAY}, {"Python", "React"}, prefs)
    assert r["fit"] in ("Strong", "Possible") and r["terms"] >= 8 and r["fit_why"]
