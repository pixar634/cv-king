"""Every test runs against a throwaway profile in a temp folder: never the real data/, output/ or profiles/."""
import json

import pytest

from app import profiles

PROFILE = {
    "basics": {"name": "Test Person", "headline": "Lead Software Engineer", "location": "Bengaluru, India"},
    "summary": "Lead engineer who builds Python and React products.",
    "experience": [
        {"id": "acme", "title": "Lead Software Engineer", "company": "Acme", "start": "2022-01", "end": None, "bullets": []},
        {"id": "initech", "title": "Software Engineer", "company": "Initech", "start": "2018-06", "end": "2021-12", "bullets": []},
    ],
    "skills": {"Languages": ["Python", "TypeScript"], "Frameworks": ["FastAPI", "React"]}, "projects": [], "education": [],
}

STRATEGY = {
    "field": "Software engineering",
    "level": {"rank": 4, "label": "Lead", "current_title": "Lead Software Engineer"},
    "tracks": {
        "ic_lead": {"label": "IC Lead", "titles": ["Lead Software Engineer", "Tech Lead"], "match": ["software engineer"],
                    "also_match": ["engineer"], "headline_examples": ["Lead Full-Stack Engineer"]},
        "product_manager": {"label": "Product Manager", "titles": ["Senior Product Manager"], "match": ["product manager"],
                            "also_match": ["product manager"], "lateral": True, "headline_examples": []},
    },
    "default_track": "ic_lead",
    "discover": {"role_words": ["engineer", "product manager"], "exclude_title_words": ["recruiter"]},
}


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    data, output = tmp_path / "data", tmp_path / "output"
    data.mkdir()
    output.mkdir()
    (data / "base_cv.json").write_text(json.dumps(PROFILE), "utf-8")
    (data / "strategy.json").write_text(json.dumps(STRATEGY), "utf-8")
    monkeypatch.setattr(profiles, "DATA", data)
    monkeypatch.setattr(profiles, "OUTPUT", output)
    monkeypatch.setattr(profiles, "PROFILES", tmp_path / "profiles")
    monkeypatch.setattr(profiles, "INDEX", tmp_path / "profiles" / "index.json")
    return tmp_path
