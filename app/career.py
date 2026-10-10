"""The career map: your history as code reads it from the profile, and the routes forward the model
proposes after reading all of it. Facts (titles, companies, dates) are never the model's; routes,
levels and what each step takes are its reading of the market, labelled as typical."""
import datetime as dt
import json
import re

from .config import DATA, ROOT
from .engines import get_engine, parse_json_reply
from .fit import strong_skills
from .profile import experience_years_label, fmt_month, load_profile

MAP_FILE = DATA / "career_map.json"

BRIEF = """You are a career strategist who knows how software, product and AI-delivery careers progress in
India, the UK, the EU, the Gulf and Singapore. Read this candidate's whole history and propose the realistic
routes forward over the next five years, starting from their current role.

TODAY: {today}. TOTAL EXPERIENCE (computed from dates): {years} years. CURRENT ROLE: {current}.
THE CANDIDATE WANTS: to grow beyond "Lead engineer" if a better route exists; open to Engineering Manager,
Product Manager (Senior/Lead PM eventually), Forward Deployed / Applied AI, Solutions, Staff/Principal IC, and
role families that are new (Forward Deployed Engineering barely existed three years ago; more will appear).
LONG-TERM GOAL: {goal}
SALARY (yearly, the candidate's own log; growth figures computed by code): {salary}

RESEARCH FIRST. Use web search before you answer: what is trending in 2026 for each route (in India and, where
relevant, the UK, EU, Gulf, Singapore); which certifications employers actually screen for at each step (e.g. AWS or
Azure certifications for solutions and cloud-heavy routes, product certifications for PM, none where the market
doesn't care); what people do to get promoted from one step to the next (projects, visibility, scope); and salary
ranges for each step in India from sources you can cite. Prefer recent, specific sources (job boards' salary pages,
levels/compensation sites, recruiter reports, company career ladders).

PROFILE (every fact is true; nothing else is):
{profile}

THE CANDIDATE'S OWN CAREER NOTES:
{notes}

THE CANDIDATE'S GROWTH LOG (what they have done, are doing, learned and want, newest last; "doing" and
"goal" entries are not achievements yet, so never put them in you_have):
{growth}

Return JSON only:
{{"summary": "2 or 3 plain sentences: where this person stands now and what their strongest levers are",
  "goal_assessment": "2 or 3 sentences: which routes can lead to the long-term goal, the realistic timeline, and what decides it",
  "trends": [{{"text": "one market trend that matters to this person's routes", "source": "URL"}}],
  "paths": [
    {{"id": "short-id", "name": "route name", "fit": "strong" | "good" | "stretch",
      "why": "one or two sentences: why this route suits (or stretches) THIS profile, citing its real evidence",
      "reaches_goal": true,
      "promotion_playbook": ["2 to 4 things people on this route do to get promoted, from your research"],
      "sources": ["URLs you used for this route"],
      "steps": [
        {{"title": "the title people hold at this step", "level": "e.g. Senior IC, Manager, Senior Manager",
          "when": "year range, e.g. 2026-2027", "typical_tenure": "e.g. 1-2 years at this step",
          "what_it_takes": ["3 to 5 short items the market screens for at this step"],
          "you_have": ["evidence from the PROFILE only, short; empty if none"],
          "to_build": ["the concrete gaps to close before or during this step"],
          "search_titles": ["2 to 4 job titles to search for this step"],
          "certifications": [{{"name": "e.g. AWS Certified Solutions Architect - Associate", "need": "screened" | "helps", "why": "short"}}],
          "projects": ["1 to 3 concrete projects the candidate could take on (at work or on Lighthouse) to earn this step"],
          "salary_band": {{"low": 0, "high": 0, "unit": "LPA", "source": "URL the range came from"}},
          "ladder_note": "how this level is named across companies (e.g. 'Senior PM = PM II at some product companies')"}}
      ]}}
  ]}}
Rules:
- 5 or 6 paths, each with 3 or 4 steps that together span the next five years. Step 1 must be reachable from
  the current role within a year (a lateral move or the next level), not a leap.
- Paths must differ in kind: individual contributor, people management, product, forward-deployed / applied AI,
  solutions / customer-facing, and (if it fits) a founder or early-stage route.
- Be honest about stretch: if a step usually needs things the profile lacks (direct reports, years as a PM),
  say so in to_build and mark the path's fit accordingly. Never put anything in you_have the profile doesn't show.
- Use title conventions of product companies and global companies hiring in India; mention where Indian IT
  services titles differ.
- A salary_band must be copied from a source you found, with its URL; if you found none, set salary_band to null.
  Never estimate a number yourself. Certifications only where employers actually ask for them; [] otherwise.
- Include the long-term goal as the final step of every route that can reach it (it may sit beyond five years);
  set reaches_goal false for routes that lead elsewhere, and say where in "why".
- No percentages and no statistics without a source."""


