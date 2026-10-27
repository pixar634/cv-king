"""Fresh postings first; past 21 days hidden unless from a favourite; no date counts from the day first seen."""
import datetime as dt

from app import discover

DESC = ("Requirements: Python, React, FastAPI, PostgreSQL, Docker, AWS, TypeScript, REST APIs. "
        "You will build product features end to end. ") * 3


def day(n):
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def job(jid, posted="", first_seen="", **kw):
    return {"id": jid, "title": "Lead Software Engineer", "company": f"Co {jid}", "location": "Bengaluru, India",
            "description": DESC, "posted": posted, "first_seen": first_seen, "url": f"https://example.com/{jid}"} | kw


def test_order_and_hiding_by_age():
    discover._save_jobs({"jobs": {j["id"]: j for j in (
        job("fresh", day(1)), job("week", day(7)), job("old", day(18)), job("stale", day(25)),
        job("fav", day(25), favourite=True), job("nodate", first_seen=day(2) + "T10:00:00"),
        job("nodate_old", first_seen=day(40) + "T10:00:00"))}, "dismissed": []})
    prefs = {"regions": ["city", "country"], "candidate_years": 7, "include_senior": True}
    got = [j["id"] for j in discover.saved(30, prefs)]
    assert "stale" not in got and "nodate_old" not in got and "fav" in got
    assert got.index("fresh") < got.index("week") < got.index("old")
    nodate = next(j for j in discover.saved(30, prefs) if j["id"] == "nodate")
    assert any("first seen 2 days ago" in r for r in nodate["reasons"])
    assert "old" not in [j["id"] for j in discover.saved(14, prefs)]
