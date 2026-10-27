"""What this candidate is aiming at, and the rules that follow from it. One file per profile (strategy.json),
written by the onboarding analysis from the CV, or by hand. Every rule that used to be fixed for one person
reads from here: the career tracks and their writing briefs, which job titles count as theirs, their level and
what the headline may say, words that rule a posting out, extra skill terms for their field, and their city.

A profile without a strategy still works: it gets one track built from its current title until the CV is read.
"""
import json
import re
from functools import lru_cache

from .config import data_dir

FILE = "strategy.json"

# The ladder every title is read against. Fields with their own conventions (banks' AVP and VP) override it
# with "title_levels" in the strategy.
LEVELS = {0: "Intern / trainee", 1: "Junior / associate", 2: "Mid-level", 3: "Senior", 4: "Lead",
          5: "Staff / principal / senior manager", 6: "Head / director", 7: "VP / C-level"}
LEVEL_WORDS = [
    (7, r"(?<!assistant )(?<!associate )\b(vp|svp|evp|vice[\s-]president|chief\s+\w+\s+officer|c[tpfoi]o)\b"),
    (6, r"\b(head of|director)\b"),
    (5, r"\b(staff|principal|distinguished|group (product|engineering) manager|senior manager|sr\.? manager)\b"),
    (4, r"\b(lead|team lead|tech(nical)? lead|avp|(assistant|associate) vice[\s-]president)\b"),
    (3, r"\b(senior|sr\.?)\b|\b(sde|swe|engineer|developer|analyst)[\s-]*(iii|3)\b"),
    (2, r"\bmid[\s-]*level\b|\b(sde|swe|engineer|developer|analyst)[\s-]*(ii|2)\b"),
    (1, r"\b(junior|jr\.?|associate|entry[\s-]level|graduate)\b|\b(sde|swe|engineer|developer|analyst)[\s-]*(i|1)\b"),
    (0, r"\b(intern|internship|trainee|apprentice|fresher)\b"),
]
# Words that never name a level on their own: "Associate Director" is a director, not an associate.
_HIGHER_FIRST = re.compile(r"\bassociate (director|partner)\b", re.I)

CITY_ALIASES = [("bengaluru", "bangalore", "blr"), ("gurugram", "gurgaon"), ("mumbai", "bombay"), ("chennai", "madras"),
                ("kolkata", "calcutta"), ("thiruvananthapuram", "trivandrum"), ("kochi", "cochin"), ("pune",),
                ("hyderabad", "secunderabad"), ("noida", "greater noida"), ("delhi", "new delhi", "ncr")]
INDIAN_STATES = {"karnataka", "maharashtra", "tamil nadu", "telangana", "andhra pradesh", "kerala", "gujarat", "rajasthan", "west bengal",
                 "uttar pradesh", "madhya pradesh", "haryana", "punjab", "odisha", "bihar", "assam", "goa", "delhi", "jharkhand",
                 "chhattisgarh", "uttarakhand", "himachal pradesh", "chandigarh", "sikkim", "tripura", "manipur", "meghalaya"}
COUNTRY_CODES = {"india": "IN", "united kingdom": "GB", "uk": "GB", "united states": "US", "usa": "US", "canada": "CA",
                 "singapore": "SG", "united arab emirates": "AE", "uae": "AE", "australia": "AU", "germany": "DE",
                 "netherlands": "NL", "ireland": "IE", "france": "FR"}


def phrase_regex(phrase: str) -> str:
    """'full stack' -> a boundary-aware pattern that also matches 'full-stack'; 're:...' is a raw regex."""
    phrase = (phrase or "").strip()
    if phrase.startswith("re:"):
        return phrase[3:]
    body = r"[\s\-]*".join(re.escape(p) for p in re.split(r"[\s\-]+", phrase) if p)
    return r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"


def any_of(phrases: list[str]) -> re.Pattern | None:
    pats = [phrase_regex(p) for p in phrases or [] if p and p.strip()]
    if not pats:
        return None
    try:
        return re.compile("|".join(f"(?:{p})" for p in pats), re.I)
    except re.error:
        safe = [phrase_regex(p) for p in phrases if p and p.strip() and not p.startswith("re:")]
        return re.compile("|".join(f"(?:{p})" for p in safe), re.I) if safe else None


# ---------- loading ----------

def path():
    return data_dir() / FILE


def exists() -> bool:
    return path().exists()


@lru_cache(maxsize=16)
def _read(p: str, mtime: float) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load() -> dict:
    p = path()
    if p.exists():
        return _read(str(p), p.stat().st_mtime)
    return fallback()


def save(strategy: dict) -> None:
    p = path()
    if p.exists():
        p.with_name("strategy.prev.json").write_text(p.read_text("utf-8"), "utf-8")
    p.write_text(json.dumps(strategy, indent=2, ensure_ascii=False), "utf-8")


