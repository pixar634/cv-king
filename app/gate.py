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


# A remote job on US hours: a normal working day at home overlaps European and Asian teams, not American ones.
US_HOURS = re.compile(
    r"(overlap|work|available|online)\w*\s+(with\s+|during\s+|in\s+)?(the\s+)?(us\s+|u\.s\.\s+|north american?\s+)?"
    r"(us|u\.s\.|pst|pt|est|et|cst|pacific|eastern|central)\s*(time\s*zones?|hours|business hours|working hours|time)|"
    r"(pst|est|us|pacific|eastern)\s*(business|working|office)\s*hours|\d{1,2}\s*(am)?\s*(-|–|to)\s*\d{1,2}\s*pm\s*(pst|est|pt|et)\b|"
    r"\bus\s*shift\b|night\s*shift", re.I)
ASYNC = re.compile(r"\basync(hronous)?\b|any\s*time\s*zone|flexible\s*hours", re.I)


def _home_named(text: str) -> bool:
    from .discover import home_country_pattern
    return bool(home_country_pattern().search(text or ""))


def openness(text: str, title: str = "", location: str = "", remote=None, market: str = "") -> dict:
    """Is the job open to someone living at home, on a full-time contract and a normal day there?
    {"state": "open" | "unclear" | "closed", "why": "..."}. Closed is hidden in Discover and stops tailoring."""
    from .tailor import home_market
    text = text or ""
    if m := CONTRACT.search(f"{title}\n{text}"):
        return {"state": "closed", "why": f"Contract work, not a full-time job (\"{m.group(0).strip()}\")"}
    if (m := US_HOURS.search(text)) and not ASYNC.search(text):
        return {"state": "closed", "why": f"Needs US working hours (\"{m.group(0).strip()}\")"}
    where = region({"location": location, "remote": remote})
    if where == "home" or _home_named(location):
        return {"state": "open", "why": "at home"}
    home = home_market()
    both = f"{location}\n{text}"
    if market == "remote" or where == "remote" or (remote and where not in ("abroad", "us")):
        closed = RESIDENTS_ONLY.search(both)
        said = closed.group(0) if closed else location if US_ONLY.search(location or "") else ""
        if OPEN_TO_HOME.search(both) or _home_named(text):
            return {"state": "open", "why": "remote, open to where you live"}
        if said:
            return {"state": "closed", "why": f"Remote, but not open to where you live (\"{said.strip()}\")"}
        return {"state": "unclear", "why": "remote, doesn't say if it hires where you live"}
    if market not in ("", home) or where in ("abroad", "us", "other"):
        if m := NO_SPONSOR.search(text):
            return {"state": "closed", "why": f"A job abroad that won't sponsor a visa (\"{m.group(0).strip()}\")"}
        if SPONSOR.search(text):
            return {"state": "open", "why": "sponsors visas or helps relocate"}
        if m := RESIDENTS_ONLY.search(both):
            return {"state": "closed", "why": f"A job abroad for people who already live there (\"{m.group(0).strip()}\")"}
        if remote or re.search(r"\bremote\b", location or "", re.I):
            return {"state": "closed", "why": "Remote within another country: those teams hire people who already live there"}
        return {"state": "unclear", "why": "abroad, doesn't say if it sponsors a visa"}
    return {"state": "unclear", "why": "location unclear"}


# Languages and the frameworks that define a stack: a job built on one you don't have is a different job.
LANGUAGES = {"Java", "Spring Boot", ".NET", "C#", "Go", "Rust", "Ruby", "Ruby on Rails", "Scala", "C++", "Kotlin",
             "Swift", "PHP", "Python", "TypeScript", "JavaScript", "Node.js"}
# Wording that makes a skill a must-have even where the posting has no "Requirements" heading.
DEMAND = re.compile(r"\b(strong|proven|solid|deep|expert|extensive|hands[\s-]?on|must|required|requirement|"
                    r"proficien\w*|mastery|advanced|\d+\+?\s*(years?|yrs))\b", re.I)
# Wording that offers languages as alternatives: "any of the backend languages (Python, Go)", "Java or Python".
ALTERNATIVE = re.compile(r"\b(or|any of|one of|one or more of|either|such as|e\.g|like|similar|equivalent|at least one)\b|/", re.I)


