"""The extension's Watch button: the job you're reading -> its company watched in Discover, through the job board or
careers site it's on. No network: board links are worked out from addresses, robots.txt and boards are stubbed."""
from fastapi.testclient import TestClient

from app import discover
from app.main import app


def test_board_root_from_postings_and_embeds():
    assert discover.board_root("https://boards.greenhouse.io/acme/jobs/4012345") == "https://boards.greenhouse.io/acme"
    assert discover.board_root("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=401") == "https://boards.greenhouse.io/acme"
    assert discover.board_root("https://boards.greenhouse.io/embed/job_board/js?for=acme") == "https://boards.greenhouse.io/acme"
    assert discover.board_root("https://jobs.lever.co/acme/0f9c1e2a-1111-2222-3333-444455556666/apply") == "https://jobs.lever.co/acme"
    assert discover.board_root("https://jobs.ashbyhq.com/acme/0f9c1e2a-1111-2222-3333-444455556666") == "https://jobs.ashbyhq.com/acme"
    assert discover.board_root("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Bangalore/Lead_JR1") == "https://acme.wd5.myworkdayjobs.com/External"
    assert (discover.board_root("https://abc.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/12345")
            == "https://abc.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions")
    assert discover.board_root("https://www.greenhouse.io/") == "" and discover.board_root("https://careers.acme.com/jobs/1") == ""


def test_listing_url_on_a_company_site_keeps_the_search():
    url = ("https://careers.acme-data.com/jobs/220862/senior-ai-platform-engineer?search=workplaceType%3DREMOTE%26"
           "jobCategory%3D3%26datePosted%3DWITHIN_LAST_WEEK&j_idx=4")
    assert discover.listing_url(url) == "https://careers.acme-data.com/jobs?workplaceType=REMOTE&jobCategory=3&datePosted=WITHIN_LAST_WEEK"
    assert discover.listing_url("https://careers.acme.com/us/en/job/R12345/Lead-Engineer") == "https://careers.acme.com/us/en"
    links = [{"t": "Search all jobs", "h": "https://careers.acme.com/search"}, {"t": "About", "h": "https://careers.acme.com/about"}]
    assert discover.listing_url("https://careers.acme.com/lead-engineer-bengaluru", links) == "https://careers.acme.com/search"


def test_watch_finds_the_board_or_the_site_and_says_so(monkeypatch):
    monkeypatch.setattr(discover, "robots_allows", lambda url: True)
    monkeypatch.setattr(discover, "find_board", lambda name: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    embed = {"url": "https://www.acme.com/careers/job?gh_jid=4012345", "company": "Acme",
             "html": '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'}
    got = c.post("/api/discover/watch", json=embed).json()
    assert got["value"] == "https://boards.greenhouse.io/acme" and got["favourite"] and got["added"]
    assert got["found_via"] == "its Greenhouse board"
    assert c.post("/api/discover/watch", json=embed).json()["added"] is False          # once is enough

    own = c.post("/api/discover/watch", json={"url": "https://careers.globex.com/jobs/220862/senior-engineer", "company": "Globex"}).json()
    assert own["value"] == "https://careers.globex.com/jobs" and own["found_via"] == "its careers site"
    assert discover.watched("globex")["id"] == own["id"]
    assert discover.watched("", "https://careers.globex.com/jobs/1/x")["id"] == own["id"]   # the site you're on

    li = c.post("/api/discover/watch", json={"url": "https://www.linkedin.com/jobs/view/4012345678/", "company": "Initech"}).json()
    assert li["kind"] == "websearch" and li["added"]       # LinkedIn isn't read: the company is found by name
    assert c.post("/api/discover/watch", json={"url": "https://www.linkedin.com/jobs/view/1/"}).status_code == 400


def test_a_board_found_later_replaces_the_web_search(monkeypatch):
    monkeypatch.setattr(discover, "find_board", lambda name: None)
    first = discover.add_favourite("Initech")
    assert first["kind"] == "websearch"
    later = discover.watch("https://jobs.lever.co/initech/0f9c1e2a-1111-2222-3333-444455556666", "Initech")
    assert later["id"] == first["id"] and later["kind"] == "url" and later["value"] == "https://jobs.lever.co/initech"
    assert len([s for s in discover.load_sources() if s.get("favourite")]) == 1
