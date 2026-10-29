"""Discover shows only the regions you picked: places you'd move to narrow "Rest of the world" and never override it,
a listing with no location is placed by its own text, and a title with no level word is judged by the years asked."""
from app import discover

DESC = ("You will build product features in Python and React, own FastAPI services end to end, design APIs "
        "and review code on a product team. ") * 4
EVIDENCE = {"Python", "React", "FastAPI"}


def rank(prefs, title="Lead Software Engineer", location="Bengaluru, India", desc=DESC):
    """The job as Discover lists it: ranked, then filtered by the region chips."""
    job = {"title": title, "company": "Acme", "location": location, "description": desc}
    p = discover.with_profile({"candidate_years": 8} | prefs)
    return discover.apply_regions(discover.rank(job, EVIDENCE, p), set(discover.regions(p)))


def test_places_show_only_with_rest_of_the_world_on():
    city_only = {"regions": ["city"], "places": ["Germany"]}
    assert not rank(city_only)["excluded"]
    assert rank(city_only, location="Berlin, Germany")["excluded"]
    world = {"regions": ["city", "world"], "places": ["Germany"]}
    berlin = rank(world, location="Berlin, Germany")
    assert not berlin["excluded"] and "a place you'd move to" in berlin["reasons"]


def test_abroad_anywhere_shows_when_it_sponsors_and_silent_only_in_your_places():
    world = {"regions": ["world"], "places": ["Germany"]}
    silent = rank(world, location="Sydney, Australia")
    assert silent["excluded"] and silent["reasons"] == [discover.ABROAD_SILENT]
    assert not rank(world, location="Sydney, Australia", desc=DESC + " Visa sponsorship is available.")["excluded"]
    assert rank(world, location="Austin, Texas")["excluded"]                     # US onsite stays out


def test_remote_from_any_country_shows_when_it_hires_where_you_live():
    remote = {"regions": ["remote"]}
    assert not rank(remote, location="Remote, Australia", desc=DESC + " Open to candidates across APAC.")["excluded"]
    assert not rank(remote, location="Remote - New Zealand", desc=DESC + " We also hire in India.")["excluded"]
    local = rank(remote, location="Remote, Australia")
    assert local["excluded"] and "Remote within another country" in local["reasons"][0]
    assert rank(remote, location="Remote", desc=DESC + " Our office is in Indianapolis.")["open"] == "unclear"
    assert rank({"regions": ["city"]}, location="Remote, Australia", desc=DESC + " Worldwide.")["excluded"]   # Remote off


def test_a_listing_without_a_location_is_placed_by_its_text():
    city_only = {"regions": ["city"]}
    assert not rank(city_only, location="", desc=DESC + "This role is based in our Bengaluru office.")["excluded"]
    got = rank(city_only, location="", desc=DESC + "This role is based in our Berlin office.")
    assert got["excluded"] and got["reasons"] == ["doesn't say where it is"]
    assert not rank(city_only, location="", desc="")["excluded"]    # no text yet: listed until the posting is read


def test_a_title_without_a_level_is_judged_by_the_years_it_asks():
    prefs = {"regions": ["city"], "include_senior": True}
    mid = rank(prefs, title="Software Development Engineer", desc=DESC + " 3+ years of software development experience.")
    assert mid["excluded"] and mid["reasons"] == ["below your level"]
    assert not rank(prefs, title="Software Development Engineer", desc=DESC + " 9+ years of experience.")["excluded"]


def test_include_senior_lets_senior_in_and_nothing_below():
    prefs = {"regions": ["city"], "include_senior": True}
    assert not rank(prefs, title="Senior Software Engineer")["excluded"]
    assert rank(prefs, title="Software Development Engineer II")["excluded"]
    assert rank(prefs, title="Associate Software Engineer")["excluded"]


def test_another_kind_of_engineering_is_hidden_unless_a_track_covers_it():
    from app import strategy
    s = strategy.load()
    s["tracks"]["ic_lead"]["also_match"] = ["software engineer", "platform engineer"]   # as real tracks are: no bare "engineer"
    strategy.save(s)
    prefs = {"regions": ["city"], "include_senior": True}
    for title in ("Senior Corporate Security Engineer", "Senior Site Reliability Engineer", "Lead DevOps Engineer",
                  "Senior Compliance Engineer", "Lead Network Engineer"):
        got = rank(prefs, title=title)
        assert got["excluded"] and got["reasons"][0].startswith("another kind of engineering"), title
    assert not rank(prefs, title="Lead Software Engineer")["excluded"]
    assert not rank(prefs, title="Lead Platform Engineer")["excluded"]
    s["tracks"]["ic_lead"]["also_match"] += ["security engineer"]
    strategy.save(s)
    assert not rank(prefs, title="Senior Corporate Security Engineer")["excluded"]   # your own track: kept


def test_switching_a_region_chip_ranks_nothing_again():
    city, remote = discover.with_profile({"regions": ["city"]}), discover.with_profile({"regions": ["city", "remote"]})
    assert discover.rank_signature(city, EVIDENCE) == discover.rank_signature(remote, EVIDENCE)
    job = {"title": "Lead Software Engineer", "company": "Acme", "location": "Remote", "description": DESC + " Worldwide."}
    ranked = discover.rank(job, EVIDENCE, city)
    assert not ranked["excluded"] and ranked["chip"] == "remote"
    assert discover.apply_regions(ranked, {"city"})["excluded"] and not discover.apply_regions(ranked, {"remote"})["excluded"]