def fallback() -> dict:
    """Until the CV has been read: one track from the current title, the level its title words give."""
    from .profile import load_profile
    try:
        profile = load_profile()
    except FileNotFoundError:
        return {"tracks": {}, "level": {"rank": 2, "label": LEVELS[2]}}
    current = next((r for r in profile["experience"] if not r.get("end")), (profile["experience"] or [{}])[0])
    title = current.get("title") or profile["basics"].get("headline") or "Professional"
    rank = title_level(title, {}) or 2
    function = re.sub(r"(?i)\b(junior|jr\.?|associate|senior|sr\.?|lead|principal|staff|mid[\s-]*level|i{1,3})\b", "", title).strip(" ,-")
    return {
        "field": function, "level": {"rank": rank, "label": LEVELS[rank], "current_title": title, "headline_word": ""},
        "tracks": {"main": {"label": function or title, "brief": f"Target: {function or title} roles at the candidate's level.",
                            "headline_examples": [profile["basics"].get("headline") or title], "titles": [function or title],
                            "match": [function] if function else []}},
        "default_track": "main", "fallback": True,
    }


# ---------- tracks ----------

def tracks() -> dict:
    return load().get("tracks") or {}


def default_track() -> str:
    s = load()
    t = tracks()
    return s.get("default_track") if s.get("default_track") in t else next(iter(t), "main")


def track(tid: str) -> dict:
    t = tracks()
    return t.get(tid) or t.get(default_track()) or {"label": "General", "brief": "", "headline_examples": []}


@lru_cache(maxsize=256)
def _pattern(key: str, phrases: tuple) -> re.Pattern | None:
    return any_of(list(phrases))


def track_pattern(tid: str, wide: bool = False) -> re.Pattern | None:
    """`match` picks the track when tailoring; `also_match` adds titles that only count as this track in Discover."""
    t = tracks().get(tid) or {}
    words = (t.get("match") or []) + ((t.get("also_match") or []) if wide else [])
    return _pattern(f"{tid}:{wide}", tuple(words))


def track_for(title: str) -> str:
    """The career track a job title implies, for the 'auto' setting: the first track (in match order) whose
    title phrases appear in it, else the default track."""
    s = load()
    order = [t for t in (s.get("match_order") or []) if t in tracks()] + [t for t in tracks() if t not in (s.get("match_order") or [])]
    for tid in order:
        pat = track_pattern(tid)
        if pat and pat.search(title or ""):
            return tid
    return default_track()


def matching_tracks(title: str, wanted: list[str] | None = None) -> list[str]:
    return [tid for tid in (wanted or list(tracks())) if (p := track_pattern(tid, wide=True)) and p.search(title or "")]


def lateral(title: str) -> bool:
    """A move sideways into another kind of role, where a 'Senior' title is not a step down."""
    t = tracks()
    if any(t[tid].get("lateral") for tid in matching_tracks(title) if tid in t):
        return True
    pat = _pattern("lateral_words", tuple(load().get("discover", {}).get("lateral_words") or []))
    return bool(pat and pat.search(title or ""))


# ---------- level ----------

def level() -> dict:
    lv = dict(load().get("level") or {})
    lv.setdefault("rank", 2)
    lv.setdefault("label", LEVELS.get(lv["rank"], ""))
    lv.setdefault("headline_word", "")
    lv.setdefault("people_manager", False)
    return lv


# IT-services and consulting firms: product companies usually read their "Lead" as a Senior (SDE-2 or SDE-3).
SERVICES = re.compile(
    r"\b(tech mahindra|tcs|tata consultancy|infosys|wipro|hcl\w*|cognizant|accenture|capgemini|ltimindtree|mindtree|"
    r"larsen|l&t|lti|mphasis|hexaware|persistent systems|zensar|birlasoft|coforge|niit tech\w*|mastek|virtusa|"
    r"sonata software|cyient|kpit|ust global|ibm|deloitte|kpmg|pwc|genpact|nagarro|globallogic|epam|dxc|ntt data|"
    r"atos|sopra steria|igate|syntel|happiest minds|tavant|quest global|mathco|mu sigma|fractal analytics|"
    r"tiger analytics|latentview|ey gds|ernst)\b", re.I)


def calibrated_rank(profile: dict | None = None) -> int:
    """Your level as a product company reads it: a services firm's title one step down (Lead reads as Senior).
    The strategy can say so itself (`level.services_title`); otherwise the current employer decides."""
    lv = level()
    if "services_title" in lv:
        services = bool(lv["services_title"])
    else:
        current = next((r for r in (profile or {}).get("experience", []) if not r.get("end")), None)
        services = bool(current and SERVICES.search(current.get("company") or ""))
    return lv["rank"] - 1 if services and lv["rank"] >= 3 else lv["rank"]


