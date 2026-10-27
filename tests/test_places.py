"""A country in "Places you'd move to" matches postings that name only one of its cities."""
from app import discover


def pat(*places):
    return discover.places_pattern({"places": list(places)})


def test_country_matches_its_cities_and_spellings():
    assert pat("Germany").search("Berlin") and pat("Germany").search("München, Bavaria")
    assert pat("United Kingdom").search("London, England")
    assert pat("Poland").search("Krakow") and pat("Poland").search("Wrocław")
    assert pat("UK").search("Manchester")   # a two-letter country name counts now


def test_a_city_stays_a_city():
    p = pat("Amsterdam")
    assert p.search("Amsterdam, NL") and not p.search("Rotterdam")


def test_other_countries_dont_match():
    assert not pat("Germany", "Ireland").search("Paris, France")
