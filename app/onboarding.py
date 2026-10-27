"""A new profile, from a CV.

1. Read the file (PDF, Word or text) into text.
2. The model structures it into the master profile. Code then checks it against the CV's own text: every number in a
   bullet must be in the CV, every company and degree must be named in it. Anything that isn't is flagged for the
   person to look at, never silently kept.
3. The model reads the whole profile and works out the strategy: the person's field and level, how careers like theirs
   progress, the kinds of role to aim at, the words that find those roles, the employers worth watching and the
   searches worth running. That strategy (strategy.py) drives Tailor, Discover, Interview, Cold mail and Career.
"""
import base64
import concurrent.futures as cf
import contextvars
import datetime as dt
import io
import json
import re
import zipfile

from . import discover, profiles, strategy
from .engines import ENGINES, EngineError, parse_json_reply
from .profile import experience_months, load_profile, original_cv_path, save_profile

MAX_BYTES = 8 * 1024 * 1024


# ---------- 1. the file ----------

def read_cv(filename: str, data_b64: str) -> str:
    try:
        raw = base64.b64decode(data_b64, validate=False)
    except Exception as e:
        raise ValueError("The file didn't upload properly. Try again.") from e
    if len(raw) > MAX_BYTES:
        raise ValueError("That file is over 8 MB. A CV should be far smaller; export it again as a PDF.")
    name = (filename or "").lower()
    if name.endswith(".pdf") or raw[:5] == b"%PDF-":
        from pypdf import PdfReader
        try:
            text = "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(raw)).pages)
        except Exception as e:
            raise ValueError("Couldn't read that PDF. If it's a scan, paste the CV text instead.") from e
    elif name.endswith(".docx") or raw[:2] == b"PK":
        try:
            xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode("utf-8", "replace")
        except Exception as e:
            raise ValueError("Couldn't read that Word file. Save it as .docx or PDF, or paste the text.") from e
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        text = re.sub(r"&amp;", "&", re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", text)))
    elif name.endswith((".doc", ".rtf", ".odt", ".pages")):
        raise ValueError("Save the CV as PDF or .docx first (File > Save As), or paste its text.")
    else:
        text = raw.decode("utf-8", "replace")
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r", ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 300:
        raise ValueError("That CV has almost no readable text (a scanned image, perhaps). Paste its text instead.")
    return text


# ---------- 2. the profile ----------

PARSE = """You are reading one person's CV to build their master profile: the only source of facts that every later CV,
cover letter and email may draw on. Accuracy matters far more than polish.

Rules:
- Copy facts from the CV; never add, infer or improve them. Keep every number, name, tool and date exactly as written.
- Each bullet is one achievement or duty from the CV, kept in the CV's own words (you may fix line-break damage and
  split a run-on paragraph into separate bullets). Do not rewrite, merge, embellish or add metrics.
- Dates as "YYYY-MM"; if the CV gives only a year, "YYYY". The current job has "end": null. Roles in the CV's order,
  newest first. A role the CV marks as internship, trainee or apprenticeship gets "internship": true and "optional": true.
- If one employer lists several positions in a row, make one role per position.
- "client": the end client when the CV says the person worked on-site or on a project for a client of their employer
  (for example an IT-services or consulting company placing someone at a client); else "". "context": one line the CV
  gives about that engagement or the employer, if any; else "".
- "skills": group the skills section into 3 to 8 labelled groups as the CV does; if the CV has no skills section, build
  groups only from tools, methods and domains named in the experience. Never add a skill that is not in the CV.
- Leave a field "" (or [] / null) when the CV doesn't say; never guess a phone number, email, LinkedIn or location.
- "headline": the person's current title as the CV states it (or its top line). "summary": the CV's own summary,
  verbatim; if it has none, write 2 plain sentences from the CV's facts only.
- "projects": only side projects, independent products or notable projects the CV lists on their own, not employer duties.
- "languages": spoken languages only, if listed; "level" only if the CV gives one.

Return one JSON object only:
{"basics": {"name": "", "headline": "", "email": "", "phone": "", "location": "City, Country", "linkedin": "domain/path, no https://",
            "nationality": ""},
 "summary": "",
 "experience": [{"title": "", "company": "", "client": "", "context": "", "location": "", "start": "YYYY-MM", "end": "YYYY-MM or null",
                 "internship": false, "optional": false, "stack": ["tools and methods used in this role, from the CV"],
                 "bullets": ["..."]}],
 "education": [{"degree": "", "institution": "", "location": "", "year": "graduation year", "details": ["honours, thesis, activities as listed"],
                "optional": false}],
 "certifications": [{"name": "", "issuer": "", "detail": ""}],
 "languages": [{"name": "", "level": ""}],
 "skills": {"Group label": ["skill"]},
 "projects": [{"name": "", "role": "", "url": "", "start": "YYYY-MM", "end": "YYYY-MM or null", "stack": [""], "bullets": [""]}],
 "interests": ""}
Mark school-level entries (Class X, Class XII, high school) "optional": true.

THE CV:
<<<
%s
>>>"""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:14] or "x"


