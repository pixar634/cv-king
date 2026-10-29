"""Discover lists saved jobs at once with their last ranks, and brings them up to date in one background run; the
profile index is read from memory until the file changes."""
import datetime as dt
import json
import time

from app import discover, profiles

DESC = ("Requirements: Python, React, FastAPI, PostgreSQL, Docker, AWS, TypeScript, REST APIs. "
        "You will build product features end to end. ") * 3
PREFS = {"regions": ["city", "country"], "candidate_years": 7, "include_senior": True}


def job(jid):
    return {"id": jid, "title": "Lead Software Engineer", "company": f"Co {jid}", "location": "Bengaluru, India",
            "description": DESC, "posted": (dt.date.today() - dt.timedelta(days=2)).isoformat(), "first_seen": "",
            "url": f"https://example.com/{jid}"}


def test_stale_ranks_list_at_once_and_update_in_the_background():
    discover._save_jobs({"jobs": {j["id"]: j for j in (job("a"), job("b"), job("c"))}, "dismissed": []})
    first = discover.saved(14, PREFS)                     # nothing ranked yet: ranked here and now
    assert {j["id"] for j in first} == {"a", "b", "c"}
    state = discover._load_jobs()
    for j in state["jobs"].values():                      # as on the next day: every rank out of date
        j["_r"]["sig"] = "yesterday"
        j["_r"]["match"] = 1
    discover._save_jobs(state)

    listed = discover.saved(14, PREFS)                    # listed at once, with yesterday's numbers
    assert {j["id"] for j in listed} == {"a", "b", "c"} and all(j["match"] == 1 for j in listed)
    for _ in range(100):
        if not discover.rescore_progress()["running"]:
            break
        time.sleep(0.05)
    assert discover.rescore_progress() | {"label": ""} == {"running": False, "done": 3, "total": 3, "label": ""}
    sig = next(iter(discover._load_jobs()["jobs"].values()))["_r"]["sig"]
    assert sig != "yesterday"
    assert all(j["match"] > 1 for j in discover.saved(14, PREFS))   # today's ranks, and no new background run
    assert discover.rescore_progress()["total"] == 3


def test_profile_index_cached_until_written():
    profiles._write_index({"profiles": [{"id": "p1", "name": "One", "status": "ready"}], "last": "p1"})
    assert [p["id"] for p in profiles.all_profiles()] == ["p1"]
    profiles.all_profiles().append({"id": "edited"})      # what a caller does with its copy doesn't leak back
    assert [p["id"] for p in profiles.all_profiles()] == ["p1"]
    profiles._write_index({"profiles": [{"id": "p2", "name": "Two", "status": "ready"}], "last": "p2"})
    assert [p["id"] for p in profiles.all_profiles()] == ["p2"]
    profiles.INDEX.write_text(json.dumps({"profiles": [{"id": "p3", "name": "Three"}], "last": "p3"}), "utf-8")
    assert [p["id"] for p in profiles.all_profiles()] == ["p3"]    # changed on disk by someone else: read again
