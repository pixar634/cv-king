"""The network graph: you, companies coloured by what they mean for your search, and people by how they can help."""
import json

from app import network, warm
from app.config import output_dir

CSV = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Asha,Iyer,https://www.linkedin.com/in/asha,,Okta,Engineering Manager,01 Jan 2024
Ravi,K,https://www.linkedin.com/in/ravi,,Okta,Technical Recruiter,02 Feb 2025
Mei,L,https://www.linkedin.com/in/mei,,Initech,Senior Software Engineer,03 Mar 2025
"""


def test_kind_of_help():
    assert network.kind("Senior Technical Recruiter") == "recruiter"
    assert network.kind("Director of Engineering") == "leader"
    assert network.kind("Software Engineer II") == "peer"
    assert network.kind("Account Executive") == "other"


def test_sample_until_connections_are_added():
    g = network.graph({"regions": ["city"]})
    assert g["sample"] and g["stats"]["people"] > 20
    assert {n["type"] for n in g["nodes"]} == {"me", "company", "person"}
    assert all(any(link["target"] == n["id"] for link in g["links"]) for n in g["nodes"] if n["type"] != "me")


def test_real_connections_and_an_application_mark_the_company():
    warm.import_csv(CSV)
    (output_dir() / "applications.json").write_text(json.dumps([{"id": "a1", "company": "Okta, Inc.", "role": "Lead Engineer",
                                                                  "status": "Applied"}]), "utf-8")
    g = network.graph({"regions": ["city"]})
    assert not g["sample"] and g["stats"]["people"] == 3
    okta = next(n for n in g["nodes"] if n["type"] == "company" and n["label"] == "Okta")
    assert okta["status"] == "applied" and okta["count"] == 2 and okta["applied"][0]["role"] == "Lead Engineer"
    people = [n for n in g["nodes"] if n["type"] == "person" and n["company"] == "Okta"]
    assert [p["kind"] for p in people] == ["leader", "recruiter"]   # the most useful first