def _ym(v) -> str | None:
    if v in (None, "", "null") or str(v).lower() in ("present", "current", "now", "till date", "to date"):
        return None
    m = re.match(r"(\d{4})(?:[-/](\d{1,2}))?", str(v).strip())
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m.group(2) and 1 <= int(m.group(2)) <= 12 else m.group(1)


def _str(v) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()


def _list(v) -> list:
    return v if isinstance(v, list) else []


def build_profile(raw: dict) -> dict:
    """The model's structured reading, cleaned into the master profile's shape. Ids are made here, not by the model."""
    b = raw.get("basics") or {}
    basics = {k: _str(b.get(k)) for k in ("name", "headline", "email", "phone", "location", "linkedin", "nationality")}
    basics["linkedin"] = re.sub(r"^https?://(www\.)?", "", basics["linkedin"]).rstrip("/")
    basics["availability"] = ""
    experience, used = [], set()
    for r in _list(raw.get("experience")):
        if not isinstance(r, dict) or not (_str(r.get("title")) or _str(r.get("company"))):
            continue
        rid, n = _slug(r.get("company")) or "role", 2
        base = rid
        while rid in used:
            rid, n = f"{base}-{n}", n + 1
        used.add(rid)
        bullets = [{"id": f"{rid}-{i}", "text": _str(t if isinstance(t, str) else (t or {}).get("text"))}
                   for i, t in enumerate(_list(r.get("bullets")), 1)]
        role = {"id": rid, "title": _str(r.get("title")), "company": _str(r.get("company")), "client": _str(r.get("client")),
                "context": _str(r.get("context")), "location": _str(r.get("location")),
                "start": _ym(r.get("start")) or "", "end": _ym(r.get("end")),
                "stack": [_str(x) for x in _list(r.get("stack")) if _str(x)],
                "bullets": [x for x in bullets if x["text"]]}
        if r.get("internship"):
            role["internship"] = role["optional"] = True
        experience.append({k: v for k, v in role.items() if v not in ("", [], None) or k in ("end", "bullets", "start")})
    edu = []
    for i, e in enumerate(_list(raw.get("education")), 1):
        if isinstance(e, dict) and _str(e.get("degree")):
            row = {"id": f"edu{i}", "degree": _str(e.get("degree")), "institution": _str(e.get("institution")),
                   "location": _str(e.get("location")), "year": _str(e.get("year")),
                   "details": [_str(d) for d in _list(e.get("details")) if _str(d)]}
            if e.get("optional") or re.search(r"class\s*(x|xii|10|12)|high school|secondary|\bhsc\b|\bssc\b", row["degree"], re.I):
                row["optional"] = True
            edu.append(row)
    certs = [{"id": f"cert{i}", "name": _str(c.get("name")), "issuer": _str(c.get("issuer")), "detail": _str(c.get("detail"))}
             for i, c in enumerate(_list(raw.get("certifications")), 1) if isinstance(c, dict) and _str(c.get("name"))]
    langs = [{"name": _str(l.get("name")), **({"level": _str(l["level"])} if _str(l.get("level")) else {})}
             for l in _list(raw.get("languages")) if isinstance(l, dict) and _str(l.get("name"))]
    skills = {_str(k): [_str(x) for x in v if _str(x)] for k, v in (raw.get("skills") or {}).items()
              if isinstance(v, list) and any(_str(x) for x in v)} if isinstance(raw.get("skills"), dict) else {}
    projects = []
    for i, p in enumerate(_list(raw.get("projects")), 1):
        if isinstance(p, dict) and _str(p.get("name")):
            projects.append({"id": f"proj{i}", "name": _str(p.get("name")), "role": _str(p.get("role")), "url": _str(p.get("url")),
                             "start": _ym(p.get("start")) or "", "end": _ym(p.get("end")),
                             "stack": [_str(x) for x in _list(p.get("stack")) if _str(x)],
                             "bullets": [{"id": f"proj{i}-{j}", "text": _str(t if isinstance(t, str) else (t or {}).get("text"))}
                                         for j, t in enumerate(_list(p.get("bullets")), 1) if _str(t if isinstance(t, str) else (t or {}).get("text"))]})
    profile = {"_note": "Master profile, read from the CV. Every tailored CV is built only from what is written here. "
                        "Edit anything wrong; add what is missing.",
               "basics": basics, "summary": _str(raw.get("summary")), "experience": experience, "education": edu,
               "certifications": certs, "languages": langs, "skills": skills, "confirmed_skills": [],
               "interests": _str(raw.get("interests")), "projects": projects}
    if not basics["name"] or not experience:
        raise EngineError("Couldn't find a name and work experience in that CV. Is it the right file?")
    return profile


