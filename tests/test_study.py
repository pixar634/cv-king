"""The study list: gaps ticked under a tailored CV, kept in the local database to learn later."""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import db, study, tracker
from app.main import app

API = TestClient(app, base_url="http://127.0.0.1:8765")


def add_app(app_id, company="Acme", role="Lead Engineer"):
    tracker._save(tracker.load() + [{"id": app_id, "created": "2026-10-01T10:00:00", "company": company, "role": role,
                                     "status": "Generated", "applied_on": "", "domain": "", "logo": False}])


def test_a_ticked_gap_goes_on_the_list_with_the_job_that_asked():
    add_app("a1")
    got = API.post("/api/study", json={"app_id": "a1", "terms": [{"term": "LangGraph", "must_have": True, "note": "agent orchestration"}]})
    assert got.status_code == 200
    [item] = got.json()["items"]
    assert (item["term"], item["kind"], item["status"]) == ("LangGraph", "skill", "todo")
    assert item["asks"] == [{"app_id": "a1", "company": "Acme", "role": "Lead Engineer", "must_have": True,
                             "note": "agent orchestration", "at": item["asks"][0]["at"]}]


def test_one_skill_is_one_row_however_it_is_written():
    add_app("a1")
    add_app("a2", company="Initech")
    study.add([{"term": "LangGraph"}], "a1")
    study.update(study.items()[0]["id"], {"status": "learning"})
    items = study.add([{"term": " langgraph "}, "Kubernetes"], "a2")
    lg = next(i for i in items if i["key"] == "langgraph")
    assert len(items) == 2 and lg["status"] == "learning"   # asked again: keeps its progress
    assert {a["company"] for a in lg["asks"]} == {"Acme", "Initech"}
    assert items[0]["key"] == "langgraph"   # what you're learning comes first


def test_your_own_items_need_no_application():
    [item] = API.post("/api/study", json={"terms": ["AWS Solutions Architect"], "kind": "certification"}).json()["items"]
    assert item["kind"] == "certification" and item["asks"] == []
    assert API.post("/api/study", json={"terms": ["  "]}).status_code == 400
    assert API.post("/api/study", json={"app_id": "nope", "terms": ["Go"]}).status_code == 404


def test_status_plan_and_date_are_saved():
    [item] = study.add(["Rust"])
    done = API.post(f"/api/study/{item['id']}", json={"status": "done", "notes": "Build a CLI", "target_on": "2026-12-31"}).json()
    assert (done["status"], done["notes"], done["target_on"]) == ("done", "Build a CLI", "2026-12-31") and done["done_on"]
    back = study.update(item["id"], {"status": "learning", "target_on": ""})
    assert back["done_on"] is None and back["target_on"] is None
    assert API.post(f"/api/study/{item['id']}", json={"status": "maybe"}).status_code == 400
    assert API.post(f"/api/study/{item['id']}", json={"target_on": "next year"}).status_code == 400
    assert API.post("/api/study/999", json={"status": "done"}).status_code == 404


def test_removing_drops_the_item_and_its_asks():
    add_app("a1")
    [item] = study.add(["Kafka"], "a1")
    assert API.delete(f"/api/study/{item['id']}").status_code == 200
    assert study.items() == []
    with closing(db.connect()) as con:
        assert con.execute("SELECT COUNT(*) FROM study_asks").fetchone()[0] == 0


def test_a_sync_and_a_re_tailor_keep_the_list(monkeypatch):
    monkeypatch.setattr(tracker, "fetch_logo", lambda *a: False)
    add_app("old")
    study.add([{"term": "LangGraph", "must_have": True}], "old")
    db.sync()
    report = {"id": "new", "created": "2026-10-20T09:00:00", "company": "Acme", "role": "Lead Engineer",
              "market_label": "India", "options": {"variant": "flowcv", "max_pages": 2}, "engine": "claude:opus",
              "scores": {"before": 40, "after": 70, "ceiling": 75}, "pdf": "cv.pdf", "jd_meta": {}}
    tracker.add_from_report(report, {"replace_id": "old"})
    [item] = study.items()
    assert [a["app_id"] for a in item["asks"]] == ["new"]


