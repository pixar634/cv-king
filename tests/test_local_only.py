"""Only the app's own pages and its extension can change anything; links to local files or addresses are dropped."""
import json

import pytest
from fastapi.testclient import TestClient

from app import discover, engines
from app.main import app

LOCAL = TestClient(app, base_url="http://127.0.0.1:8765")


def test_another_host_name_is_refused():
    # DNS rebinding: a site's own name pointed at 127.0.0.1 must not read anything
    assert TestClient(app, base_url="http://evil.example").get("/api/answers").status_code == 403
    assert LOCAL.get("/api/answers").status_code == 200


def test_writes_need_json_and_a_local_origin():
    simple = LOCAL.post("/api/answers", content='{"q": "Pronouns", "a": "they"}', headers={"Content-Type": "text/plain"})
    assert simple.status_code == 415   # what a cross-site form or fetch can send without a preflight
    foreign = LOCAL.post("/api/answers", json={"q": "Pronouns", "a": "they"}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    for origin in ("http://127.0.0.1:8765", "chrome-extension://abcdefghijklmnop"):
        assert LOCAL.post("/api/answers", json={"q": "Pronouns", "a": "they"}, headers={"Origin": origin}).status_code == 200


@pytest.mark.parametrize("url", ["file:///C:/Users/me/data/base_cv.json", "http://127.0.0.1:8765/api/answers",
                                 "http://192.168.1.1/admin", "http://[::1]/", "javascript:alert(1)", "http://localhost/"])
def test_local_job_links_are_dropped(url):
    assert discover._job("Engineer", "Acme", "Remote", url)["url"] == ""
    with pytest.raises(ValueError):
        discover.render_page(url)


def test_public_job_links_are_kept():
    assert discover._job("Engineer", "Acme", "Remote", "https://boards.greenhouse.io/acme/jobs/1")["url"]


def test_gemini_runs_with_no_tools():
    settings = json.loads(engines._gemini_no_tools().read_text("utf-8"))
    assert {"google_web_search", "web_fetch", "read_file"} <= set(settings["tools"]["exclude"])
    assert settings["mcp"]["allowed"] == ["none"] and settings["skills"]["enabled"] is False
