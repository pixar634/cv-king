"""Can this application turn into a callback at all? Checked by code before any writing, in well under a second:
level and years, location and work rights, contract work, a parked career track, a start date you can't make.

A screener checks title history, years and location before anything else, so a CV written for a job that fails one
of these is a wasted application. "stop" means a hard screen fails; "stretch" means it may. Both stop the writer
with the reason, and "Tailor anyway" (say, with a referral) goes through.
"""
import re

from . import strategy
from .discover import NO_SPONSOR, SPONSOR, US_ONLY, region
from .fit import level_check, strong_skills
from .profile import experience_months, notice_days, release_note, wants_immediate

# Employment that isn't a full-time job: contracting is out.
CONTRACT = re.compile(
    r"\b(contract|contractor|freelance|freelancer)\s*(role|position|basis|job|engagement|only|opportunity|assignment)\b|"
    r"\b(employment|job|contract|engagement)\s*type\s*:?\s*(contract|freelance|temporary)\b|\bb2b\s*contract\b|"
    r"\b1099\b|\bc2c\b|corp[\s-]*to[\s-]*corp|\bhourly\s*rate\b|\bper\s*hour\b|\bcontract\s*(length|duration)\b|"
    r"\b\d{1,2}[\s-]*months?\s*contract\b|\bfixed[\s-]*term\b", re.I)
# A remote job that only hires where it already is.
RESIDENTS_ONLY = re.compile(
    r"(must|need to|required to|should)\s+(be\s+)?(located|based|resid\w*|living|live)\s+(with)?in\s+(the\s+)?"
    r"(us\b|usa|u\.s\.|united states|uk\b|united kingdom|eu\b|european union|europe|eea|canada|germany|the netherlands|"
    r"netherlands|poland|ireland|france|spain|australia|one of the following)|"
    r"(us|u\.s\.|uk|eu|europe|canada)[\s-]*(based\s+)?(residents?|citizens?|candidates)\s+only|"
    r"(legally\s+)?authori[sz]ed\s+to\s+work\s+in\s+(the\s+)?(us\b|u\.s\.|united states|uk\b|united kingdom|eu\b|canada)|"
    r"right\s+to\s+work\s+in\s+(the\s+)?(uk\b|united kingdom|us\b|united states|eu\b|europe|ireland|canada)|"
    r"anywhere\s+in\s+the\s+(united kingdom|uk\b|united states|us\b)|remote\s*[-–(,]\s*(us|usa|uk|eu|europe|emea|canada)\b|"
    r"(us|uk|eu|emea|europe)[\s-]*(only\s+)?remote\b", re.I)
OPEN_TO_HOME = re.compile(r"worldwide|anywhere in the world|work from anywhere|global(ly)?\s*(remote|team)|"
                          r"\bapac\b|asia|employer of record|\beor\b|\bdeel\b", re.I)


def _home_named(text: str) -> bool:
    from .discover import home_country_pattern
    return bool(home_country_pattern().search(text or ""))


def check(jd_text: str, title: str = "", location: str = "", market: str = "", remote=None,
          profile: dict | None = None, prefs: dict | None = None, track: str = "") -> dict:
    """{"verdict": "ok" | "stretch" | "stop", "reasons": [...]}: the same verdict everywhere a job can be tailored."""
    from .profile import load_profile
    from .tailor import home_market
    profile = profile or load_profile()
    prefs = prefs or profile.get("preferences") or {}
    text = jd_text or ""
    stops, stretches = [], []

    years = experience_months(profile) // 12
    lvl = level_check(title, text, years, strong_skills(profile), prefs.get("max_years_asked", years + 2), profile)
    if lvl["verdict"] == "above":
        stops.append("Above your level: " + "; ".join(lvl["reasons"]))
    elif lvl["verdict"] == "stretch":
        stretches.append("A stretch: " + "; ".join(lvl["reasons"]))

    if m := CONTRACT.search(f"{title}\n{text}"):
        stops.append(f"Contract work, not a full-time job (\"{m.group(0).strip()}\")")

    home = home_market()
    where = region({"location": location, "remote": remote})
    abroad = market not in ("", home, "remote") or where in ("abroad", "us")
    if market == "remote" or where == "remote" or (remote and where != "home"):
        closed = RESIDENTS_ONLY.search(f"{location}\n{text}")
        said = closed.group(0) if closed else location if US_ONLY.search(location or "") else ""
        if said and not OPEN_TO_HOME.search(f"{location}\n{text}") and not _home_named(location):
            stops.append(f"Remote, but not open to where you live (\"{said.strip()}\")")
    elif abroad and not _home_named(location):
        if m := NO_SPONSOR.search(text):
            stops.append(f"A job abroad that won't sponsor a visa (\"{m.group(0).strip()}\")")
        elif (m := RESIDENTS_ONLY.search(text)) and not SPONSOR.search(text):
            stops.append(f"A job abroad for people who already live there (\"{m.group(0).strip()}\")")

    tid = track or strategy.track_for(title)
    t = strategy.tracks().get(tid) or {}
    active = prefs.get("tracks") or list(strategy.tracks())
    if t.get("status") == "parked" or (tid in strategy.tracks() and tid not in active):
        stops.append(f"{t.get('label') or tid} is a parked track: cold applications to it screen out. Apply through a referral")

    days = notice_days(profile)
    if wants_immediate(text) and days and days > 15 and not release_note(profile, text):
        stretches.append(f"Wants someone who can join within about two weeks; you have {days} days' notice")

    verdict = "stop" if stops else "stretch" if stretches else "ok"
    return {"verdict": verdict, "reasons": stops + stretches, "track": tid}


class NotEligible(Exception):
    """Stopped before any writing: the job fails (or may fail) a hard screen. Tailor anyway goes through."""

    def __init__(self, result: dict):
        lead = "Not written: this job fails a screen." if result["verdict"] == "stop" else "Not written yet: this job is a stretch."
        super().__init__(f"{lead} " + " · ".join(result["reasons"]) + ". Have a referral? Tailor it anyway.")
        self.result = result