def history(profile: dict) -> list[dict]:
    """Every role, project and the degree, oldest first, straight from the profile."""
    items = []
    for e in profile["education"]:
        if not e.get("optional"):
            items.append({"id": f"edu-{e['id']}", "kind": "education", "title": e["degree"], "org": e["institution"],
                          "when": e["year"], "sort": f"{e['year']}-06"})
    for r in profile["experience"]:
        items.append({"id": r["id"], "kind": "internship" if r.get("internship") else "role", "title": r["title"],
                      "org": r["company"] + (f", for {r['client']}" if r.get("client") else ""),
                      "when": f"{fmt_month(r['start'])} – {fmt_month(r.get('end'))}", "sort": r["start"],
                      "current": not r.get("end"), "highlights": [b["text"] for b in r["bullets"][:2]]})
    for p in profile.get("projects", []):
        items.append({"id": p["id"], "kind": "project", "title": p["name"], "org": p["role"],
                      "when": f"{fmt_month(p['start'])} – {fmt_month(p.get('end'))}", "sort": p["start"] + "-b",
                      "highlights": [b["text"] for b in p["bullets"][:2]]})
    return sorted(items, key=lambda x: x["sort"])


LOG_FILE = DATA / "growth_log.json"
GOAL_FILE = DATA / "career_goal.json"
SALARY_FILE = DATA / "salary.json"


def goal() -> dict:
    return json.loads(GOAL_FILE.read_text("utf-8")) if GOAL_FILE.exists() else {}


def set_goal(body: dict) -> dict:
    g = {"role": (body.get("role") or "").strip()[:80], "year": int(body.get("year") or 0) or None,
         "salary": float(body["salary"]) if str(body.get("salary") or "").strip() else None,
         "unit": body.get("unit") or "LPA"}
    GOAL_FILE.write_text(json.dumps(g, indent=2), "utf-8")
    return g


def salaries() -> list[dict]:
    return json.loads(SALARY_FILE.read_text("utf-8")) if SALARY_FILE.exists() else []


def add_salary(year: int, amount: float, unit: str = "LPA", note: str = "") -> dict:
    import uuid
    if not (1990 < int(year) < 2100) or float(amount) <= 0:
        raise ValueError("Give a year and a yearly amount above zero.")
    row = {"id": uuid.uuid4().hex[:8], "year": int(year), "amount": float(amount), "unit": unit or "LPA", "note": (note or "")[:120]}
    rows = sorted([r for r in salaries() if r["year"] != row["year"]] + [row], key=lambda r: r["year"])
    SALARY_FILE.write_text(json.dumps(rows, indent=2), "utf-8")
    return row


def remove_salary(row_id: str) -> None:
    SALARY_FILE.write_text(json.dumps([r for r in salaries() if r["id"] != row_id], indent=2), "utf-8")


def projection() -> dict:
    """Code, not the model: your past yearly growth, where it takes you, and what your goal needs."""
    rows, g = salaries(), goal()
    if not rows:
        return {}
    units = {r["unit"] for r in rows}
    first, last = rows[0], rows[-1]
    out = {"unit": last["unit"], "mixed_units": len(units) > 1, "actual": [[r["year"], r["amount"]] for r in rows]}
    span = last["year"] - first["year"]
    if span >= 1 and first["amount"] > 0:
        cagr = (last["amount"] / first["amount"]) ** (1 / span) - 1
        out["past_growth"] = round(cagr * 100, 1)
        horizon = max((g.get("year") or 0), last["year"] + 5)
        out["projected"] = [[y, round(last["amount"] * (1 + cagr) ** (y - last["year"]), 1)] for y in range(last["year"], horizon + 1)]
    if g.get("salary") and g.get("year") and g["year"] > last["year"] and (g.get("unit") or "LPA") == last["unit"]:
        years = g["year"] - last["year"]
        need = (g["salary"] / last["amount"]) ** (1 / years) - 1
        out["needed_growth"] = round(need * 100, 1)
        out["needed"] = [[y, round(last["amount"] * (1 + need) ** (y - last["year"]), 1)] for y in range(last["year"], g["year"] + 1)]
        if "past_growth" in out:
            at_goal = next((v for y, v in out["projected"] if y == g["year"]), None)
            out["projected_at_goal_year"] = at_goal
    return out
