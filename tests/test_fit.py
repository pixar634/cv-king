"""The match shown before tailoring: code only, and the same numbers tailoring reports."""
from app import tailor
from app.profile import load_profile, original_cv_path

JD = ("We need a backend engineer with Java, Kubernetes, AWS, Terraform, Kafka, Spark, Go and some Python. "
      "You will run Docker and PostgreSQL in production, build CI/CD with Jenkins, and ship React and FastAPI. ") * 3


def test_fit_splits_what_tailoring_adds_from_what_you_lack():
    original_cv_path().write_text("Lead engineer. Python.", "utf-8")
    f = tailor.fit(JD, load_profile(), 60)
    assert f["measured"] and f["now"] < f["reach"]
    assert "React" in f["adds"] and "FastAPI" in f["adds"] and "Python" not in f["adds"]
    assert "Kubernetes" in f["gaps"] and "Python" not in f["gaps"] and len(f["gaps"]) <= 8
    assert f["reach"] == tailor.reach(JD, load_profile())


def test_fit_flags_a_job_below_the_filter_only_when_a_filter_is_set():
    assert tailor.fit(JD, load_profile(), 90)["below"]
    assert not tailor.fit(JD, load_profile(), 0)["below"]


def test_fit_without_enough_keywords_is_unmeasured():
    f = tailor.fit("A friendly team that values communication and ownership. " * 6, load_profile(), 60)
    assert not f["measured"] and "below" not in f


def test_fit_endpoint_uses_the_saved_match_filter():
    from app import main
    from app.profile import save_profile
    p = load_profile()
    p["preferences"] = {"min_match": 90}
    save_profile(p)
    f = main.fit_preview({"jd": JD})
    assert f["minimum"] == 90 and f["below"]
