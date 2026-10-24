"""Filling an application form: facts from the profile, saved answers, and the model only for the rest,
which may only pick options the form offers and must hand back what it can't answer."""
import json

import pytest

from app import apply, extract
from app.config import output_dir

REPORT = {"company": "Acme", "role": "Lead Engineer", "headline": "Lead Full-Stack Engineer",
          "recruiter_note": "", "cover_letter": "Dear Hiring Team,\n\nHello.\n\nKind regards,\nTest Person"}


@pytest.fixture
def app_id(sandbox):
    profile = json.loads((sandbox / "data" / "base_cv.json").read_text("utf-8"))
    profile["basics"] |= {"email": "test@example.com", "phone": "+91 90000 00000", "linkedin": "https://linkedin.com/in/test"}
    (sandbox / "data" / "base_cv.json").write_text(json.dumps(profile), "utf-8")
    folder = output_dir() / "2026-10-24_Acme_Lead"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps(REPORT), "utf-8")
    (folder / "jd.txt").write_text("We build things.", "utf-8")
    return folder.name


class FakeEngine:
    def __init__(self, reply):
        self.reply, self.briefs = reply, []

    def run(self, brief, timeout=420, tools=None):
        self.briefs.append(brief)
        return json.dumps(self.reply)


@pytest.fixture
def engine(monkeypatch):
    def use(reply):
        e = FakeEngine(reply)
        monkeypatch.setitem(apply.ENGINES, "fake", e)
        monkeypatch.setattr(apply, "light_engine", lambda: "fake")
        return e
    return use


def test_contact_fields_and_files_need_no_model(app_id, engine):
    e = engine({})
    fields = [{"id": "0:f1", "kind": "text", "label": "First Name"}, {"id": "0:f2", "kind": "text", "label": "Last Name"},
              {"id": "0:f3", "kind": "email", "label": "Email"}, {"id": "0:f4", "kind": "tel", "label": "Phone"},
              {"id": "0:f5", "kind": "url", "label": "LinkedIn Profile"},
              {"id": "0:f6", "kind": "file", "label": "Resume/CV"}, {"id": "0:f7", "kind": "file", "label": "Cover Letter"}]
    got = apply.answer(app_id, fields)
    assert got["answers"] == {"0:f1": "Test", "0:f2": "Person", "0:f3": "test@example.com", "0:f4": "+91 90000 00000",
                              "0:f5": "https://linkedin.com/in/test"}
    assert got["files"] == {"0:f6": "cv", "0:f7": "letter"}
    assert got["needs"] == [] and not e.briefs


def test_model_answers_are_held_to_the_form(app_id, engine):
    engine({"answers": {"0:a": "Yes", "0:b": "Maybe later", "0:c": "Decline to self-identify", "0:d": None},
            "why": {"0:d": "needs you"}})
    fields = [{"id": "0:a", "kind": "radio", "label": "Are you willing to work hybrid?", "options": ["Yes", "No"]},
              {"id": "0:b", "kind": "select", "label": "Notice period", "options": ["Immediate", "30 days", "60 days"], "required": True},
              {"id": "0:c", "kind": "select", "label": "Gender", "options": ["Male", "Female", "Decline to self-identify"]},
              {"id": "0:d", "kind": "text", "label": "Expected salary", "required": True}]
    got = apply.answer(app_id, fields)
    assert got["answers"] == {"0:a": "Yes", "0:c": "Decline to self-identify"}
    assert [n["id"] for n in got["needs"]] == ["0:b", "0:d"]   # an answer that isn't an option is not used


def test_saved_answers_fill_without_asking(app_id, engine):
    e = engine({})
    apply.remember("Do you require visa sponsorship?", "No")
    fields = [{"id": "0:v", "kind": "radio", "label": "Do you require visa sponsorship? *", "options": ["Yes", "No"]}]
    assert apply.answer(app_id, fields)["answers"] == {"0:v": "No"} and not e.briefs


def test_saved_answer_replaces_the_old_one(app_id):
    apply.remember("Notice period", "60 days")
    apply.remember("notice period?", "30 days")
    assert apply.saved() == [{"q": "notice period?", "a": "30 days"}]
    assert apply.forget("Notice Period") == []


def test_text_is_cut_to_the_field_length(app_id, engine):
    engine({"answers": {"0:w": "x" * 500}})
    got = apply.answer(app_id, [{"id": "0:w", "kind": "textarea", "label": "Why Acme?", "max": 100}])
    assert len(got["answers"]["0:w"]) == 100


def test_linkedin_list_link_points_at_the_posting():
    assert extract._canonical("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678") \
        == "https://www.linkedin.com/jobs/view/4012345678/"
    assert extract._canonical("https://in.indeed.com/jobs?q=lead&vjk=abc123") == "https://in.indeed.com/viewjob?jk=abc123"


def test_clip_reads_the_page_it_was_given(monkeypatch):
    monkeypatch.setattr(extract, "render_page", lambda *a, **k: pytest.fail("the page must not be fetched again"))
    ld = json.dumps({"@type": "JobPosting", "title": "Lead Engineer", "hiringOrganization": {"name": "Acme"},
                     "description": "<p>" + "Build and lead the platform team. " * 20 + "</p>", "datePosted": "2026-10-20"})
    got = extract.from_clip({"url": "https://careers.acme.example/jobs/12345", "title": "Lead Engineer", "text": "",
                             "jsonld": [ld], "links": [], "frames": [], "html": ""})
    assert (got["role"], got["company"], got["method"]) == ("Lead Engineer", "Acme", "schema.org JobPosting")
    assert got["url"] == "https://careers.acme.example/jobs/12345"
