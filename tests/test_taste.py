"""Not for me: what Discover learns from passed and tailored jobs."""
from app import taste


def job(i, title, company="Acme", location="Bengaluru, India", skills=()):
    return {"id": f"j{i}", "title": title, "company": company, "location": location, "skills": list(skills),
            "url": f"https://x.co/{i}", "match": 80, "order": 80, "reasons": []}


def passed(*items):
    state = {}
    for j, reason in items:
        taste.record(state, j, "no", reason)
    return state


def test_one_pass_teaches_nothing():
    m = taste.model(passed((job(1, "Site Reliability Engineer"), "role")))
    assert m["rules"] == []


def test_two_agreeing_passes_push_similar_jobs_down():
    state = passed((job(1, "Site Reliability Engineer", "A"), "role"), (job(2, "Senior Site Reliability Engineer", "B"), "role"))
    m = taste.model(state)
    assert [r["value"] for r in m["rules"]] == ["site reliability"]
    out = taste.apply(job(3, "Staff Site Reliability Engineer", "C"), m)
    assert out["order"] < 80 and out["learned"] == ["title:site reliability"]
    assert taste.apply(job(4, "Backend Engineer", "C"), m)["order"] == 80


def test_own_title_words_never_become_rules():
    state = passed((job(1, "Lead Software Engineer", "A"), "role"), (job(2, "Lead Software Engineer II", "B"), "role"))
    assert taste.model(state)["rules"] == []


def test_not_this_company_hides_it_at_once():
    m = taste.model(passed((job(1, "Backend Engineer", "Globex"), "company")))
    assert taste.apply(job(2, "Platform Engineer", "Globex"), m) is None


def test_a_repost_is_hidden():
    m = taste.model(passed((job(1, "Backend Engineer", "Globex"), "")))
    assert taste.apply(job(9, "Backend Engineer", "Globex"), m) is None


def test_tailored_jobs_outvote_a_rule():
    state = passed((job(1, "Security Engineer", "A"), "role"), (job(2, "Security Engineer III", "B"), "role"))
    taste.record(state, job(3, "Security Engineer", "C"), "yes")
    assert taste.model(state)["rules"] == []


def test_a_forgotten_rule_stops_applying():
    state = passed((job(1, "Site Reliability Engineer", "A"), "role"), (job(2, "Site Reliability Lead", "B"), "role"))
    taste.forget(state, "title:site reliability")
    assert taste.model(state)["rules"] == []


def test_level_rule_spares_sideways_moves():
    state = passed((job(1, "Senior Software Engineer", "A"), "level"), (job(2, "Senior Backend Engineer", "B"), "level"))
    m = taste.model(state)
    assert any(r["kind"] == "level" for r in m["rules"])
    assert taste.apply(job(3, "Senior Frontend Engineer", "C"), m)["order"] < 80
    assert taste.apply(job(4, "Senior Product Manager", "C"), m)["order"] == 80