LOG_KINDS = {"did": "Shipped or did", "doing": "Doing now", "skill": "Learned a skill", "cert": "Certification", "goal": "Goal"}


def load() -> dict | None:
    return json.loads(MAP_FILE.read_text("utf-8")) if MAP_FILE.exists() else None


def log() -> list[dict]:
    return json.loads(LOG_FILE.read_text("utf-8")) if LOG_FILE.exists() else []


def add_log(kind: str, text: str, date: str = "", use_on_cv: bool = False) -> dict:
    """What the candidate has done or is doing. Skills marked for CVs join the profile's confirmed
    skills (so tailoring may claim them); everything else only informs the career routes."""
    import uuid
    from .profile import save_profile
    text = (text or "").strip()
    if not text:
        raise ValueError("Write what you did, are doing, learned or want.")
    entry = {"id": uuid.uuid4().hex[:8], "kind": kind if kind in LOG_KINDS else "did", "text": text[:600],
             "date": date or dt.date.today().isoformat(), "use_on_cv": bool(use_on_cv and kind in ("skill", "cert"))}
    rows = log() + [entry]
    LOG_FILE.write_text(json.dumps(rows, indent=2, ensure_ascii=False), "utf-8")
    if entry["use_on_cv"]:
        profile = load_profile()
        have = set(profile.get("confirmed_skills", []))
        profile["confirmed_skills"] = sorted(have | {entry["text"]})
        save_profile(profile)
    return entry


def remove_log(entry_id: str) -> None:
    from .profile import save_profile
    rows = log()
    gone = next((r for r in rows if r["id"] == entry_id), None)
    LOG_FILE.write_text(json.dumps([r for r in rows if r["id"] != entry_id], indent=2, ensure_ascii=False), "utf-8")
    if gone and gone.get("use_on_cv"):
        profile = load_profile()
        profile["confirmed_skills"] = [s for s in profile.get("confirmed_skills", []) if s != gone["text"]]
        save_profile(profile)


