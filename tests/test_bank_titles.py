"""Bank titles: a Vice President engineer is a lead, not an executive; elsewhere a VP is still a VP."""
from app import discover, gate, strategy


def test_bank_ladder():
    assert strategy.title_level("Vice President, Software Engineering", company="JPMorgan Chase") == 4
    assert strategy.title_level("Software Engineer III (AVP)") == 3                 # bank style even with no company
    assert strategy.title_level("Lead Software Engineer - VP") == 4
    assert strategy.title_level("Executive Director, Technology", company="Morgan Stanley") == 6
    assert strategy.title_level("Vice President, Engineering", company="Conductor") == 7   # a real VP elsewhere
    assert strategy.title_level("Lead Software Engineer", company="JPMorgan Chase") == 4


def test_a_bank_vp_role_is_not_hidden_as_above_your_level():
    prefs = discover.with_profile({"regions": ["city", "country"], "candidate_years": 7, "include_senior": True})
    desc = ("Build Python and React services for trading platforms, FastAPI APIs, AWS. 6+ years of experience in "
            "software engineering. ") * 3
    job = {"title": "Vice President - Software Engineer (Python, React)", "company": "JPMorgan Chase",
           "location": "Bengaluru, India", "description": desc}
    assert not discover.rank(job, {"Python", "React"}, prefs)["excluded"]
    real_vp = job | {"title": "Vice President, Engineering", "company": "Conductor"}
    assert discover.rank(real_vp, {"Python", "React"}, prefs)["excluded"]
    assert gate.check(desc, job["title"], job["location"], "india", company="JPMorgan Chase")["verdict"] != "stop"
