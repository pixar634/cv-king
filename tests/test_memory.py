"""Memory: your own notes about your work become evidence, and nothing the note didn't say gets in."""
import pytest

from app import memory, tailor
from app.engines import EngineError
from app.profile import brief_view, bullet_index, load_profile, profile_text

NOTE = "At Acme I built the SSO login with Okta and role-based access for 3 business units, and set up Docker images."
JD = ("We need a lead engineer for authentication and SSO, Docker, Kubernetes and Python. "
      "You will own role-based access control, ship React and FastAPI services, and run Terraform. ") * 3


class Writer:
    def __init__(self, reply):
        self.reply = reply

    def run(self, brief, timeout=0):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def with_writer(monkeypatch, reply):
    monkeypatch.setattr(memory, "get_engine", lambda _id: Writer(reply))


def test_guard_drops_facts_that_add_numbers_or_skills():
    kept, dropped = memory.guard(NOTE, [
        "Built SSO login with Okta for 3 business units.",
        "Built SSO login for 12 business units.",            # a number the note doesn't have
        "Ran Kubernetes clusters for the portal.",          # a skill the note doesn't have
    ])
    assert kept == ["Built SSO login with Okta for 3 business units."] and len(dropped) == 2


def test_note_is_tidied_and_kept_against_its_role(monkeypatch):
    with_writer(monkeypatch, '{"facts": ["Built SSO login with Okta and role-based access for 3 business units.", '
                             '"Set up Docker images.", "Led 40 engineers."]}')
    e = memory.add("acme", NOTE, "claude:sonnet")
    assert e["facts"] == ["Built SSO login with Okta and role-based access for 3 business units.", "Set up Docker images."]
    assert e["dropped"] == ["Led 40 engineers."] and "Docker" in e["skills"]
    assert load_profile()["memory"][0]["role"] == "acme"


def test_without_a_writer_your_own_sentences_are_kept(monkeypatch):
    with_writer(monkeypatch, EngineError("no CLI"))
    e = memory.add("", "Mentored two new joiners. Ran the weekly release.", "claude:sonnet")
    assert e["facts"] == ["Mentored two new joiners.", "Ran the weekly release."] and not e["cleaned"]


def test_unknown_role_and_empty_note_are_refused(monkeypatch):
    with_writer(monkeypatch, '{"facts": []}')
    with pytest.raises(ValueError):
        memory.add("nowhere", NOTE, "claude:sonnet")
    with pytest.raises(ValueError):
        memory.add("acme", "  ok ", "claude:sonnet")


def test_memory_raises_the_reach_and_feeds_the_brief(monkeypatch):
    before = tailor.reach(JD, load_profile())
    with_writer(monkeypatch, '{"facts": ["Built SSO login with Okta and role-based access for 3 business units."]}')
    e = memory.add("acme", NOTE, "claude:sonnet")
    p = load_profile()
    assert tailor.reach(JD, p) > before and "Okta" in profile_text(p)
    sid = f"mem-{e['id']}-0"
    assert bullet_index(p)[sid][0] == "acme"
    role = next(r for r in brief_view(p)["experience"] if r["id"] == "acme")
    assert any(n["id"] == sid for k, v in role.items() if k.startswith("notes") for n in v)


def test_a_bullet_citing_a_memory_fact_is_traced(monkeypatch):
    with_writer(monkeypatch, '{"facts": ["Built SSO login with Okta for 3 business units."]}')
    e = memory.add("acme", NOTE, "claude:sonnet")
    p = load_profile()
    raw = {"headline": "Lead Software Engineer", "summary": "", "skills": [{"label": "Security", "items": ["Okta"]}],
           "experience": [{"id": "acme", "bullets": [{"text": "Built SSO login with Okta for 3 business units.", "src": [f"mem-{e['id']}-0"]}]}]}
    _, flags = tailor.validate(raw, p)
    assert not [f for f in flags if f["kind"] in ("trace", "numbers", "skills", "evidence")]


def test_edit_and_forget():
    p = load_profile()
    p["memory"] = [{"id": "m1", "role": "acme", "text": NOTE, "facts": ["Built SSO login."], "skills": [], "created": ""}]
    from app.profile import save_profile
    save_profile(p)
    e = memory.edit("m1", ["Built SSO login with Okta.", "  "])
    assert e["facts"] == ["Built SSO login with Okta."] and "Authentication" in e["skills"]
    memory.remove("m1")
    assert load_profile()["memory"] == []