def check_against_cv(profile: dict, cv_text: str) -> list[dict]:
    """Flags for the review screen: anything in the profile that the CV's own text doesn't back up."""
    flat = re.sub(r"\s+", " ", cv_text).lower()
    squeezed = re.sub(r"[\s,]", "", cv_text).lower()
    flags = []
    add = lambda level, text: flags.append({"level": level, "text": text})
    num = re.compile(r"(?<![A-Za-z0-9.])\d[\d,]*(?:\.\d+)?")
    for r in profile["experience"]:
        for token in (r["company"], r["title"]):
            if token and re.sub(r"\s+", " ", token).lower() not in flat:
                add("warn", f"\"{token}\" isn't written like that in the CV (role at {r['company']}). Check it.")
        for b in r["bullets"]:
            missing = [n for n in num.findall(b["text"]) if n.replace(",", "") not in squeezed]
            if missing:
                add("error", f"{r['company']}: a bullet has numbers the CV doesn't show ({', '.join(missing)}): \"{b['text'][:80]}...\"")
        if not r["bullets"]:
            add("warn", f"No bullets were found for {r['title']} at {r['company']}. Add them so CVs can use that role.")
        if not r["start"]:
            add("error", f"{r['title']} at {r['company']} has no start date. Dates drive your years of experience.")
    for e in profile["education"]:
        if e["institution"] and re.sub(r"\s+", " ", e["institution"]).lower() not in flat:
            add("warn", f"\"{e['institution']}\" isn't written like that in the CV.")
    b = profile["basics"]
    for key, label in (("email", "email"), ("phone", "phone number"), ("location", "location")):
        if not b[key]:
            add("warn", f"No {label} found in the CV. It prints on every CV, so add it.")
    return flags


# ---------- 3. the strategy ----------

