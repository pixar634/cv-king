"""Levels as a product company reads them: a services firm's Lead is a Senior there, so Staff and Principal are two
steps up, and a posting that wants the level already held, or two gaps at once, is out of reach."""
import pytest

from app import discover, fitlabel, strategy
from app.fit import level_check

DESC = ("You will build product features in Python and React, own FastAPI services end to end, design APIs "
        "and review code on a product team. ") * 4


@pytest.fixture
def services_lead(monkeypatch):
    real = strategy.level
    monkeypatch.setattr(strategy, "level", lambda *a, **k: real(*a, **k) | {"services_title": True})


def test_staff_and_principal_are_two_steps_up_from_a_services_lead(services_lead):
    got = level_check("Principal Software Engineer", DESC, 8)
    assert got["verdict"] == "stretch"
    assert "title is two levels above yours (Lead at a services firm, which reads as Senior)" in got["reasons"]
    assert level_check("Senior Staff Software Engineer", DESC, 8)["verdict"] == "above"
    assert level_check("Sr. Principal Software Engineer", DESC, 8)["verdict"] == "above"


def test_a_product_company_lead_is_judged_as_a_lead():
    assert level_check("Principal Software Engineer", DESC, 8)["verdict"] == "ok"


def test_two_levels_up_and_short_on_years_is_out_of_reach(services_lead):
    got = level_check("Staff Software Engineer", DESC + " 10+ years of software engineering experience.", 8, max_years=10)
    assert got["verdict"] == "above" and any("and it asks for more years" in r for r in got["reasons"])


def test_a_posting_that_wants_the_level_already_held():
    text = DESC + " 10+ years of software engineering experience, including operating at Staff level."
    got = level_check("Senior Software Engineer", text, 8, max_years=10)
    assert got["verdict"] == "above" and "asks for experience at Staff level, a level your CV doesn't show" in got["reasons"]
    assert level_check("Senior Software Engineer", text, 12, max_years=14,
                       profile={"experience": [{"title": "Staff Engineer"}]})["verdict"] != "above"


def test_a_manager_title_is_a_stretch_without_direct_reports():
    got = level_check("Manager of Software Engineering", DESC + " 5+ years applied experience.", 8)
    assert got["verdict"] == "stretch" and "a people manager's role (your CV shows no direct reports)" in got["reasons"]


def test_a_title_you_have_held_means_the_same_kind_of_role():
    assert fitlabel.held("Senior Software Engineer", ["Software Engineer"])
    assert not fitlabel.held("Manager of Software Engineering", ["Software Engineer"])
    assert not fitlabel.held("Software Architect", ["Software Engineer"])


def test_a_title_above_your_level_is_not_one_you_have_held(services_lead):
    p = discover.with_profile({"candidate_years": 8, "regions": ["city"], "include_senior": True})
    got = discover.rank({"title": "Principal Software Engineer", "company": "Acme", "location": "Bengaluru, India",
                         "description": DESC}, {"Python", "React", "FastAPI"}, p)
    assert not got["excluded"] and "a title you've held" not in got["fit_why"] and "a stretch" in got["fit_why"]
