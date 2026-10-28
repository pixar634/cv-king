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
