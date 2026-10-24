"""Cover letter versions: every one is kept, any one can be used again."""
import json

import pytest

from app import letters
from app.config import output_dir

FIRST = "Dear Hiring Team,\n\nThe first draft.\n\nKind regards,\nTest Person"
WARM = "Dear Hiring Team,\n\nA warmer letter.\n\nKind regards,\nTest Person"


@pytest.fixture
def app_id():
    folder = output_dir() / "2026-10-24_Acme_Lead"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"created": "2026-10-24T10:00:00", "cover_letter": FIRST}), "utf-8")
    (folder / "model_reply.json").write_text(json.dumps({"cover_letter": FIRST}), "utf-8")
    return folder.name


def report(app_id):
    return json.loads((output_dir() / app_id / "report.json").read_text("utf-8"))


def test_first_draft_is_a_version(app_id):
    v = letters.list_versions(app_id)
    assert [x["id"] for x in v["versions"]] == ["first"] and v["kept"] == "first"


def test_switch_to_a_voice_and_back(app_id):
    warm = letters.add_version(app_id, {"voice": "warm", "label": "Warm", "text": WARM})
    letters.keep_letter(app_id, "", "", warm["id"])
    assert report(app_id)["cover_letter"] == WARM
    letters.keep_letter(app_id, "", "", "first")
    assert report(app_id)["cover_letter"] == FIRST
    assert len(letters.list_versions(app_id)["versions"]) == 2


def test_overwritten_first_draft_is_recovered(app_id):
    folder = output_dir() / app_id
    (folder / "report.json").write_text(json.dumps({"created": "x", "cover_letter": WARM, "cover_voice": "warm"}), "utf-8")
    v = letters.list_versions(app_id)
    assert {x["text"] for x in v["versions"]} == {FIRST, WARM}


def test_same_text_is_one_version(app_id):
    letters.add_version(app_id, {"voice": "warm", "text": WARM})
    letters.add_version(app_id, {"voice": "warm", "text": WARM})
    assert len(letters.list_versions(app_id)["versions"]) == 2


def test_first_draft_and_letter_in_use_cannot_be_removed(app_id):
    warm = letters.add_version(app_id, {"voice": "warm", "text": WARM})
    letters.keep_letter(app_id, "", "", warm["id"])
    letters.remove_version(app_id, "first")
    letters.remove_version(app_id, warm["id"])
    assert len(letters.list_versions(app_id)["versions"]) == 2


def test_banned_phrases_are_flagged():
    phrase = letters.banned_phrases()[0]
    assert letters.phrase_flags(f"Hello. {phrase}. Thanks!")
