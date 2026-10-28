"""Who to ask first at a company, for one role: the person's level and the help they can give."""
from app import warm

CSV = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Ann,A,https://www.linkedin.com/in/ann,,Okta,Account Executive,01 Jan 2024
Rec,R,https://www.linkedin.com/in/rec,,Okta,Senior Technical Recruiter,01 Jan 2024
Pat,P,https://www.linkedin.com/in/pat,,Okta,Senior Software Engineer,01 Jan 2024
Mia,M,https://www.linkedin.com/in/mia,,Okta,Engineering Manager - Platform,01 Jan 2024
Dan,D,https://www.linkedin.com/in/dan,,Okta,Director of Marketing,01 Jan 2024
"""


def test_most_useful_first_for_the_role():
    warm.import_csv(CSV)
    got = warm.who_to_ask("Okta, Inc.", "Senior Software Engineer, Platform")
    assert [p["name"] for p in got] == ["Mia M", "Pat P", "Rec R", "Dan D", "Ann A"]
    assert [p["ask"] for p in got] == ["hiring_manager", "referral", "recruiter", "intro", "intro"]
    assert got[1]["level"] == "Senior" and got[3]["level"] == "Head / director"
    assert "hiring manager" in got[0]["why"]


def test_limit_and_unknown_company():
    warm.import_csv(CSV)
    assert len(warm.who_to_ask("Okta", "Engineer", 2)) == 2
    assert warm.who_to_ask("Initech", "Engineer") == []
