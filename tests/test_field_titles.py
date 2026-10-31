"""A US head-office address is the US, and a title naming another field of engineering stays out even when a track
matches the rest of its words."""
from app import discover

DESC = ("You will build product features in Python and React, own FastAPI services end to end, design APIs "
        "and review code on a product team. ") * 4


def rank(title, location="Bengaluru, India"):
    p = discover.with_profile({"candidate_years": 8, "regions": ["city", "remote", "world"], "include_senior": True})
    return discover.rank({"title": title, "company": "Acme", "location": location, "description": DESC},
                         {"Python", "React", "FastAPI"}, p)


def test_a_state_and_zip_code_is_the_us():
    assert discover.region({"location": "7000 Target Pkwy N,NCD-0375 Brooklyn Park,MN 55445"}) == "us"
    assert discover.region({"location": "Austin, TX 78701-1234"}) == "us"
    assert discover.region({"location": "Pune, IN 411001"}) != "us"         # an Indian PIN has six digits
    assert discover.region({"location": "Bengaluru, Karnataka, India"}) == "home"
    assert rank("Lead Software Engineer", "Brooklyn Park, MN 55445")["excluded"]


def test_another_field_stays_out_when_a_track_matches_only_the_rest():
    got = rank("Lead Engineer - Cloud Security (Security Governance)")
    assert got["excluded"] and got["reasons"][0].startswith("another kind of engineering")
    assert rank("Senior Cloud Engineer")["excluded"]
    assert not rank("Lead Software Engineer")["excluded"]