def test_unknown_kinds_are_refused():
    [item] = study.add(["Go"])
    with pytest.raises(ValueError):
        study.update(item["id"], {"kind": "hobby"})


def keyword_rows(rows):
    """Applications and their keyword results, as a sync copies them from the reports."""
    with closing(db.connect()) as con, con:
        for app_id, company, term in rows:
            con.execute("INSERT OR IGNORE INTO applications (id, company, role) VALUES (?, ?, 'Engineer')", (app_id, company))
            con.execute("INSERT INTO keywords (app_id, term, tier, covered, in_profile) VALUES (?, ?, 'required', 0, 0)", (app_id, term))


def test_suggestions_are_the_most_required_skills_not_on_the_list():
    keyword_rows([("a1", "Acme", "Kubernetes"), ("a2", "Initech", "Kubernetes"), ("a1", "Acme", "Terraform"),
                  ("a2", "Initech", "Python")])                 # the profile has Python: never suggested
    assert [(g["term"], g["jobs"]) for g in study.suggestions()] == [("Kubernetes", 2), ("Terraform", 1)]
    [item] = study.add(["Kubernetes"], asked=True)
    assert {a["company"] for a in item["asks"]} == {"Acme", "Initech"} and all(a["must_have"] for a in item["asks"])
    assert [g["term"] for g in study.suggestions()] == ["Terraform"]


def test_a_finished_skill_goes_into_the_growth_log_and_on_your_cvs():
    from app import career
    from app.profile import load_profile
    [item] = study.add(["LangGraph"])
    assert API.post(f"/api/study/{item['id']}/log", json={}).status_code == 400   # not done yet
    study.update(item["id"], {"status": "done"})
    logged = API.post(f"/api/study/{item['id']}/log", json={"use_on_cv": True}).json()
    [entry] = career.log()
    assert (entry["kind"], entry["text"], entry["use_on_cv"]) == ("skill", "LangGraph", True)
    assert logged["log_id"] == entry["id"] and "LangGraph" in load_profile()["confirmed_skills"]
    assert API.post(f"/api/study/{item['id']}/log", json={}).status_code == 400   # once only
    career.remove_log(entry["id"])
    assert study.items()[0]["log_id"] is None                                     # taken out of the log: can log again


def test_a_finished_project_is_logged_as_done_work_never_as_a_skill():
    from app import career
    [item] = study.add(["RAG search over my notes"], kind="project")
    study.update(item["id"], {"status": "done"})
    study.log(item["id"], use_on_cv=True)
    [entry] = career.log()
    assert (entry["kind"], entry["text"], entry["use_on_cv"]) == ("did", "Built a project: RAG search over my notes", False)


def test_the_career_map_reads_the_study_list():
    add_app("a1")
    study.add([{"term": "LangGraph", "must_have": True}], "a1")
    item = next(i for i in study.add(["AWS Solutions Architect"], kind="certification") if i["kind"] == "certification")
    study.update(item["id"], {"status": "learning", "target_on": "2027-03-01", "notes": "Udemy course"})
    lines = study.for_brief().splitlines()
    assert lines[0] == "- AWS Solutions Architect [certification; learning; aiming for 2027-03-01] plan: Udemy course"
    assert lines[1] == "- LangGraph [skill; to study; required by 1 of 1 tailored jobs that named it]"


def test_an_older_study_table_gets_the_new_column():
    with closing(db.connect()) as con, con:
        con.execute("DROP TABLE study_items")
        con.execute("CREATE TABLE study_items (id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT NOT NULL, key TEXT NOT NULL UNIQUE, "
                    "kind TEXT NOT NULL DEFAULT 'skill', status TEXT NOT NULL DEFAULT 'todo', notes TEXT NOT NULL DEFAULT '', "
                    "target_on TEXT, added TEXT NOT NULL, updated TEXT NOT NULL, done_on TEXT)")
        con.execute("INSERT INTO study_items (term, key, added, updated) VALUES ('Go', 'go', 'x', 'x')")
    assert study.items()[0]["log_id"] is None and study.items()[0]["term"] == "Go"
