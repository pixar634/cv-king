"""The checks that run on every CV the model writes, before anything is printed."""
import json

import pytest

from app import tailor
from app.config import data_dir
from app.profile import load_profile


def set_level_word(word):
    p = data_dir() / "strategy.json"
    s = json.loads(p.read_text("utf-8"))
    s["level"]["headline_word"] = word
    p.write_text(json.dumps(s), "utf-8")


def headline_after(raw_headline):
    doc, flags = tailor.validate({"headline": raw_headline, "summary": "", "skills": [], "experience": []}, load_profile())
    return doc["headline"], [f for f in flags if f["kind"] == "headline"]


def test_headline_gets_the_level_word():
    set_level_word("Lead")
    headline, flags = headline_after("Senior Full Stack Engineer")
    assert headline == "Lead Full Stack Engineer" and flags


def test_headline_that_already_says_it_is_left_alone():
    set_level_word("Senior")
    headline, flags = headline_after("Senior Full Stack Engineer")
    assert headline == "Senior Full Stack Engineer" and not flags


# ---------- the match filter, checked before the writer runs ----------

FAR_JD = ("We need a backend engineer with Java, Kubernetes, AWS, Terraform, Kafka, Spark, Go and some Python. "
          "You will run Docker and PostgreSQL in production, and build CI/CD with Jenkins. ") * 3


class Reached(Exception):
    """Raised by the stand-in writer: the pipeline got past the match check."""


def no_writer(monkeypatch):
    def engine(_id):
        raise Reached
    monkeypatch.setattr(tailor, "get_engine", engine)


def test_reach_is_unmeasured_when_the_posting_names_few_keywords():
    assert tailor.reach("A friendly team that values communication and ownership. " * 6, load_profile()) is None


def test_job_below_the_filter_stops_before_writing(monkeypatch):
    no_writer(monkeypatch)
    with pytest.raises(tailor.BelowMatch) as e:
        tailor.run({"jd": FAR_JD, "min_match": 60, "market": "india"})
    assert e.value.reach < 60 and "below your 60% match filter" in str(e.value)


def test_tailor_anyway_goes_past_the_filter(monkeypatch):
    no_writer(monkeypatch)
    with pytest.raises(Reached):
        tailor.run({"jd": FAR_JD, "min_match": 60, "force": True, "market": "india"})


def test_any_match_filter_never_stops(monkeypatch):
    no_writer(monkeypatch)
    with pytest.raises(Reached):
        tailor.run({"jd": FAR_JD, "min_match": 0, "market": "india"})


def test_retry_with_force_keeps_the_entry_and_adds_force(monkeypatch):
    from app import batch
    seen = {}
    monkeypatch.setattr(batch, "add", lambda entries, defaults, min_match=0: seen.update(e=entries[0], m=min_match) or ["x"])
    batch._mine()["b1"] = {"id": "b1", "status": "error", "entry": {"url": "https://example.com/job"}, "defaults": {}}
    batch.retry("b1", 60, force=True)
    assert seen == {"e": {"url": "https://example.com/job", "force": True}, "m": 60}