ANALYSE = """You are a career strategist and senior recruiter. Read this person's whole profile and work out how to
search for their next job and how to write every CV they send. You work for the person, so be honest about their level.

TODAY: {today}. TOTAL PAID EXPERIENCE (computed from dates): {years} years. BASED IN: {home}.
WHAT THE PERSON SAYS THEY WANT NEXT: {wants}

The goal is callbacks, not application volume. A screener checks title history, years and location before anything
else, so a cold application only converts for a role the person has held, at their level, where they can work.

Research on the web before you answer: how careers in this field progress from this person's level in {country}
(the titles at each step and how long people stay at each); which kinds of role this profile can realistically move to
next, including adjacent fields; which employers hire people like this, in {city} and elsewhere in {country} (include
the person's own industry's biggest employers, product companies, and global capability centres where they exist),
and which hire full-time employees remote from {country} (their own entity there, or an employer of record); what
each route pays at the person's level (in {city}, remote from {country}, and abroad if they want to move); and which
job titles and keywords recruiters use for these roles today.

THE PROFILE (every fact is true; nothing else is):
{profile}

Return one JSON object only:
{{
 "field": "the person's professional field in a few words, e.g. 'Business analysis in digital banking'",
 "industries": ["2 to 4 industries/domains their experience sits in"],
 "job_city": "the one city to search jobs in: where they work, or the nearest major employment hub if they live in a small town; just the city name",
 "summary": "2 or 3 plain sentences: who this person is professionally, and their strongest levers",
 "progression": "the realistic ladder from their current title upward in {country}, as 'Title > Title > Title', 4 to 6 steps, starting at their current title",
 "level": {{"rank": 0-7, "label": "", "current_title": "their current title, copied from the profile",
            "headline_word": "the seniority word their headline should always carry (e.g. 'Lead'), or '' when the title doesn't have one worth enforcing",
            "people_manager": false,
            "services_title": true when the current employer is an IT-services or consulting firm whose titles product companies read one level lower (a services "Lead" is usually a product company's "Senior")}},
 "pay_targets": {{"home": "what this level pays in {city}, as a range with its source", "remote": "what full-time remote from {country} pays, with its source", "abroad": "what the target countries pay, if the person wants to move"}},
 "title_levels": [{{"phrase": "a title word that means a different level in THIS field", "rank": 0-7}}],
 "headline_never": ["title words the person has NOT held and a headline must never use, e.g. 'Director', 'VP', 'Head', 'Manager' when they have never managed"],
 "tracks": {{
   "track_id": {{"label": "short name of this kind of target role",
                "brief": "3 to 6 sentences telling the CV writer what to lead with for this target, using ONLY real evidence from the profile (name the actual employers, systems, clients and numbers to lead with), what hiring managers for this role screen for, and what the CV must never claim because the profile doesn't show it",
                "headline_examples": ["2 or 3 headlines of at most 5 words that this person could honestly carry for this target"],
                "titles": ["1 to 3 exact job titles to search job boards for"],
                "match": ["plain title phrases (lowercase) that mean a job posting IS this track"],
                "also_match": ["extra plain title phrases that count as this track in job search only"],
                "lateral": true for a move into a different kind of role, false for the same kind of role,
                "status": "primary | secondary | parked"}}
 }},
 "match_order": ["track ids, most specific first, the generic track last"],
 "default_track": "the track id used when a job title matches none",
 "discover": {{"role_words": ["plain title words/phrases that mark a posting as the kind of job this person might want at all, wide: e.g. 'analyst', 'product', 'consultant'"],
              "exclude_title_words": ["title words that mean it is NOT their kind of job (other professions, sales, interns above their level, etc.)"],
              "lateral_words": ["title phrases for sideways moves into other fields"]}},
 "terms": {{"Category name": {{"Canonical skill or term": ["aliases as job posts write them"]}}}},
 "companies": [{{"name": "employer name as it appears on its careers page", "why": "one line: why it hires people like this", "kind": "product | bank | fintech | consulting | services | gcc | startup",
                "pay_tier": "top | good | unknown: how it pays at this level against the market, from public pay data",
                "hires_where": "the evidence it hires in {city}, in {country}, or remote from {country}"}}],
 "searches": [{{"query": "a web-search query that finds fresh openings for this person (title, city, domain words)", "why": "one line"}}],
 "career_wants": "restate in 1 or 2 sentences what the person wants next, adding the strongest routes you found if they didn't say",
 "cv_notes": "optional extra writing guidance specific to this person: which employer's work to frame how (e.g. when a role is a client engagement, lead with the client's domain), what to downplay, how the headline should read. Facts only from the profile."
}}

Rules:
- rank scale: 0 intern, 1 junior/associate, 2 mid-level, 3 senior, 4 lead, 5 staff/principal/senior manager, 6 head/director, 7 VP/C-level.
  Use the person's years and titles; note that in banks, consultancies and some services firms "Associate" or "Analyst" mean different ranks than in product companies. Use title_levels for those words.
- 3 to 6 tracks, each with a status. At most 3 are active ("primary" or "secondary"): roles the person has held or
  has direct evidence for, at their level. Anything that needs a title, years or direct reports the profile doesn't
  show (a first manager or product manager role, staff/principal, head/VP) is "parked": reachable through a referral or
  an internal move, not a cold application. Every track's match and titles must be plain phrases job boards use.
- A title word listed in discover.exclude_title_words must not be one an active track searches for.
- Remote: only employers that hire full-time employees remote from {country}; never contract or freelance platforms.
- "terms": 20 to 60 skills, tools, methods and domain words that job descriptions in this field use and that matter to the
  profile (include the profile's own). Plain words only, no regexes. Group by category.
- companies: 20 to 30 real employers, ordered by fit. Include the person's current employer only if people there commonly move internally.
- searches: 3 to 5, written as a person would type them into a search engine, with the city.
- Never invent facts about the person. Tracks may aim above the person's level, but the brief must say what they cannot claim.
- Never guess the person's gender from their name. Refer to them by name or as "they", in every text field.
"""


