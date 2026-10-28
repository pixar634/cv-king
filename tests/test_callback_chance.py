"""The chance of a callback beside the keyword score: a 100% keyword match on a job that fails a screen is a long shot."""
import json

from app import callback
from app.config import output_dir
from app.profile import load_profile


def _app(app_id, role, jd):
    folder = output_dir() / app_id
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"company": "Acme", "role": role, "market": "india",
                                                     "job": {"location": "Bengaluru, India"}}), "utf-8")
    (folder / "jd.txt").write_text(jd, "utf-8")
    return app_id


def test_full_keywords_but_a_failed_screen_is_a_long_shot():
    jd = ("Senior Product Manager. Required: 8+ years as a product manager, Python, React, SQL, roadmaps, "
          "stakeholder management. ") * 3
    got = callback.for_app(_app("pm", "Senior Product Manager", jd), load_profile(), {"max_years_asked": 9})
    assert got["label"] == "Long shot" and got["band"] == "under 2%"
    assert any("product manager" in w.lower() for w in got["why"])
    assert got["warm_band"] == "about 5 to 15%" and "Ashby" in got["source"]


def test_bands_by_label_and_referral():
    assert callback.band("Strong") == "about 6 to 12%"
    assert callback.band("Strong", warm=True) == "about 35 to 50%"
    assert callback.band("Possible") == "about 2 to 6%"
