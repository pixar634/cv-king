"""Remote and Europe job boards, and places you'd move to."""
from app import discover, main
from app.profile import load_profile


def prefs(**kw):
    return discover.with_profile(main._prefs(load_profile()) | kw)


def ranked(job, p):
    ev = discover.strong_skills(load_profile())
    return discover.apply_regions(discover.rank(job, ev, p, discover.rank_signature(p, ev)), set(discover.regions(p)))


AMS = {"id": "j1", "title": "Lead Software Engineer", "company": "Acme", "location": "Amsterdam, North Holland, Netherlands",
       "url": "https://jobs.example.com/1", "posted": "", "remote": False,
       "description": "Python, FastAPI, React, TypeScript, AWS and Kubernetes on our platform. " * 8}


def test_a_place_you_would_move_to_needs_the_abroad_switch():
    assert ranked(AMS, prefs(regions=["city", "country", "remote"], places=["Amsterdam, Netherlands"]))["excluded"]
    assert not ranked(AMS, prefs(regions=["world"], places=["Amsterdam, Netherlands"]))["excluded"]


def test_a_place_matches_on_its_country_alone():
    job = AMS | {"location": "Netherlands"}
    assert not ranked(job, prefs(regions=["world"], places=["Amsterdam, Netherlands"]))["excluded"]


def test_indeed_country_for_a_place():
    assert discover.indeed_country("Amsterdam, Netherlands") == "netherlands"
    assert discover.indeed_country("Luxembourg") == "luxembourg"
    assert discover.indeed_country("Atlantis") == ""


def test_jobspy_also_searches_each_place_with_rest_of_the_world_on(monkeypatch):
    calls = []
    monkeypatch.setattr(discover, "from_jobspy", lambda q, loc, country, days: calls.append((loc, country)) or [])
    places = ["Amsterdam, Netherlands", "Luxembourg"]
    discover.scan_jobspy(7, prefs(regions=["city"], places=places))
    assert not any(c in calls for c in (("Amsterdam", "netherlands"), ("Luxembourg", "luxembourg")))
    discover.scan_jobspy(7, prefs(regions=["city", "world"], places=places), force=True)
    assert ("Amsterdam", "netherlands") in calls and ("Luxembourg", "luxembourg") in calls


FEEDS = {
    "remotive.com": {"jobs": [{"title": "Lead Software Engineer", "company_name": "Acme", "candidate_required_location": "Europe",
                               "url": "https://remotive.com/remote-jobs/1", "publication_date": "2026-10-20T10:00:00", "description": "<p>Python</p>"}]},
    "jobicy.com": {"jobs": [{"jobTitle": "Staff Engineer", "companyName": "Beta", "jobGeo": "Netherlands",
                             "url": "https://jobicy.com/jobs/2", "pubDate": "2026-10-21T10:00:00+00:00", "jobDescription": "Go"}]},
    "himalayas.app": {"jobs": [{"title": "Platform Engineer", "companyName": "Gamma", "locationRestrictions": [],
                                "applicationLink": "https://himalayas.app/companies/gamma/jobs/3", "pubDate": "1792500000", "description": "AWS"}]},
    "arbeitnow.com": {"data": [{"title": "Backend Engineer", "company_name": "Delta", "location": "Berlin", "remote": True,
                                "url": "https://www.arbeitnow.com/jobs/4", "created_at": 1792500000, "description": "Java"}]},
}


def test_each_board_is_read_into_jobs(monkeypatch):
    monkeypatch.setattr(discover, "_json", lambda url, timeout=15: next(v for k, v in FEEDS.items() if k in url))
    p = prefs(places=["Amsterdam, Netherlands"])
    for board, (title, where) in {"remotive": ("Lead Software Engineer", "Europe"), "jobicy": ("Staff Engineer", "Netherlands"),
                                  "himalayas": ("Platform Engineer", "Worldwide"), "arbeitnow": ("Backend Engineer", "Remote, Berlin")}.items():
        method, jobs = discover.from_board(board, p)
        j = jobs[0]
        assert (j["title"], j["location"]) == (title, where) and j["url"].startswith("https://") and j["posted"].startswith("20"), board


def test_switching_boards_on_and_off():
    discover.set_boards(["remotive", "jobicy", "nonsense"])
    assert {r["value"] for r in discover.load_sources() if r["kind"] == "board"} == {"remotive", "jobicy"}
    discover.set_boards(["jobicy"])
    assert [r["value"] for r in discover.load_sources() if r["kind"] == "board"] == ["jobicy"]


def test_a_country_jobicy_rejects_is_skipped(monkeypatch):
    import urllib.error

    def feed(url, timeout=15):
        if "geo=luxembourg" in url:
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
        return FEEDS["jobicy.com"]
    monkeypatch.setattr(discover, "_json", feed)
    _, jobs = discover.from_board("jobicy", prefs(places=["Luxembourg"]))
    assert jobs and all(j["location"] == "Netherlands" for j in jobs)


def test_work_in_finland_keeps_your_roles_and_describes_what_it_may(monkeypatch):
    feed = {"jobs": [
        {"title": "Lead Software Engineer", "employer": {"name": "Acme", "city": "Helsinki"}, "externalUrl": "https://thehub.fi/jobs/1"},
        {"title": "Staff Software Engineer", "employer": {"name": "Beta", "city": "Espoo"}, "externalUrl": "https://www.jobly.fi/en/job/2"},
        {"title": "Warehouse Worker", "employer": {"name": "Gamma", "city": "Oulu"}, "externalUrl": "https://thehub.fi/jobs/3"},
    ]}
    page = ('<html><script type="application/ld+json">{"@type": "JobPosting", "title": "Lead Software Engineer", '
            '"datePosted": "2026-10-20", "description": "<p>Python, FastAPI and React for our platform.</p>", '
            '"hiringOrganization": {"name": "Acme"}}</script></html>')
    monkeypatch.setattr(discover, "_json", lambda url, timeout=15: feed)
    monkeypatch.setattr(discover, "_get", lambda url, timeout=15, accept="": page.encode())
    monkeypatch.setattr(discover, "robots_allows", lambda url: True)
    method, jobs = discover.from_board("workinfinland", prefs())
    by = {j["company"]: j for j in jobs}
    assert set(by) == {"Acme", "Beta"}                                   # not a target role: left out
    assert "FastAPI" in by["Acme"]["description"] and by["Acme"]["posted"] == "2026-10-20" and by["Acme"]["location"] == "Helsinki, Finland"
    assert by["Beta"]["description"] == ""                               # Jobly refuses automated reading


def test_a_job_with_no_description_is_unscored_not_forty_percent():
    p = prefs(regions=["city", "country", "remote", "world"], places=["Amsterdam, Netherlands"])
    bare = ranked(AMS | {"description": ""}, p)
    assert bare["unscored"] and not bare["excluded"]
    assert not ranked(AMS, p)["unscored"]
