"""UK and Dutch sponsor registers: names matched without legal suffixes; UK jobs from non-sponsors marked unlikely."""
import json

import pytest

from app import discover, gate, sponsors


@pytest.fixture(autouse=True)
def registers(tmp_path, monkeypatch):
    f = tmp_path / "sponsors.json"
    names = {"uk": ["Monzo Bank Ltd", "Wise Payments Limited", "ALT", "Roofoods Ltd t/a Deliveroo", "Soho House Limited"], "nl": ["Adyen N.V.", "Booking.com B.V."]}
    f.write_text(json.dumps({"fetched": "2026-10-28"} | {k: sorted(sponsors.norm(n) for n in v) for k, v in names.items()}),
                 "utf-8")
    monkeypatch.setattr(sponsors, "CACHE", f)
    sponsors._loaded.clear()
    yield
    sponsors._loaded.clear()


def test_names_match_without_suffixes():
    assert sponsors.on_register("Monzo", "uk") and sponsors.on_register("Wise Payments Ltd", "uk")
    assert sponsors.on_register("Adyen", "nl") and sponsors.on_register("Booking.com", "nl")
    assert not sponsors.on_register("Al", "uk")             # too short to match a longer name
    assert not sponsors.on_register("Revolut", "uk")


def test_a_trading_name_matches():
    assert sponsors.on_register("Deliveroo", "uk") and sponsors.on_register("Roofoods", "uk")


def test_a_job_board_slug_matches_with_its_words_run_together():
    assert sponsors.on_register("Sohohouseco", "uk") and sponsors.on_register("Monzobank", "uk")
    assert not sponsors.on_register("Mon", "uk") and not sponsors.on_register("Sohoh", "uk")


def test_check_reads_the_country_from_the_location():
    assert sponsors.check("Monzo", "London, England") == {"register": "uk", "listed": True}
    assert sponsors.check("Revolut", "London") == {"register": "uk", "listed": False}
    assert sponsors.check("Adyen", "Amsterdam") == {"register": "nl", "listed": True}
    assert sponsors.check("Adyen", "Berlin, Germany") is None


def test_uk_job_from_a_non_sponsor_is_hidden_unless_the_posting_offers_sponsorship():
    prefs = discover.with_profile({"regions": ["city", "remote", "world"], "candidate_years": 7, "include_senior": True})
    text = "Build product features in Python and React. " * 10
    job = lambda company, extra="": {"title": "Software Engineer", "company": company, "location": "London, United Kingdom",
                                     "description": text + extra}
    yes, no = discover.rank(job("Monzo"), {"Python", "React"}, prefs), discover.rank(job("Revolut"), {"Python", "React"}, prefs)
    assert "licensed UK sponsor" in yes["reasons"] and not yes["excluded"]
    assert no["excluded"] and no["reasons"] == [discover.NOT_UK_SPONSOR]
    assert not discover.rank(job("Revolut", "Visa sponsorship is available."), {"Python", "React"}, prefs)["excluded"]
    g = gate.check(text, "Software Engineer", "London, United Kingdom", "uk", company="Revolut")
    assert g["verdict"] == "stop" and discover.NOT_UK_SPONSOR in g["reasons"]


def test_missing_lists_say_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sponsors, "CACHE", tmp_path / "none.json")
    sponsors._loaded.clear()
    assert sponsors.check("Monzo", "London") is None
