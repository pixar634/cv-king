import datetime as dt

import pytest

from app import discover, strategy

TODAY = dt.date(2026, 10, 24)


@pytest.mark.parametrize("raw, expected", [
    ("Posted today", "2026-10-24"),
    ("3 hours ago", "2026-10-24"),
    ("yesterday", "2026-10-23"),
    ("3 days ago", "2026-10-21"),
    ("2w", "2026-10-10"),
    ("30+ days ago", "2026-09-24"),
    ("Oct 1, 2026", "2026-10-01"),
    ("1 October 2026", "2026-10-01"),
    ("2026-09-15T10:00:00Z", "2026-09-15"),
    ("", ""),
    ("whenever", ""),
])
def test_parse_posted(raw, expected):
    assert discover.parse_posted(raw, TODAY) == expected


def test_phrase_regex_matches_hyphen_and_space():
    pat = strategy.any_of(["full stack"])
    assert pat.search("Senior Full-Stack Engineer")
    assert pat.search("full stack developer")
    assert not pat.search("fullstackish")


def test_title_level():
    assert strategy.title_level("Lead Software Engineer") == 4
    assert strategy.title_level("Senior Product Manager") == 3
    assert strategy.title_level("Associate Director, Engineering") == 6
    assert strategy.title_level("Product Manager") is None


def test_refused_sites():
    assert discover.refused_reason("https://www.linkedin.com/jobs/view/1")
    assert discover.refused_reason("https://in.indeed.com/viewjob?jk=1")
    assert discover.refused_reason("https://jobs.lever.co/acme") is None


def test_pasted_reply_with_fences_and_curly_quotes():
    text = '```json\n{“jobs”: [{“title”: “Tech Lead”, “url”: “https://x.co/1”}], "notes": ""}\n```'
    jobs, notes = discover._parse_pasted(text)
    assert jobs == [{"title": "Tech Lead", "url": "https://x.co/1"}]


def test_pasted_reply_that_is_not_json():
    with pytest.raises(ValueError):
        discover._parse_pasted("no json here")


def test_every_track_is_searched_before_any_gets_a_second_query(monkeypatch):
    titles = {"ic_lead": ["Lead Software Engineer", "Tech Lead", "Engineering Lead"], "engineering_manager": ["Engineering Manager"],
              "product_manager": ["Senior Product Manager"], "delivery_lead": ["Delivery Manager"], "staff": ["Staff Software Engineer"],
              "forward_deployed": ["Forward Deployed Engineer"], "ai_engineer": ["AI Engineer"], "solutions_gtm": ["Solutions Architect"]}
    monkeypatch.setattr(strategy, "search_titles", lambda: titles)
    monkeypatch.setattr(strategy, "default_track", lambda: "ic_lead")
    q = discover.jobspy_queries({"tracks": list(titles)})
    assert q[:8] == ["Lead Software Engineer", "Engineering Manager", "Senior Product Manager", "Delivery Manager",
                     "Staff Software Engineer", "Forward Deployed Engineer", "AI Engineer", "Solutions Architect"]
    assert q[8] == "Tech Lead" and len(q) <= discover.JOBSPY_MAX_QUERIES
    assert "Forward Deployed Engineer" in discover.board_terms({"tracks": list(titles)})
