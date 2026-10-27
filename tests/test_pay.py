"""Pay against your floor: stated pay parsed (range, currency, period), company tiers when it isn't stated."""
import pytest

from app import discover, pay

PREFS = {"pay_floor_home_lpa": 50, "pay_floor_remote_lpa": 80, "pay_floor_abroad_eur": 0,
         "pay_tiers": "Databricks | top\nInitech Ltd | low"}


@pytest.mark.parametrize("text, low_lpa, cur", [
    ("CTC: ₹30 to 40 LPA", 30, "INR"),
    ("Compensation 45-60 LPA plus ESOPs", 45, "INR"),
    ("Salary: $120,000 - $150,000 per year", 105.6, "USD"),
    ("Base pay $120k–$150k", 105.6, "USD"),
    ("€70.000 – €85.000 brutto", 71.4, "EUR"),
    ("Wynagrodzenie 20 000 - 25 000 PLN / month", 57.6, "PLN"),
    ("£70k-£90k", 82.6, "GBP"),
])
def test_parse_reads_range_currency_and_period(text, low_lpa, cur):
    got = pay.parse(text)
    assert got["currency"] == cur and round(got["low"] / 1e5, 1) == low_lpa


def test_no_salary_and_hourly_are_not_pay():
    assert pay.parse("We raised $20m and offer a $500 learning budget.") is None
    assert pay.parse("Paid $60 per hour.") is None


def test_verdicts():
    job = lambda text, company="Acme", loc="Bengaluru": {"description": text, "company": company, "location": loc}
    assert pay.verdict(job("₹30 to 40 LPA"), "home", PREFS)["label"] == "below"
    assert pay.verdict(job("₹45 to 60 LPA"), "home", PREFS)["label"] == "likely"
    assert pay.verdict(job("₹55 to 70 LPA"), "home", PREFS)["label"] == "meets"
    assert pay.verdict(job("", "Databricks, Inc."), "home", PREFS) == {"label": "likely", "why": pay.TIERS["top"], "tier": "top"}
    assert pay.verdict(job(""), "home", PREFS)["label"] == "unknown"
    # abroad: the permit threshold is the floor even with no ask of your own
    assert pay.verdict(job("€40,000 a year", loc="Berlin, Germany"), "abroad", PREFS)["label"] == "below"


def test_discover_hides_below_floor_and_ranks_meeting_pay_first():
    prefs = discover.with_profile(PREFS | {"regions": ["city", "country"], "candidate_years": 7, "include_senior": True})
    base = "Build product features in Python and React, own services end to end. " * 8
    job = lambda text: {"title": "Software Engineer", "company": "Acme", "location": "Bengaluru, India", "description": base + text}
    assert discover.rank(job("CTC ₹30 to 40 LPA"), {"Python", "React"}, prefs)["excluded"]
    meets, plain = discover.rank(job("CTC ₹55 to 70 LPA"), {"Python", "React"}, prefs), discover.rank(job(""), {"Python", "React"}, prefs)
    assert meets["pay"] == "meets" and meets["order"] > plain["order"]