def _validated(raw: dict, profile: dict) -> dict:
    """Strategy JSON from the model, cleaned so the rest of the app can trust its shape."""
    def strs(v, n=40):
        return [_str(x) for x in _list(v) if isinstance(x, str) and _str(x)][:n]

    tracks = {}
    for tid, t in (raw.get("tracks") or {}).items():
        if not isinstance(t, dict):
            continue
        tid = re.sub(r"[^a-z0-9_]+", "_", str(tid).lower()).strip("_")[:30] or f"track{len(tracks) + 1}"
        match = strs(t.get("match"), 30)
        titles = strs(t.get("titles"), 4)
        if not (t.get("label") and (match or titles)):
            continue
        tracks[tid] = {"label": _str(t["label"]), "brief": _str(t.get("brief")),
                       "headline_examples": strs(t.get("headline_examples"), 4), "titles": titles or [_str(t["label"])],
                       "match": match or titles, "also_match": strs(t.get("also_match"), 30), "lateral": bool(t.get("lateral")),
                       "status": t.get("status") if t.get("status") in ("primary", "secondary", "parked") else "primary"}
    if not tracks:
        raise EngineError("The analysis came back without any career tracks. Run it again.")
    # At most three active tracks: more spreads applications over roles a screener won't call back for.
    for tid in [t for t in tracks if tracks[t]["status"] != "parked"][3:]:
        tracks[tid]["status"] = "parked"
    lv = raw.get("level") if isinstance(raw.get("level"), dict) else {}
    rank = lv.get("rank") if isinstance(lv.get("rank"), int) and 0 <= lv["rank"] <= 7 else 2
    d = raw.get("discover") if isinstance(raw.get("discover"), dict) else {}
    current = next((r for r in profile["experience"] if not r.get("end")), profile["experience"][0])
    terms = {}
    for cat, entries in (raw.get("terms") or {}).items():
        if isinstance(entries, dict):
            terms[_str(cat)[:40]] = {_str(k)[:60]: strs(v, 8) for k, v in entries.items() if _str(k)}
    companies, seen = [], set()
    for c in _list(raw.get("companies")):
        if isinstance(c, dict) and _str(c.get("name")) and _str(c["name"]).lower() not in seen:
            seen.add(_str(c["name"]).lower())
            companies.append({"name": _str(c["name"]), "why": _str(c.get("why")), "kind": _str(c.get("kind")),
                              "pay_tier": _str(c.get("pay_tier")) if _str(c.get("pay_tier")) in ("top", "good") else "",
                              "hires_where": _str(c.get("hires_where"))})
    default = raw.get("default_track") if raw.get("default_track") in tracks else next(iter(tracks))
    return {
        "version": 1, "generated_at": dt.datetime.now().isoformat(timespec="seconds"), "source": "read from the CV",
        "job_city": _str(raw.get("job_city"))[:40],
        "field": _str(raw.get("field")), "industries": strs(raw.get("industries"), 5), "summary": _str(raw.get("summary")),
        "progression": _str(raw.get("progression")),
        "level": {"rank": rank, "label": (_str(lv.get("label")) if 0 < len(_str(lv.get("label"))) <= 28 else strategy.LEVELS[rank]),
                  "current_title": _str(lv.get("current_title")) or current["title"],
                  "headline_word": _str(lv.get("headline_word")), "people_manager": bool(lv.get("people_manager")),
                  **({"services_title": bool(lv["services_title"])} if isinstance(lv.get("services_title"), bool) else {})},
        "pay_targets": {k: _str(v) for k, v in (raw.get("pay_targets") or {}).items() if k in ("home", "remote", "abroad")}
        if isinstance(raw.get("pay_targets"), dict) else {},
        "title_levels": [{"phrase": _str(x["phrase"]), "rank": x["rank"]} for x in _list(raw.get("title_levels"))
                         if isinstance(x, dict) and _str(x.get("phrase")) and isinstance(x.get("rank"), int) and 0 <= x["rank"] <= 7],
        "headline_never": strs(raw.get("headline_never"), 15), "tracks": tracks,
        "match_order": [t for t in strs(raw.get("match_order"), 12) if t in tracks], "default_track": default,
        "discover": {"role_words": strs(d.get("role_words"), 60), "exclude_title_words": strs(d.get("exclude_title_words"), 60),
                     "lateral_words": strs(d.get("lateral_words"), 30)},
        "terms": terms, "companies": companies[:30],
        "searches": [{"query": _str(s["query"]), "why": _str(s.get("why"))} for s in _list(raw.get("searches"))
                     if isinstance(s, dict) and _str(s.get("query"))][:5],
        "career_wants": _str(raw.get("career_wants")), "cv_notes": _str(raw.get("cv_notes")),
    }


