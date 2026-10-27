"""Tracks that can convert: at most three active; parked tracks never show in Discover and default off."""
import json

from app import discover, onboarding, strategy
from app.main import _default_prefs
from app.profile import load_profile


def _park(sandbox, tid):
    f = sandbox / "data" / "strategy.json"
    s = json.loads(f.read_text("utf-8"))
    s["tracks"][tid]["status"] = "parked"
    f.write_text(json.dumps(s), "utf-8")


def test_parked_track_is_hidden_in_discover_and_off_by_default(sandbox):
    _park(sandbox, "product_manager")
    assert strategy.active_tracks() == ["ic_lead"]
    assert _default_prefs(load_profile())["tracks"] == ["ic_lead"]
    prefs = discover.with_profile({"regions": ["city", "country"], "candidate_years": 7, "include_senior": True,
                                   "tracks": ["ic_lead", "product_manager"]})
    r = discover.rank({"title": "Senior Product Manager", "company": "Acme", "location": "Bengaluru, India",
                       "description": "Own the roadmap. " * 30}, set(), prefs)
    assert r["excluded"] and "parked" in r["reasons"][0]


def test_analysis_keeps_at_most_three_active_tracks():
    raw = {"tracks": {f"t{i}": {"label": f"Track {i}", "titles": [f"Engineer {i}"], "status": "primary"} for i in range(5)},
           "level": {"rank": 4, "services_title": True}}
    got = onboarding._validated(raw, load_profile())
    assert [t["status"] for t in got["tracks"].values()] == ["primary"] * 3 + ["parked"] * 2
    assert got["level"]["services_title"] is True
