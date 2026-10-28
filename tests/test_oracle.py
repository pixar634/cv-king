"""Oracle Recruiting Cloud careers sites (JPMorgan and others): read through their public search, no model."""
from app import discover

URL = "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_9/jobs?location=India&locationId=123"
LIST = {"items": [{"requisitionList": [
    {"Id": "2", "Title": "Lead Software Engineer", "PrimaryLocation": "Bengaluru, Karnataka, India", "PostedDate": "2026-10-27",
     "WorkplaceTypeCode": "ORA_HYBRID", "ShortDescriptionStr": "Short."},
    {"Id": "1", "Title": "Software Engineer III", "PrimaryLocation": "Hyderabad, Telangana, India", "PostedDate": "2026-10-20"}]}]}
DETAIL = {"items": [{"ExternalDescriptionStr": "<p>Build Python services &amp; APIs.</p>", "ExternalQualificationsStr": "5+ years"}]}


def test_reads_the_search_and_the_newest_in_full(monkeypatch):
    urls = []

    def fake(url, timeout=15):
        urls.append(url)
        return DETAIL if "Details" in url else LIST
    monkeypatch.setattr(discover, "_json", fake)
    method, jobs = discover.from_ats(URL, ["software engineer"])
    assert method == "Oracle Recruiting search"
    assert "siteNumber=CX_9" in urls[0] and "locationId=123" in urls[0] and "keyword=%22software%20engineer%22" in urls[0]
    assert [j["title"] for j in jobs] == ["Lead Software Engineer", "Software Engineer III"]   # newest first
    assert "Build Python services & APIs" in jobs[0]["description"] and "5+ years" in jobs[0]["description"]
    assert jobs[0]["url"] == "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_9/job/2"
    assert jobs[0]["posted"] == "2026-10-27"


def test_scan_names_the_employer_from_the_source(monkeypatch):
    monkeypatch.setattr(discover, "from_ats", lambda url, terms=None: ("Oracle Recruiting search",
                        [discover._job("Lead Software Engineer", "", "Bengaluru", "https://acme.fa.oraclecloud.com/x/job/2")]))
    got = discover.scan_one({"id": "s", "kind": "url", "value": URL, "label": "JPMorgan Chase", "company": "JPMorgan Chase"}, 3, {})
    assert got["jobs"][0]["company"] == "JPMorgan Chase"
