"""Reading the model's JSON reply: whatever it wraps around the object. Connector runs wait for the connector."""
import json
from types import SimpleNamespace

import pytest

from app import engines
from app.engines import EngineError, parse_json_reply


@pytest.mark.parametrize("reply", [
    '{"role": "Lead Engineer", "skills": ["Python"]}',
    '```json\n{"role": "Lead Engineer", "skills": ["Python"]}\n```',
    'Here it is:\n{"role": "Lead Engineer", "skills": ["Python"]}\nHope that helps.',
    # a note after the object that has braces of its own: "Extra data" before
    '{"role": "Lead Engineer", "skills": ["Python"]}\n\nNote: I left {company} blank.',
    # two objects: the first is the answer
    '{"role": "Lead Engineer", "skills": ["Python"]}\n{"role": "other"}',
    # a stray brace before the real object
    'Using {curly} placeholders:\n{"role": "Lead Engineer", "skills": ["Python"]}',
])
def test_reply_is_read_whatever_surrounds_it(reply):
    assert parse_json_reply(reply) == {"role": "Lead Engineer", "skills": ["Python"]}


@pytest.mark.parametrize("reply", ["No JSON here.", '{"role": "Lead Engineer", "skills": ["Python"'])
def test_no_complete_object_is_an_error(reply):
    with pytest.raises(EngineError):
        parse_json_reply(reply)


def test_connector_runs_wait_for_the_connector(monkeypatch):
    seen = []

    def fake_run(cmd, brief, timeout, env=None):
        seen.append(env)
        return SimpleNamespace(stdout=json.dumps({"result": "{}"}), stderr="", returncode=0)
    monkeypatch.setattr(engines.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(engines, "_run", fake_run)
    engines.ClaudeCLI("sonnet").run("brief", tools=["mcp__claude_ai_Gmail__create_draft"])
    engines.ClaudeCLI("sonnet").run("brief", tools=["WebSearch"])
    assert seen[0]["MCP_CONNECTION_NONBLOCKING"] == "false"
    assert seen[1] is None
