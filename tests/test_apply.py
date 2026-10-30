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
    assert apply.saved() == [{"q": "notice period?", "a": "30 days", "market": "india"}]
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


def _report(app_id, **kw):
    f = output_dir() / app_id / "report.json"
    f.write_text(json.dumps(json.loads(f.read_text("utf-8")) | kw), "utf-8")


SPONSOR = {"id": "0:s", "kind": "radio", "label": "Will you now or in the future require Visa Sponsorship?",
           "options": ["Yes, I will require sponsorship now or in the future", "No, I don't require sponsorship"]}


def test_sponsorship_follows_the_jobs_market_not_an_old_answer(app_id, engine):
    e = engine({})
    apply.remember(SPONSOR["label"], "Yes, I will require sponsorship now or in the future")   # saved on a Berlin form
    for market, want in (("india", "No, I don't require sponsorship"), ("remote", "No, I don't require sponsorship"),
                         ("eu", "Yes, I will require sponsorship now or in the future")):
        _report(app_id, market=market)
        assert apply.answer(app_id, [SPONSOR])["answers"] == {"0:s": want}
    assert not e.briefs


def test_pay_answers_stay_in_their_market_and_currency(app_id, engine):
    e = engine({"answers": {}, "why": {}})
    apply.remember("Expected salary", "50 LPA", market="india")
    pay = [{"id": "0:p", "kind": "text", "label": "Expected salary", "required": True}]
    _report(app_id, market="india")
    assert apply.answer(app_id, pay)["answers"] == {"0:p": "50 LPA"}
    _report(app_id, market="remote")
    got = apply.answer(app_id, pay)
    assert got["answers"] == {} and [n["id"] for n in got["needs"]] == ["0:p"]
    assert "50 LPA" not in e.briefs[-1]   # the model never sees another market's figure
    _report(app_id, market="india")
    usd = [{"id": "0:u", "kind": "text", "label": "Expected salary (USD)", "required": True}]
    apply.remember("Expected salary (USD)", "₹50 LPA", market="india")
    assert apply.answer(app_id, usd)["answers"] == {}


def test_saved_pay_answer_is_kept_per_market(app_id):
    apply.remember("Expected salary", "50 LPA", market="india")
    apply.remember("Expected salary", "$95,000", market="remote")
    apply.remember("Pronouns", "they/them", market="remote")
    assert {(r.get("market"), r["a"]) for r in apply.saved()} == {("india", "50 LPA"), ("remote", "$95,000"),
                                                                   (None, "they/them")}
    apply.forget("Expected salary", market="remote")
    assert [r["a"] for r in apply.saved()] == ["50 LPA", "they/them"]


def test_notice_and_title_come_from_the_profile(app_id, engine, sandbox):
    e = engine({})
    f = sandbox / "data" / "base_cv.json"
    f.write_text(json.dumps(json.loads(f.read_text("utf-8")) | {"basics": json.loads(f.read_text("utf-8"))["basics"]
                                                                | {"availability": "30 days' notice"}}), "utf-8")
    fields = [{"id": "0:n", "kind": "select", "label": "Notice period", "options": ["Immediate", "15 days", "1 month", "2 months"]},
              {"id": "0:d", "kind": "text", "label": "Earliest start date"},
              {"id": "0:t", "kind": "text", "label": "Current job title"}]
    assert apply.answer(app_id, fields)["answers"] == {"0:n": "1 month", "0:d": "30 days after an offer",
                                                       "0:t": "Lead Software Engineer"}   # not the tailored headline
    assert not e.briefs


def test_contact_box_filled_wrongly_from_the_cv_is_corrected(app_id, engine):
    e = engine({})
    # The site read the uploaded CV and put the address in the phone box; other boxes it filled are kept.
    fields = [{"id": "0:p", "kind": "tel", "label": "Phone", "value": "12 MG Road, Bengaluru 560001"},
              {"id": "0:m", "kind": "text", "label": "Mobile number", "value": "090000 00000"},
              {"id": "0:e", "kind": "email", "label": "Email", "value": "old@example.com"},
              {"id": "0:f", "kind": "text", "label": "First Name", "value": "Test"},
              {"id": "0:c", "kind": "text", "label": "City", "value": "Bangalore"},
              {"id": "0:s", "kind": "text", "label": "Expected salary", "value": "50 LPA"}]
    got = apply.answer(app_id, fields)
    assert got["answers"] == {"0:p": "+91 90000 00000", "0:e": "test@example.com"}
    assert got["needs"] == [] and not e.briefs


def test_phone_with_its_own_country_code_box_gets_the_number_alone(app_id, engine):
    e = engine({})
    fields = [{"id": "0:c", "kind": "select", "label": "Country code",
               "options": ["+1 United States", "+44 United Kingdom", "+91 India"]},
              {"id": "0:p", "kind": "tel", "label": "Phone"}]
    got = apply.answer(app_id, fields)
    assert got["answers"] == {"0:c": "+91 India", "0:p": "9000000000"} and not e.briefs


def test_country_code_box_is_known_by_its_options_and_corrects_the_site(app_id, engine):
    engine({})
    # The site read the CV into the phone box with the code, and its own code box shows +1.
    fields = [{"id": "0:c", "kind": "combobox", "label": "Phone", "value": "+1",
               "options": ["+1", "+44", "+91", "+61"]},
              {"id": "0:p", "kind": "tel", "label": "Mobile number", "value": "+91 90000 00000"}]
    assert apply.answer(app_id, fields)["answers"] == {"0:c": "+91", "0:p": "9000000000"}


def test_phone_without_a_code_box_keeps_the_country_code(app_id, engine):
    engine({})
    fields = [{"id": "0:p", "kind": "tel", "label": "Phone number"},
              {"id": "0:n", "kind": "select", "label": "Country", "options": ["India", "United States", "Germany"]}]
    assert apply.answer(app_id, fields)["answers"]["0:p"] == "+91 90000 00000"


def test_where_are_you_currently_located_is_answered_from_the_profile(app_id, engine, sandbox):
    e = engine({})
    profile = json.loads((sandbox / "data" / "base_cv.json").read_text("utf-8"))
    where = profile["basics"].get("location")
    labels = ["Where are you currently located?", "Where do you live?", "Your current city", "Current location *"]
    fields = [{"id": f"0:{i}", "kind": "text", "label": label} for i, label in enumerate(labels)]
    got = apply.answer(app_id, fields)
    assert got["answers"] == {f"0:{i}": where for i in range(len(labels))} and not e.briefs
