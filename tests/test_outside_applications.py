"""Applications made outside JobMagnet (LinkedIn Easy Apply): on the Pipeline and in the callback rate, no CV."""
from fastapi.testclient import TestClient

from app import tracker
from app.main import app

CSV = """Application Date,Contact Email,Contact Phone Number,Company Name,Job Title,Job Url,Resume Name,Question And Answers
"9/11/26, 10:00 AM",a@b.c,1,Weekday AI,Lead Software Engineer,http://www.linkedin.com/jobs/view/1,,
"10/4/26, 9:00 AM",a@b.c,1,Rakuten India,Lead System Designer,http://www.linkedin.com/jobs/view/2,,
"5/12/19, 12:20 PM",a@b.c,1,Old Co,Data Engineer,http://www.linkedin.com/jobs/view/3,,
"""


def test_import_since_a_date_without_duplicates():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    assert c.post("/api/apps/import-linkedin", json={"csv": CSV, "since": "2026-09-01"}).json() == {"added": 2, "skipped": 0}
    rows = tracker.load()
    assert [(r["company"], r["status"], r["applied_on"], r["channel"], r["outside"]) for r in rows] == [
        ("Weekday AI", "Applied", "2026-09-11", "Easy Apply", "LinkedIn"), ("Rakuten India", "Applied", "2026-10-04", "Easy Apply", "LinkedIn")]
    assert c.post("/api/apps/import-linkedin", json={"csv": CSV, "since": "2026-09-01"}).json()["added"] == 0
    listed = c.get("/api/apps").json()
    assert len(listed) == 2 and all(r["callback"] is None and not r["callback_pending"] for r in listed)   # no CV, so no guide; the card still shows
    assert c.delete(f"/api/apps/{rows[0]['id']}").status_code == 200       # and it can be removed
