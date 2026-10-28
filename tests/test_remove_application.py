"""Removing an application: off the Pipeline, its folder kept in output/_trash, its history kept."""
import json

import pytest
from fastapi.testclient import TestClient

from app import tracker
from app.config import output_dir
from app.main import app

ROWS = [{"id": "a1", "company": "Honeywell", "role": "Architect", "status": "Generated", "created": "2026-10-28T10:00:00"},
        {"id": "a2", "company": "Acme", "role": "Lead", "status": "Applied", "created": "2026-10-28T10:00:00"}]


def test_remove_moves_the_folder_to_trash_and_keeps_the_rest():
    (output_dir() / "applications.json").write_text(json.dumps(ROWS), "utf-8")
    (output_dir() / "a1").mkdir()
    (output_dir() / "a1" / "cv.html").write_text("<p>cv</p>", "utf-8")
    got = TestClient(app, base_url="http://127.0.0.1:8765").delete("/api/apps/a1")
    assert got.status_code == 200 and got.json()["moved_to"].startswith("output/_trash/a1--removed-")
    assert [r["id"] for r in tracker.load()] == ["a2"]
    assert not (output_dir() / "a1").exists()
    kept = list((output_dir() / tracker.TRASH).iterdir())
    assert len(kept) == 1 and (kept[0] / "cv.html").read_text("utf-8") == "<p>cv</p>"
    with pytest.raises(KeyError):
        tracker.remove("a1")


def test_a_path_outside_the_applications_folder_is_refused():
    assert TestClient(app, base_url="http://127.0.0.1:8765").delete("/api/apps/..%2F..%2Fdata").status_code == 404
