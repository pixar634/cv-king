"""Discover learns from your own applications: a kind of role that never brought a callback is marked down, a
posting that names only a few skills can't look like a strong fit, and research or people-manager roles you have
no title for are hidden."""
import datetime as dt

from app import discover, fitlabel, outcomes
from app.fit import level_check

TODAY = dt.date.today()
OLD = (TODAY - dt.timedelta(days=30)).isoformat()
NEW = (TODAY - dt.timedelta(days=3)).isoformat()


def app(i, role, status="Applied", applied_on=OLD):
    return {"id": f"a{i}", "company": f"Co{i}", "role": role, "status": status, "applied_on": applied_on}


def test_kind_of_role_from_the_title():
    assert outcomes.kind("Senior Manager, Engineering") == "people manager"
    assert outcomes.kind("Head of Engineering") == "people manager"
    assert outcomes.kind("Senior Technical Program Manager") == "product or programme manager"
    assert outcomes.kind("AI Delivery Manager") == "product or programme manager"
    assert outcomes.kind("Research Engineer / Scientist, Data & Evals") == "research"
    assert outcomes.kind("Applied AI Architect") == "architect"
    assert outcomes.kind("Lead Software Engineer") is None


def test_only_settled_applications_count():
    rows = [app(1, "Engineering Manager", "Rejected", NEW),        # a reply settles it at once
            app(2, "Delivery Manager"),                            # 30 days of silence
            app(3, "Engineering Manager", applied_on=NEW),         # still waiting: says nothing yet
            app(4, "Engineering Manager", "Withdrawn"),            # you pulled out: not the market's answer
            app(5, "Lead Software Engineer", "Rejected"),          # an engineer's title isn't split further
            app(6, "VP Engineering", "Generated", "")]             # never sent
    got = outcomes.by_kind(rows)
    assert got == {"people manager": {"settled": 1, "callbacks": 0, "rejected": 1},
                   "product or programme manager": {"settled": 1, "callbacks": 0, "rejected": 0}}


def test_a_cold_kind_costs_points_and_one_callback_clears_it():
    cold = outcomes.by_kind([app(i, "Senior Program Manager", "Rejected") for i in range(3)])
    pts, why = outcomes.penalty("Technical Program Manager", cold)
    assert pts == 2 and "no callback from your 3" in why and "3 rejected" in why
    colder = outcomes.by_kind([app(i, "Product Manager", "No response") for i in range(5)])
    assert outcomes.penalty("Senior Product Manager", colder)[0] == 4
    assert outcomes.penalty("Lead Software Engineer", colder) == (0, "")
    warm = outcomes.by_kind([app(i, "Product Manager", "No response") for i in range(5)]
                            + [app(9, "Product Manager", "Interviewing")])
    assert outcomes.penalty("Product Manager", warm) == (0, "")
    assert outcomes.penalty("Product Manager", outcomes.by_kind([app(1, "Product Manager", "Rejected")])) == (0, "")


BASE = dict(target=True, held_titles=["Software Engineer"], level="ok", at_level=True, must=(4, 4), terms=12,
            match=90, pay={"label": "meets"}, open_state="open")
JOB = {"title": "Senior Software Engineer", "posted": TODAY.isoformat(), "method": "Greenhouse API"}


def test_history_and_thin_postings_lower_the_label():
    plain = fitlabel.compute(JOB, **BASE)
    cold = fitlabel.compute(JOB, **BASE | {"history": (4, "no callback from your 5 architect applications")})
    assert cold["points"] == plain["points"] - 4 and "no callback from your 5 architect applications" in cold["why"]
    possible = BASE | {"target": False, "held_titles": [], "pay": {"label": "unknown"}}
    assert fitlabel.compute(JOB, **possible)["label"] == "Possible"
    assert fitlabel.compute(JOB, **possible | {"history": (4, "x")})["label"] == "Long shot"
    # Six generic skills, all covered: no bonus for the share, a point off for having so little to go on.
    thin = fitlabel.compute(JOB, **BASE | {"terms": 6, "match": 85, "must": (3, 4)})
    assert "names only 6 skills to measure" in thin["why"] and not any("must-haves" in w for w in thin["why"])
    assert thin["points"] < fitlabel.compute(JOB, **BASE)["points"]


DESC = ("You will build product features in Python and React, own FastAPI services end to end, design APIs "
        "and review code on a product team. ") * 4


def rank(title, desc=DESC, **prefs):
    p = discover.with_profile({"candidate_years": 8, "regions": ["city"], "include_senior": True} | prefs)
    return discover.rank({"title": title, "company": "Acme", "location": "Bengaluru, India", "description": desc},
                         {"Python", "React", "FastAPI"}, p)


def test_a_research_role_is_hidden_without_a_research_title():
    got = rank("Research Engineer, Data & Evals")
    assert got["excluded"] and got["reasons"] == ["a research role (no research or scientist title on your CV)"]


def test_a_manager_title_needing_years_of_reports_is_hidden_without_direct_reports():
    desc = DESC + " Has at least 4 years of people management experience and performance management."
    got = level_check("Senior Manager, Engineering", desc, 8)
    assert got["verdict"] == "above" and any("years managing people" in r for r in got["reasons"])
    # The same ask on an engineer's title stays a stretch: shown, ranked lower.
    lead = rank("Lead Software Engineer", desc)
    assert not lead["excluded"] and any("years managing people" in r for r in lead["reasons"])


def test_your_history_reaches_the_discover_card(monkeypatch):
    monkeypatch.setattr(outcomes, "by_kind",
                        lambda *a, **k: {"product or programme manager": {"settled": 5, "callbacks": 0, "rejected": 3}})
    got = rank("Senior Product Manager")
    assert not got["excluded"] and got["fit"] == "Long shot"
    assert "no callback from your 5 product or programme manager applications (3 rejected)" in got["fit_why"]
    assert not any("no callback" in w for w in rank("Lead Software Engineer")["fit_why"])
