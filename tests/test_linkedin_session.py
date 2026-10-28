"""The Easy Apply session's LinkedIn links: a search with your filters, and the job id on any LinkedIn job page."""
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import linkedin
from app.main import app


def test_search_url_has_easy_apply_and_filters():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    url = c.post("/api/session/search", json={"title": "Lead AI Engineer", "modes": ["remote", "hybrid"],
                                              "place": "Bengaluru", "days": 3}).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://www.linkedin.com/jobs/search/?")
    assert q == {"keywords": ["Lead AI Engineer"], "f_AL": ["true"], "sortBy": ["DD"], "location": ["Bengaluru"],
                 "f_WT": ["2,3"], "f_TPR": ["r259200"]}
    assert c.post("/api/session/search", json={"title": " "}).status_code == 400


def test_job_id_from_linkedin_pages():
    assert linkedin.job_id("https://www.linkedin.com/jobs/view/4012345678/") == "4012345678"
    assert linkedin.job_id("https://in.linkedin.com/jobs/view/lead-engineer-at-acme-4012345678") == "4012345678"
    assert linkedin.job_id("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=x") == "4012345678"
    assert linkedin.job_id("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678") == "4012345678"
    assert linkedin.job_id("https://example.com/jobs/view/4012345678") == ""
    assert linkedin.job_id("https://www.linkedin.com/feed/") == ""


def test_fit_says_which_market_the_job_is_in():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    jd = "We need a Python engineer to build FastAPI services on AWS with PostgreSQL, Docker and Kubernetes. " * 4
    assert c.post("/api/fit", json={"jd": jd, "role": "Engineer", "location": "Bengaluru, Karnataka, India (Hybrid)"}).json()["market"] == "india"
