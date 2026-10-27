"""Reading the model's JSON reply: whatever it wraps around the object."""
import pytest

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
