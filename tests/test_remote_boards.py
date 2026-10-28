"""Remote OK and We Work Remotely: read, credited by linking to their own postings, and checked like every job."""
from app import discover
from app.gate import openness

ROK = [{"legal": "Please link back to Remote OK."},
       {"position": "Senior Software Engineer", "company": "Acme", "location": "", "tags": ["python"],
        "url": "https://remoteok.com/remote-jobs/1", "date": "2026-10-26T08:00:12+00:00", "description": "<p>Build.</p>"},
       {"position": "Account Executive", "company": "Sales Co", "location": "", "tags": ["sales"],
        "url": "https://remoteok.com/remote-jobs/2", "date": "2026-10-26T08:00:12+00:00", "description": "Sell."}]
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Acme: Backend Engineer</title><region>Anywhere in the World</region><type>Full-Time</type>
<link>https://weworkremotely.com/remote-jobs/acme-backend</link><pubDate>Tue, 06 Oct 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Build APIs.&lt;/p&gt;</description></item>
<item><title>Gig Co: Full-stack Developer</title><region>Anywhere in the World</region><type>Contract</type>
<link>https://weworkremotely.com/remote-jobs/gig</link><pubDate>Tue, 06 Oct 2026 10:00:00 +0000</pubDate>
<description>Gigs.</description></item></channel></rss>"""


def test_remoteok_skips_its_legal_row_and_other_roles(monkeypatch):
    monkeypatch.setattr(discover, "_json", lambda url, timeout=15: ROK)
    jobs = discover.from_remoteok(["software engineer"])
    assert [(j["title"], j["location"], j["url"], j["posted"]) for j in jobs] == [
        ("Senior Software Engineer", "Remote", "https://remoteok.com/remote-jobs/1", "2026-10-26")]


def test_wwr_reads_region_and_type(monkeypatch):
    monkeypatch.setattr(discover, "_get", lambda url, timeout=15, accept="": RSS)
    jobs = discover.from_wwr()
    assert [(j["company"], j["title"], j["location"], j["posted"]) for j in jobs] == [
        ("Acme", "Backend Engineer", "Anywhere in the World", "2026-10-06"),
        ("Gig Co", "Full-stack Developer", "Anywhere in the World", "2026-10-06")]
    assert openness(jobs[0]["description"], jobs[0]["title"], jobs[0]["location"], True)["state"] == "open"
    assert openness(jobs[1]["description"], jobs[1]["title"], jobs[1]["location"], True)["state"] == "closed"
