"""The tailoring queue: one job is never written twice at once."""
import pytest

from app import batch


@pytest.fixture
def started(monkeypatch):
    """An empty queue whose workers never start; returns the jobs that would have."""
    runs = []
    monkeypatch.setattr(batch, "_queues", {})
    monkeypatch.setattr(batch.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda s: runs.append(args)})())
    return runs


def test_second_tailor_of_a_running_job_returns_the_same_item(started):
    job = {"jd": "x" * 300, "company": "Acme Robotics", "role": "AI Engineer", "job_url": "https://jobs.example.com/acme/42"}
    first = batch.add([job], {})
    # The Apply button opened a form at another address; the page reads as the same company and role.
    again = batch.add([job | {"job_url": "https://jobs.example.com/acme/42/apply?src=x"}], {})
    assert first == again and len(started) == 1


def test_same_link_counts_as_the_same_job(started):
    first = batch.add([{"url": "https://jobs.example.com/acme/42?utm_source=a"}], {})
    assert batch.add([{"url": "https://jobs.example.com/acme/42"}], {}) == first


def test_a_different_job_still_queues(started):
    a = batch.add([{"jd": "x" * 300, "company": "Acme", "role": "AI Engineer"}], {})
    b = batch.add([{"jd": "x" * 300, "company": "Acme", "role": "Data Engineer"}], {})
    assert a != b and len(started) == 2


def test_a_finished_job_can_be_tailored_again(started):
    job = {"jd": "x" * 300, "company": "Acme", "role": "AI Engineer"}
    first = batch.add([job], {})
    batch._mine()[first[0]]["status"] = "done"
    assert batch.add([job], {}) != first


def test_the_pages_own_posted_wording_is_kept_as_a_date(started):
    import datetime as dt
    batch.add([{"jd": "x" * 300, "company": "Acme", "role": "AI Engineer", "posted": "Reposted 2 weeks ago"}], {})
    req = started[0][2]
    assert req["posted"] == (dt.date.today() - dt.timedelta(days=14)).isoformat()
