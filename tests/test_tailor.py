"""The checks that run on every CV the model writes, before anything is printed."""
import json

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
