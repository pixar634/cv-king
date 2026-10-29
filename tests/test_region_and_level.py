"""Discover shows only the regions you picked: places you'd move to narrow "Rest of the world" and never override it,
a listing with no location is placed by its own text, and a title with no level word is judged by the years asked."""
from app import discover

DESC = ("You will build product features in Python and React, own FastAPI services end to end, design APIs "
        "and review code on a product team. ") * 4
EVIDENCE = {"Python", "React", "FastAPI"}


def rank(prefs, title="Lead Software Engineer", location="Bengaluru, India", desc=DESC):
    job = {"title": title, "company": "Acme", "location": location, "description": desc}
    return discover.rank(job, EVIDENCE, discover.with_profile({"candidate_years": 8} | prefs))


def test_places_show_only_with_rest_of_the_world_on():
    city_only = {"regions": ["city"], "places": ["Germany"]}
    assert not rank(city_only)["excluded"]
    assert rank(city_only, location="Berlin, Germany")["excluded"]
    world = {"regions": ["city", "world"], "places": ["Germany"]}
    assert not rank(world, location="Berlin, Germany")["excluded"]
    assert rank(world, location="London, United Kingdom")["excluded"]          # abroad, but not one of your places
    assert not rank({"regions": ["world"]}, location="London, United Kingdom")["excluded"]   # no places: every target


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
