"""Favourites on Workday or amazon.jobs are read without the model; model web searches run weekly, a few per scan."""
import datetime as dt

from app import discover

WD = {"jobPostings": [{"title": "Senior Software Engineer", "externalPath": "/job/Bengaluru/Senior-SWE_R1",
                       "locationsText": "Bengaluru, India", "postedOn": "Posted 3 Days Ago"}]}
AMZ = {"jobs": [{"title": "SDE II", "job_path": "/en/jobs/1/sde-ii", "normalized_location": "Bengaluru, Karnataka, IND",
                 "posted_date": "October 27, 2026", "description": "Build.", "basic_qualifications": "Python"}]}


def test_workday_listing(monkeypatch):
    sent = []
    monkeypatch.setattr(discover, "_post_json", lambda url, payload, timeout=25: sent.append((url, payload)) or WD)
    method, jobs = discover.from_ats("https://acme.wd5.myworkdayjobs.com/en-US/External", ["software engineer"])
    assert sent[0][0] == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    assert sent[0][1]["searchText"] == "software engineer"
    j = jobs[0]
    assert (j["title"], j["company"], j["location"]) == ("Senior Software Engineer", "Acme", "Bengaluru, India")
    assert j["url"] == "https://acme.wd5.myworkdayjobs.com/External/job/Bengaluru/Senior-SWE_R1"
    assert j["posted"] == (dt.date.today() - dt.timedelta(days=3)).isoformat()


def test_amazon_listing_and_board(monkeypatch):
    urls = []
    monkeypatch.setattr(discover, "_json", lambda url, timeout=15: urls.append(url) or AMZ)
    _, jobs = discover.from_ats("https://www.amazon.jobs/", ["software engineer"])
    assert "normalized_country_code%5B%5D=IND" in urls[0]
    assert jobs[0]["posted"] == "2026-10-27" and "Python" in jobs[0]["description"]
    assert discover.find_board("Amazon India") == ("amazon.jobs", "https://www.amazon.jobs/")


def test_model_searches_are_weekly_and_capped(monkeypatch):
    now = dt.datetime.now()
    rows = [{"id": f"w{i}", "kind": "websearch", "value": f"q{i}", "label": f"q{i}",
             "last_scan": (now - dt.timedelta(days=d)).isoformat(timespec="seconds")} for i, d in enumerate((1, 8, 9, 10, 30))]
    monkeypatch.setattr(discover, "load_sources", lambda: rows)
    ran = []
    monkeypatch.setattr(discover, "scan_one", lambda s, days, prefs=None: ran.append(s["id"]) or {"source": s["id"], "jobs": []})
    monkeypatch.setattr(discover, "_store", lambda *a, **k: {"jobs": []})
    discover.scan(None, 3, {})
    assert sorted(ran) == ["w2", "w3", "w4"]   # w0 ran yesterday; the oldest three of the rest, at most three
