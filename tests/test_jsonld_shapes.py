"""schema.org JobPosting fields can be lists or objects; reading a page must not fail on them."""
import json

from app import discover


def test_list_and_object_fields_read_as_text():
    blob = json.dumps({"@type": "JobPosting", "title": "Senior Software Engineer",
                       "hiringOrganization": {"name": "Acme"}, "datePosted": "2026-10-27",
                       "jobLocation": [{"address": {"addressLocality": ["Bengaluru", "Pune"],
                                                    "addressCountry": {"@type": "Country", "name": "India"}}}],
                       "description": ["Build services.", "Lead the team."], "url": ["https://x.example/a"]})
    j = discover._jsonld_jobs([blob], "https://acme.example/jobs/1")[0]
    assert j["location"] == "Bengaluru, Pune, India"
    assert j["title"] == "Senior Software Engineer" and j["company"] == "Acme"
    assert "Build services." in j["description"] and j["url"] == "https://acme.example/jobs/1"
    assert j["posted"] == "2026-10-27"
