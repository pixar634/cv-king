from app import tracker


def test_saved_job_id_wins():
    row = {"job_id": " R-1234 ", "job_url": "https://www.linkedin.com/jobs/view/4012345678/"}
    assert tracker.job_id(row) == "R-1234"


def test_job_id_falls_back_to_the_posting_link():
    assert tracker.job_id({"job_id": "", "job_url": "https://www.linkedin.com/jobs/view/4012345678/"}) == "4012345678"
    assert tracker.job_id({"job_url": "https://boards.greenhouse.io/acme/jobs/5551234?gh_jid=5551234"}) == "5551234"


def test_no_link_no_id():
    assert tracker.job_id({"job_id": None, "job_url": None}) == ""
    assert tracker.job_id({"job_url": "https://acme.example/careers"}) == ""