def generate(engine_id: str = "claude:opus", progress=lambda m: None) -> dict:
    profile = load_profile()
    current = next((r for r in profile["experience"] if not r.get("end")), profile["experience"][0])
    notes_path = ROOT / "CAREER_MAP.md"
    notes = notes_path.read_text("utf-8")[:9000] if notes_path.exists() else ""
    facts = {k: profile[k] for k in ("experience", "education", "certifications", "projects", "skills") if k in profile}
    g, proj = goal(), projection()
    goal_txt = (f"{g.get('role') or 'not set'} by {g.get('year') or 'no year set'}"
                + (f", earning {g['salary']} {g.get('unit', 'LPA')} a year" if g.get("salary") else "")) if g else "not set"
    sal_txt = "not logged"
    if proj:
        sal_txt = f"current {proj['actual'][-1][1]} {proj['unit']} ({proj['actual'][-1][0]})"
        if "past_growth" in proj:
            sal_txt += f"; past growth {proj['past_growth']}% a year"
        if "needed_growth" in proj:
            sal_txt += f"; the goal needs {proj['needed_growth']}% a year from now"
    brief = BRIEF.format(today=dt.date.today().isoformat(), years=experience_years_label(profile), goal=goal_txt, salary=sal_txt,
                         current=f"{current['title']} at {current['company']}",
                         profile=json.dumps(facts, ensure_ascii=False)[:30000], notes=notes,
                         growth="\n".join(f"- {e['date']} [{LOG_KINDS.get(e['kind'], e['kind'])}] {e['text']}" for e in log()) or "(empty)")
    progress("Researching the market and reading your whole history (2 to 5 minutes)")
    engine = get_engine(engine_id)
    try:
        reply = engine.run(brief, timeout=600, tools=["WebSearch"]) if engine_id.startswith("claude") else engine.run(brief, timeout=600)
    except TypeError:
        reply = engine.run(brief, timeout=600)
    raw = parse_json_reply(reply)
    progress("Laying out the routes")
    paths = []
    for p in (raw.get("paths") or [])[:6]:
        steps = [s for s in (p.get("steps") or [])[:4] if s.get("title")]
        if steps:
            paths.append({"id": re.sub(r"[^a-z0-9-]", "", str(p.get("id") or p.get("name", "")).lower())[:30] or f"p{len(paths)}",
                          "name": p.get("name", "Route"), "fit": p.get("fit", "good"), "why": p.get("why", ""),
                          "reaches_goal": bool(p.get("reaches_goal")),
                          "promotion_playbook": p.get("promotion_playbook", [])[:5],
                          "sources": [u for u in p.get("sources", []) if str(u).startswith("http")][:8],
                          "steps": [{"title": s["title"], "level": s.get("level", ""), "when": s.get("when", ""),
                                     "typical_tenure": s.get("typical_tenure", ""), "what_it_takes": s.get("what_it_takes", [])[:6],
                                     "you_have": s.get("you_have", [])[:6], "to_build": s.get("to_build", [])[:6],
                                     "search_titles": s.get("search_titles", [])[:4], "ladder_note": s.get("ladder_note", ""),
                                     "certifications": [c for c in (s.get("certifications") or []) if isinstance(c, dict) and c.get("name")][:4],
                                     "projects": s.get("projects", [])[:3],
                                     "salary_band": band(s.get("salary_band"))}
                                    for s in steps]})
    out = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "engine": engine_id,
           "summary": raw.get("summary", ""), "goal_assessment": raw.get("goal_assessment", ""),
           "trends": [t for t in (raw.get("trends") or []) if isinstance(t, dict) and t.get("text")][:8],
           "goal": g, "history": history(profile), "paths": paths}
    MAP_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), "utf-8")
    return out


def band(b) -> dict | None:
    """A salary range survives only with numbers that make sense and a source link; no source, no number."""
    try:
        low, high = float(b["low"]), float(b["high"])
    except (TypeError, KeyError, ValueError):
        return None
    src = str(b.get("source") or "")
    if not src.startswith("http") or low <= 0 or high < low:
        return None
    return {"low": low, "high": high, "unit": b.get("unit") or "LPA", "source": src}


def matching_jobs(titles: list[str], days: int, prefs: dict) -> list[dict]:
    """Jobs already found by Discover whose titles match a step's titles."""
    from .discover import saved
    words = [re.escape(t.strip()) for t in titles if t and t.strip()]
    if not words:
        return []
    pat = re.compile("|".join(words), re.I)
    loose = re.compile("|".join(re.escape(w) for t in titles for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", t)
                                if w.lower() not in {"senior", "lead", "staff", "principal", "head", "manager", "engineer"}), re.I)
    jobs = saved(days, prefs)
    exact = [j for j in jobs if pat.search(j["title"])]
    near = [j for j in jobs if j not in exact and loose.pattern and loose.search(j["title"])]
    return (exact + near)[:25]


def search_web(titles: list[str], prefs: dict, progress=lambda m: None) -> dict:
    """A live web search for one step's titles, in the candidate's regions, through Discover's web source."""
    from .discover import from_websearch, rank, rank_signature, with_profile
    regions = prefs.get("regions") or ["remote", "bengaluru", "india", "world"]
    where = {"remote": "remote", "bengaluru": "Bengaluru", "india": "India", "world": "UK, EU, Dubai or Singapore"}
    query = f"{' OR '.join(titles[:3])} jobs in " + ", ".join(where[r] for r in regions if r in where)
    progress(f"Searching the web: {query}")
    _, jobs = from_websearch(query, prefs.get("days", 14))
    ev = strong_skills(load_profile())
    p = with_profile(prefs)
    sig = rank_signature(p, ev)
    ranked = [rank(j, ev, p, sig) for j in jobs]
    return {"query": query, "jobs": [{k: v for k, v in j.items() if k not in ("description", "_r")} for j in ranked]}