def title_level(title: str, strategy: dict | None = None) -> int | None:
    """The seniority a title's words name, or None when it names none ("Business Analyst", "Product Manager")."""
    title = title or ""
    s = load() if strategy is None else strategy
    for row in s.get("title_levels") or []:
        try:
            if re.search(phrase_regex(row["phrase"]), title, re.I):
                return int(row["rank"])
        except (KeyError, ValueError, re.error):
            continue
    if _HIGHER_FIRST.search(title):
        return 6
    for rank, pat in LEVEL_WORDS:
        if re.search(pat, title, re.I):
            return rank
    return None


def headline_never() -> list[str]:
    return [w for w in load().get("headline_never") or [] if w and w.strip()]


# ---------- Discover ----------

def relevant_pattern() -> re.Pattern | None:
    """Titles that are this person's kind of role at all: every track's phrases plus the wider role words."""
    s = load()
    words = list(s.get("discover", {}).get("role_words") or [])
    for t in tracks().values():
        words += (t.get("match") or []) + (t.get("also_match") or [])
        # The search titles without their level word ("Associate Product Manager" also finds "Product Manager I"):
        # whether the level fits is the level check's job, not the relevance test's.
        for title in t.get("titles") or []:
            core = re.sub(r"(?i)(?<![A-Za-z0-9])(junior|jr\.?|associate|senior|sr\.?|lead|principal|staff|i{1,3}|[123])(?![A-Za-z0-9])", " ", title)
            core = re.sub(r"\s+", " ", core).strip(" -,")
            if len(core) > 4:
                words.append(core)
    return _pattern("relevant:" + str(path()), tuple(words))


def excluded_pattern() -> re.Pattern | None:
    s = load()
    words = list(s.get("discover", {}).get("exclude_title_words") or [])
    if level()["rank"] >= 1:
        words += ["intern", "internship", "trainee", "apprentice", "fresher"]
    return _pattern("excluded:" + str(path()), tuple(words))


def search_titles() -> dict[str, list[str]]:
    return {tid: [x for x in (t.get("titles") or []) if x] or [t.get("label", tid)] for tid, t in tracks().items()}


# ---------- where the candidate lives ----------

def home() -> dict:
    """City and country from the profile's location ('Bengaluru, India'), with the city's other spellings."""
    from .profile import load_profile
    try:
        loc = load_profile()["basics"].get("location") or ""
    except FileNotFoundError:
        loc = ""
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    # "Hassan, Karnataka" names a state, not a country; "Pune" names no country at all.
    country = next((p for p in reversed(parts) if p.lower() in COUNTRY_CODES), "")
    if not country:
        country = "India" if not parts or any(p.lower() in INDIAN_STATES for p in parts) or (parts and parts[0].lower() in
                  {n for a in CITY_ALIASES for n in a}) else (parts[-1] if len(parts) > 1 else "")
    # The city to search jobs in: the one the strategy names (the nearest hub to a small town, say), else where they live.
    city = (load().get("job_city") or "").strip() or (parts[0] if parts else "")
    names = next((list(a) for a in CITY_ALIASES if city.lower() in a), [city.lower()] if city else [])
    return {"city": city, "country": country, "city_names": names,
            "country_code": COUNTRY_CODES.get(country.lower(), "IN"),
            "timezone": "IST, UTC+5:30" if country.lower() == "india" else ""}


def city_pattern() -> re.Pattern | None:
    h = home()
    return _pattern("city:" + h["city"], tuple(h["city_names"]))


# ---------- writing ----------

def bullet_counts(profile: dict, one_page: bool) -> list[tuple[dict, str]]:
    """(role, 'n to m' bullets) per role, newest first. The strategy may set its own per role id."""
    s = load()
    own = s.get("bullets_one_page" if one_page else "bullets") or {}
    out, paid = [], 0
    for r in profile["experience"]:
        if r["id"] in own:
            v = own[r["id"]]
            out.append((r, f"{v[0]} to {v[1]}" if isinstance(v, list) else str(v)))
            continue
        if r.get("internship") or r.get("optional"):
            out.append((r, "0" if one_page else "0 to 1"))
            continue
        if one_page:
            n = "3" if paid < 3 else "1"
        else:
            n = "4 to 5" if paid == 0 else "3 to 5" if paid < 3 else "2 to 3"
        out.append((r, n))
        paid += 1
    return out


def extra_terms() -> dict:
    """Skill terms for this person's field the shared taxonomy may lack: {category: {canonical: [aliases]}}."""
    terms = load().get("terms") or {}
    return terms if isinstance(terms, dict) else {}


def summary() -> dict:
    """What the UI shows about the strategy."""
    s = load()
    lv = level()
    return {"field": s.get("field", ""), "industries": s.get("industries") or [], "summary": s.get("summary", ""),
            "progression": s.get("progression", ""), "level": lv, "ready": not s.get("fallback"),
            "generated_at": s.get("generated_at", ""), "companies": s.get("companies") or [],
            "searches": s.get("searches") or [], "career_wants": s.get("career_wants", "")}
