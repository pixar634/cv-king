"""Languages a posting insists on that you don't have, or that you've said you skip, are caught before writing,
shown on the callback card and the Pipeline card, and counted in Discover."""
import json

from app import callback, discover, gate
from app.config import output_dir
from app.profile import load_profile

STRONG = {"Python", "TypeScript", "React", "FastAPI"}
DOTNET = ("Build cloud services for our platform. Strong web development experience with C#, ASP.NET, .NET Core "
          "and able to troubleshoot complex scenarios. Experience with Azure. ") * 2
MIXED = ("Build backend services. Strong experience in one or more of Java, Go or Python. React on the front end. "
         "You will own services end to end and review code. ") * 2
BOTH = ("Build backend services. Strong experience in Python and Go is required. React on the front end. "
        "You will own services end to end and review code. ") * 2
# Word for word from a real posting: Go is offered beside Python, so Python meets it.
ANY_OF = ("Requirements\nProgramming: 3+ years of experience with any of the backend languages (Python, Go).\n"
          "Agent frameworks: hands-on experience building agents with LangGraph.\n"
          "We're building intelligent systems that go far beyond automation.")


def test_built_on_languages_you_dont_have_stops():
    got = gate.stack(DOTNET, "Senior Software Engineer", STRONG, {})
    assert got["stop"] == ["Built on .NET, C#, which isn't on your CV"]
    assert {m["name"]: m["must"] for m in got["missing"]} == {".NET": True, "C#": True}


def test_a_skipped_skill_stops_and_so_does_a_second_must_have_language():
    assert "chosen to skip" in gate.stack(DOTNET, "Engineer", STRONG, {"exclude_skills": ["C#"]})["stop"][0]
    got = gate.stack(BOTH, "Senior Software Engineer", STRONG, {})
    assert got["stop"] == ["Also needs Go as a must-have, which isn't on your CV"]


def test_a_must_have_language_stops_though_yours_is_named_elsewhere():
    # A real posting's shape: Java is demanded; Python turns up only in a nice-to-have line.
    text = ("Strong experience in Java, Spring Boot and Microservices.\nDesign and build travel booking services.\n"
            "Nice to have: scripting in Python for tooling.\n") * 2
    assert gate.stack(text, "Senior Software Engineer - Backend", STRONG, {})["stop"]


def test_a_language_offered_beside_yours_is_met():
    for text in (MIXED, ANY_OF):
        got = gate.stack(text, "Senior AI Platform Engineer", STRONG, {})
        assert got["stop"] == [], text
        assert not any(m["must"] for m in got["missing"])
    assert gate.stack(ANY_OF, "Senior Go Engineer", STRONG, {})["stop"]   # in the title, it's the job


def test_discover_hides_and_ranks_by_it():
    prefs = discover.with_profile({"regions": ["city", "country"], "candidate_years": 7, "include_senior": True})
    job = lambda desc: {"title": "Senior Software Engineer", "company": "Acme", "location": "Bengaluru, India", "description": desc}
    assert discover.rank(job(DOTNET), STRONG, prefs)["excluded"]
    assert discover.rank(job(BOTH), STRONG, prefs)["excluded"]
    mixed = discover.rank(job(MIXED), STRONG, prefs)
    assert not mixed["excluded"] and not any("Go" in w for w in mixed["fit_why"])


def test_the_callback_card_lists_them_and_caps_a_strong_label():
    folder = output_dir() / "a1"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"company": "Acme", "role": "Senior Software Engineer", "market": "india",
                                                     "job": {"location": "Bengaluru, India"}}), "utf-8")
    (folder / "jd.txt").write_text(DOTNET, "utf-8")
    got = callback.for_app("a1", load_profile(), {"max_years_asked": 9})
    assert got["label"] == "Long shot" and {m["name"] for m in got["missing"]} == {".NET", "C#"}
    assert (folder / "callback.json").exists()   # kept for the Pipeline until something it depends on changes


def test_pipeline_reads_only_what_is_worked_out_and_works_out_the_rest(monkeypatch):
    folder = output_dir() / "a2"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({"company": "Acme", "role": "Senior Software Engineer", "market": "india",
                                                     "job": {}}), "utf-8")
    (folder / "jd.txt").write_text(DOTNET, "utf-8")
    prefs = {"max_years_asked": 9}
    assert callback.cached("a2", prefs, {}) is None
    ran = []
    monkeypatch.setattr(callback.threading, "Thread", lambda target, args, daemon=None: type("T", (), {"start": lambda self: (ran.append(1), target(*args))})())
    assert callback.work_out_soon(["a2"], load_profile(), prefs) is True and ran
    assert callback.cached("a2", prefs, {})["label"] == "Long shot"
