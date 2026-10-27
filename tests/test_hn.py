"""Hacker News "Who is hiring": only posts open worldwide, in APAC or at home; company, role and location read."""
from app import discover

STORY = {"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired? (October 2026)"},
                  {"objectID": "2", "title": "Ask HN: Who is hiring? (October 2026)"}]}
THREAD = {"children": [
    {"id": 10, "created_at": "2026-10-01T15:00:00Z",
     "text": 'Acme (YC W21) | Senior Backend Engineer | REMOTE (Worldwide) | Full-time<p>We build things. '
             '<a href="https:&#x2F;&#x2F;acme.dev&#x2F;jobs">apply</a>'},
    {"id": 11, "created_at": "2026-10-01T15:00:00Z", "text": "Beta | Staff Engineer | REMOTE (US only)<p>US only."},
    {"id": 12, "created_at": "2026-10-02T15:00:00Z", "text": "Gamma | Full-Stack Developer | Bengaluru, India | ONSITE<p>Hi."},
    {"id": 13, "created_at": "2026-10-02T15:00:00Z", "text": "Delta | Account Executive | REMOTE (Global)<p>Sales."},
]}


def test_reads_the_latest_hiring_thread(monkeypatch):
    monkeypatch.setattr(discover, "_json", lambda url, timeout=15: STORY if "search_by_date" in url else THREAD)
    jobs = discover.from_hn(discover.INDIA)
    assert [(j["company"], j["title"], j["location"]) for j in jobs] == [
        ("Acme", "Senior Backend Engineer", "REMOTE (Worldwide)"), ("Gamma", "Full-Stack Developer", "Bengaluru, India")]
    assert jobs[0]["url"] == "https://news.ycombinator.com/item?id=10" and "https://acme.dev/jobs" in jobs[0]["description"]
    assert jobs[0]["posted"] == "2026-10-01"
