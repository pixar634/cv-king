"""A scan reads only what can have changed and can matter: not a source read in the last few hours (a day when the
model reads it), not a board for a region you've turned off, not a company whose recent jobs were all elsewhere."""
import datetime as dt

from app import discover

PREFS = {"regions": ["city"]}


def ago(hours):
    return (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec="seconds")


def src(sid, kind="url", value="https://boards.greenhouse.io/acme", last=None, **extra):
    return {"id": sid, "kind": kind, "value": value, "label": sid, "last_scan": ago(last) if last is not None else "",
            "last_count": 0} | extra


def plan(sources, prefs=PREFS, **kw):
    keep, passed = discover.plan_scan(sources, prefs, **kw)
    return [s["id"] for s in keep], {p["label"]: p["why"] for p in passed}


def test_recently_read_sources_wait_and_the_model_waits_a_day():
    keep, passed = plan([src("fresh", last=1), src("old", last=4), src("never"),
                         src("web", kind="websearch", value="fde jobs", last=10),
                         src("page", last=10, method="page read by model")])
    assert keep == ["old", "never"] and passed == {"fresh": "recent", "web": "recent", "page": "recent"}


def test_a_board_for_a_region_you_turned_off_is_skipped():
    boards = [src("remoteok", kind="board", value="remoteok"), src("arbeitnow", kind="board", value="arbeitnow")]
    assert plan(boards) == ([], {"remoteok": "region", "arbeitnow": "region"})
    assert plan(boards, {"regions": ["city", "remote"]}) == (["remoteok"], {"arbeitnow": "region"})


def test_a_company_with_no_recent_jobs_in_your_regions_sits_out_unless_asked_for():
    now = dt.datetime.now().isoformat(timespec="seconds")
    jobs = {f"j{i}": {"id": f"j{i}", "title": "Engineer", "location": "Berlin, Germany", "source": "berlin-co",
                      "first_seen": now, "posted": ""} for i in range(5)}
    jobs["b1"] = {"id": "b1", "title": "Engineer", "location": "Bengaluru, India", "source": "blr-co", "first_seen": now,
                  "posted": ""}
    discover._save_jobs({"jobs": jobs, "dismissed": []})
    sources = [src("berlin-co"), src("blr-co"), src("fav-co", favourite=True)]
    assert plan(sources) == (["blr-co", "fav-co"], {"berlin-co": "quiet"})
    assert plan(sources, explicit=True)[1] == {}                      # asked for by name: read
    assert plan(sources, {"regions": ["city", "world"]})[1] == {}     # Rest of the world on: Berlin counts
    assert plan(sources + [src("recent", last=1)], force=True)[1] == {}


def test_a_scan_with_everything_skipped_reads_nothing(monkeypatch):
    discover.save_sources([src("fresh", last=1)])
    monkeypatch.setattr(discover, "scan_one", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read a source")))
    got = discover.scan(None, 14, PREFS)
    assert got["results"] == [] and got["skipped"] == [{"label": "fresh", "why": "recent"}]
    assert got["skipped_note"].startswith("Skipped 1 source: 1 scanned in the last few hours")


def test_jobspy_waits_a_few_hours_between_runs(monkeypatch):
    discover._save_jobs({"jobs": {}, "dismissed": [], "jobspy_at": ago(1)})
    monkeypatch.setattr(discover, "jobspy_queries", lambda prefs: ["Lead Software Engineer"])
    monkeypatch.setattr(discover, "from_jobspy", lambda *a: (_ for _ in ()).throw(AssertionError("scraped")))
    assert discover.scan_jobspy(14, PREFS)["skipped"] == [{"label": "JobSpy", "why": "recent"}]