def analyse(engine_id: str, wants: str = "", progress=lambda m: None) -> dict:
    """Read the saved profile and write this profile's strategy.json."""
    profile = load_profile()
    home = strategy.home()
    facts = {k: profile[k] for k in ("summary", "experience", "education", "certifications", "projects", "skills") if k in profile}
    brief = ANALYSE.format(today=dt.date.today().isoformat(), years=experience_months(profile) // 12,
                           home=f"{home['city']}, {home['country']}".strip(", ") or "not stated", country=home["country"] or "their country",
                           city=home["city"] or "their city", wants=wants.strip() or "not stated: propose what fits",
                           profile=json.dumps(facts, ensure_ascii=False)[:30000])
    engine = ENGINES[engine_id]
    progress("Researching how careers like yours progress, who hires, and what to search for (2 to 4 minutes)")
    try:
        reply = engine.run(brief, timeout=600, tools=["WebSearch"] if engine_id.startswith("claude") else None)
    except TypeError:
        reply = engine.run(brief, timeout=600)
    try:
        raw = parse_json_reply(reply)
    except EngineError:
        progress("The reply wasn't clean JSON; asking once more")
        raw = parse_json_reply(engine.run(brief + "\n\nYour previous reply was not valid JSON. Return only the JSON object.", timeout=600))
    progress("Checking the strategy")
    s = _validated(raw, profile)
    if wants.strip():
        s["career_wants"] = wants.strip() + ("\n" + s["career_wants"] if s["career_wants"] else "")
    strategy.save(s)
    return s


def seed_sources(progress=lambda m: None) -> dict:
    """Turn the strategy's employers and searches into Discover sources: employers with a public job board become
    favourites; the rest wait in the strategy as suggestions (one click to add); searches become web-search sources."""
    s = strategy.load()
    added_fav, added_search = [], []
    companies = s.get("companies") or []
    progress(f"Looking for the job boards of {len(companies[:20])} employers")
    ctx = lambda fn, *a: contextvars.copy_context().run(fn, *a)
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        boards = list(pool.map(lambda c: ctx(_board, c["name"]), companies[:20]))
    for c, board in zip(companies[:20], boards):
        if board:
            try:
                discover.add_favourite(c["name"])
                c["added"] = True
                added_fav.append(c["name"])
            except ValueError:
                pass
    for q in s.get("searches") or []:
        try:
            discover.add_source(q["query"], "websearch")
            added_search.append(q["query"])
        except ValueError:
            pass
    s["companies"] = companies
    strategy.save(s)
    return {"favourites": added_fav, "searches": added_search}


def _board(name: str):
    try:
        return discover.find_board(name)
    except Exception:
        return None


# ---------- the whole run ----------

def run(pid: str, cv_text: str, name: str, wants: str, engine_id: str, progress=lambda m: None) -> dict:
    """Runs as the new profile (the job's thread inherited it): parse, save, analyse, seed Discover."""
    engine = ENGINES[engine_id]
    progress("Reading your CV and building your profile")
    brief = PARSE % cv_text[:40000]
    try:
        raw = parse_json_reply(engine.run(brief, timeout=420))
    except EngineError:
        progress("The reply wasn't clean JSON; asking once more")
        raw = parse_json_reply(engine.run(brief + "\n\nYour previous reply was not valid JSON. Return only the JSON object.", timeout=420))
    profile = build_profile(raw)
    profile["preferences"] = {"defaults": {"engine": engine_id}}
    save_profile(profile)
    original_cv_path().write_text(cv_text, "utf-8")
    flags = check_against_cv(profile, cv_text)
    profiles.update(pid, name=profile["basics"]["name"] or name, status="reading")
    progress(f"Read {len(profile['experience'])} roles, {sum(len(r['bullets']) for r in profile['experience'])} bullets "
             f"and {sum(len(v) for v in profile['skills'].values())} skills")
    analyse(engine_id, wants, progress)
    progress("Setting up Discover with employers and searches for you")
    seeded = seed_sources(progress)
    profiles.update(pid, status="review")
    return {"profile_id": pid, "flags": flags, "strategy": strategy.summary(), "seeded": seeded}
