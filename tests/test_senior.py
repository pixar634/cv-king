"""A services firm's Lead reads as a product company's Senior: Senior titles show, and the years ceiling is years + 2."""
from app import discover, strategy
from app.main import _default_prefs
from app.profile import experience_months, load_profile, save_profile

DESC = ("You will build product features in Python and React, own FastAPI services end to end, design APIs, "
        "review code and mentor engineers on a product team. ") * 4


def _employer(name):
    p = load_profile()
    p["experience"][0]["company"] = name
    save_profile(p)
    return p


def _rank(title, desc=DESC):
    prefs = discover.with_profile(_default_prefs(load_profile()) | {"candidate_years": 7})
    job = {"title": title, "company": "Product Co", "location": "Bengaluru, India", "description": desc}
    return discover.rank(job, {"Python", "React", "FastAPI"}, prefs)


def test_services_lead_sees_senior_titles_at_their_level():
    _employer("Tech Mahindra")
    assert strategy.calibrated_rank(load_profile()) == 3
    got = _rank("Senior Software Engineer")
    assert not got["excluded"] and "at your level" in got["reasons"]


def test_product_company_lead_still_hides_senior():
    _employer("Acme")
    assert _rank("Senior Software Engineer")["excluded"]


def test_years_ceiling_is_years_plus_two(sandbox):
    _employer("Tech Mahindra")
    years = experience_months(load_profile()) // 12
    assert _default_prefs(load_profile())["max_years_asked"] == years + 2
    over = f" {years + 3}+ years of experience in software engineering."
    assert _rank("Senior Software Engineer", DESC + over)["excluded"]