def stack(text: str, title: str, strong: set[str], prefs: dict) -> dict:
    """The languages and stack frameworks a posting names that aren't on your CV.
    {"missing": [{"name", "must"}], "skipped": [...], "stop": [...], "stretch": [...]}. A posting built on languages
    you don't have (or have said you skip) is a different job, whatever its keyword score."""
    from .discover import needs_skill
    from .keywords import jd_keywords, taxonomy
    text = text or ""
    named = {n: k for n, k in jd_keywords(text).items() if n in LANGUAGES}
    for n in taxonomy().find(title or ""):
        if n in LANGUAGES and n not in named:
            named[n] = None
    sentences = [s for s in re.split(r"(?<=[.;!?])\s+|\n+", text) if s.strip()]
    pats = lambda n: taxonomy().terms[n].patterns
    in_title = set(taxonomy().find(title or ""))
    mine = [n for n in LANGUAGES & set(strong) if n in taxonomy().terms]

    def must(n):
        if n in in_title:
            return True
        said = [s for s in sentences if any(p.search(s) for p in pats(n))]
        # Offered as one of several next to a language you have ("any of Python, Go"): yours meets it.
        alt = [s for s in said if ALTERNATIVE.search(s) and any(p.search(s) for h in mine if h != n for p in pats(h))]
        if said and len(alt) == len(said):
            return False
        k = named[n]
        return k is None or k.tier == "required" or any(DEMAND.search(s) for s in said if s not in alt)
    missing = [{"name": n, "must": must(n)} for n in sorted(named) if n not in strong]
    job = {"title": title or "", "description": text}
    skipped = [s for s in prefs.get("exclude_skills") or [] if needs_skill(job, s)]
    have = [n for n in named if n in strong]
    musts = [m["name"] for m in missing if m["must"]]
    stop, stretch = [], []
    if skipped:
        stop.append(f"Needs {', '.join(skipped)}, which you've chosen to skip (Settings, Skip jobs that need)")
    elif musts and not have:
        stop.append(f"Built on {', '.join(musts)}, which isn't on your CV")
    elif musts:
        stretch.append(f"Also asks for {', '.join(musts)} as a must-have (not on your CV)")
    return {"missing": missing, "skipped": skipped, "stop": stop, "stretch": stretch}


def check(jd_text: str, title: str = "", location: str = "", market: str = "", remote=None,
          profile: dict | None = None, prefs: dict | None = None, track: str = "", company: str = "") -> dict:
    """{"verdict": "ok" | "stretch" | "stop", "reasons": [...]}: the same verdict everywhere a job can be tailored."""
    from .profile import load_profile
    profile = profile or load_profile()
    prefs = prefs or profile.get("preferences") or {}
    text = jd_text or ""
    stops, stretches = [], []

    years = experience_months(profile) // 12
    lvl = level_check(title, text, years, strong_skills(profile), prefs.get("max_years_asked", years + 2), profile, company)
    if lvl["verdict"] == "above":
        stops.append("Above your level: " + "; ".join(lvl["reasons"]))
    elif lvl["verdict"] == "stretch":
        stretches.append("A stretch: " + "; ".join(lvl["reasons"]))

    st = stack(text, title, strong_skills(profile), prefs)
    stops += st["stop"]
    stretches += st["stretch"]

    o = openness(text, title, location, remote, market)
    if o["state"] == "closed":
        stops.append(o["why"])
    elif o["state"] != "open" or o["why"] != "at home":
        from .sponsors import check as sponsor_check
        hit = sponsor_check(company, location)
        if hit and hit["register"] == "uk" and not hit["listed"] and not SPONSOR.search(text):
            stretches.append("Not on the UK register of licensed sponsors, so it can't sponsor a Skilled Worker visa "
                             "unless it gets a licence")

    tid = track or strategy.track_for(title)
    t = strategy.tracks().get(tid) or {}
    active = prefs.get("tracks") or list(strategy.tracks())
    if t.get("status") == "parked" or (tid in strategy.tracks() and tid not in active):
        stops.append(f"{t.get('label') or tid} is a parked track: cold applications to it screen out. Apply through a referral")

    days = notice_days(profile)
    if wants_immediate(text) and days and days > 15 and not release_note(profile, text):
        stretches.append(f"Wants someone who can join within about two weeks; you have {days} days' notice")

    verdict = "stop" if stops else "stretch" if stretches else "ok"
    return {"verdict": verdict, "reasons": stops + stretches, "track": tid, "missing": st["missing"]}


class NotEligible(Exception):
    """Stopped before any writing: the job fails (or may fail) a hard screen. Tailor anyway goes through."""

    def __init__(self, result: dict):
        lead = "Not written: this job fails a screen." if result["verdict"] == "stop" else "Not written yet: this job is a stretch."
        super().__init__(f"{lead} " + " · ".join(result["reasons"]) + ". Have a referral? Tailor it anyway.")
        self.result = result
