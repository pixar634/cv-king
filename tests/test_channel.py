"""How each application was made: channel and the posting's age when you applied, kept in the tracker and database."""
import json
import sqlite3

import pytest

from app import db, tracker
from app.config import output_dir

ROW = {"id": "2026-10-20_Acme_Lead", "company": "Acme", "role": "Lead", "status": "Generated", "created": "2026-10-20T10:00:00",
       "applied_on": "", "posted": "2026-10-15", "job_url": "https://boards.greenhouse.io/acme/jobs/1"}


@pytest.fixture
def row():
    (output_dir() / ROW["id"]).mkdir(parents=True)
    (output_dir() / "applications.json").write_text(json.dumps([ROW]), "utf-8")


def test_applying_records_age_and_a_first_guess_at_channel(row):
    r = tracker.update(ROW["id"], {"status": "Applied", "applied_on": "2026-10-18"})
    assert r["posting_age"] == 3 and r["channel"] == "company site"
    assert tracker.update(ROW["id"], {"channel": "referral"})["channel"] == "referral"
    with pytest.raises(ValueError):
        tracker.update(ROW["id"], {"channel": "carrier pigeon"})


def test_channel_reaches_the_database_and_old_tables_are_rebuilt(row):
    con = sqlite3.connect(db.path())
    con.execute("CREATE TABLE applications (id TEXT PRIMARY KEY, created TEXT)")   # as an older version made it
    con.commit()
    con.close()
    tracker.update(ROW["id"], {"status": "Applied", "applied_on": "2026-10-18", "channel": "referral"})
    db.sync()
    with db.connect() as con:
        got = con.execute("SELECT channel, posting_age FROM applications").fetchone()
    assert tuple(got) == ("referral", 3)
