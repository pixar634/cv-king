"""One application, end to end: JD in, tailored and checked PDF out.

The model writes words. Code owns everything else: titles, companies, dates, experience years,
the page fit, the score, and the checks that every number and skill in the output is in the profile.
"""
import datetime as dt
import json
import re
from pathlib import Path

import yaml

from . import atscheck, tracker
from .config import KNOWLEDGE, ORIGINAL_TXT, OUTPUT
from .engines import EngineError, get_engine, parse_json_reply
from .jd import parse_jd
from .keywords import SOFT, covered, jd_keywords, jd_spelling, score, taxonomy, title_match
from .layouts import LAYOUTS
from .letters import first_letter_rules, phrase_flags
from .profile import (all_skills, brief_view, bullet_index, experience_years_label, load_profile,
                      profile_text)
from .render import build_doc, fit_and_print, print_html, set_metadata

BANNED_TITLE = re.compile(r"\b(staff|principal|architect|manager|head|director|vp|cto|founder|founding)\b", re.I)
BANNED_WORDS = ["spearheaded", "orchestrated", "leveraged", "leveraging", "pioneered", "synergy",
                "cutting-edge", "state-of-the-art", "robust", "seamless", "seamlessly", "passionate",
                "results-driven", "go-getter", "delve", "tapestry", "holistic", "best-in-class",
                "world-class", "game-changer", "revolutionised", "revolutionized", "utilise", "utilize",
                "responsible for", "worked on", "helped with", "numerous"]
NUM = re.compile(r"(?<![A-Za-z0-9.])[$₹€£]?~?\d[\d,]*(?:\.\d+)?(?:\s?%|\+|x\b)?")
GDPR_TEXT = "Regulation (EU) 2016/679 of the European Parliament and of the Council of 27 April 2016"

OUTPUT_SPEC = """{
  "headline": "string, max 5 words",
  "summary": "string, may use **bold** on at most 2 short phrases",
  "skills": [{"label": "string", "items": ["string"]}],
  "experience": [
    {"id": "role id from PROFILE", "bullets": [{"text": "string", "src": ["profile bullet id"]}]}
  ],
  "jd": {"title": "", "company": "", "seniority": "", "domain": "",
         "company_domain": "the company's main website domain (e.g. razorpay.com) only if you are sure, else empty",
         "must_have": ["short verbatim JD term"], "nice_to_have": ["short verbatim JD term"]},
  "gaps": [{"term": "as the JD writes it", "must_have": true, "note": "string"}],
  "recruiter_note": "string, max 280 characters",
  "cover_letter": "string",
  "fit_notes": ["string"]
}
List roles in "experience" newest first, in the PROFILE's order. Omit a role only if it is optional."""

def markets() -> dict:
    return yaml.safe_load((KNOWLEDGE / "markets.yaml").read_text("utf-8"))


def tracks() -> dict:
    return yaml.safe_load((KNOWLEDGE / "tracks.yaml").read_text("utf-8"))


def track_for(title: str) -> str:
    """The career track a job title implies, for the 'auto' setting."""
    t = (title or "").lower()
    if re.search(r"forward[\s-]?deployed|deployment engineer|applied ai|implementation (engineer|lead)|field engineer|"
                 r"customer engineer|resident (solutions )?architect|ai architect", t):
        return "forward_deployed"
    if re.search(r"solutions? (engineer|architect)|pre[\s-]?sales|sales engineer|gtm engineer|growth engineer|"
                 r"developer advocate|devrel|partner engineer", t):
        return "solutions_gtm"
    if re.search(r"\bai engineer|agentic|ml engineer|llm engineer|ai platform", t) and "manager" not in t:
        return "ai_engineer"
    if re.search(r"engineering manager|\bem\b|tech(nical)? lead manager|head of engineering|director of engineering|development manager", t):
        return "engineering_manager"
    if re.search(r"product manager|product owner|\bapm\b|product lead|\btpm\b", t):
        return "product_manager"
    if re.search(r"program manager|project manager|project lead|delivery (lead|manager)|scrum master", t):
        return "delivery_lead"
    if re.search(r"\bstaff\b|principal|architect", t):
        return "staff"
    return "ic_lead"


def default_header_line(market_id: str, profile: dict) -> str:
    return markets()[market_id]["header_line"].format(availability=profile["basics"].get("availability", "")).strip(" ·")


def page_budget(max_pages: int) -> str:
    if max_pages == 1:
        return ("## PAGE BUDGET: ONE PAGE\nThe candidate chose a one-page CV. This overrides the counts above: "
                "summary 2 sentences, 35 to 50 words. Bullets: Tech Mahindra 3, MathCo Senior Product Engineer 3, "
                "MathCo Software Engineer 3, Dreamztech 1, internship 0. Every bullet under 170 characters. "
                "Skills: 4 groups, 18 to 24 items in total. Make every line earn its place for THIS JD.")
    return "## PAGE BUDGET: TWO PAGES\nUse the counts in the brief. Code trims from the end of the oldest roles if it overflows."


def keyword_guidance(jd_text: str, profile: dict) -> str:
    """Code decides which JD keywords the profile supports; the model is told to use those and
    never the rest. Without this list the model drops supported terms and reaches for unsupported ones."""
    ptext = profile_text(profile)
    kws = sorted(jd_keywords(jd_text).values(), key=lambda k: -k.weight)
    use = [k for k in kws if covered(k, ptext) and k.name not in SOFT]
    avoid = [k for k in kws if not covered(k, ptext) and k.name not in SOFT]
    line = lambda k: f'- {k.name} (JD writes "{jd_spelling(k, jd_text)}"){", required" if k.tier == "required" else ""}'
    return ("## KEYWORDS (computed by code from the JD and the profile)\n"
            "The profile supports these. Use each at least once where it is true in context, in the JD's own "
            "spelling, required ones in the summary or a top bullet as well as in Skills:\n"
            + ("\n".join(line(k) for k in use) or "- (none found)")
            + "\n\nThe JD also asks for these and the profile does NOT show them. Never write them anywhere; "
              "list the ones that matter under gaps:\n"
            + ("\n".join(line(k) for k in avoid) or "- (none)"))


def build_brief(profile: dict, jd: dict, market_id: str, company: str, role: str, years: str,
                max_pages: int, track_id: str = "ic_lead") -> str:
    m = markets()[market_id]
    tr = tracks().get(track_id) or tracks()["ic_lead"]
    rules = "\n".join(f"- {r}" for r in m["rules"])
    return "\n\n".join([
        (KNOWLEDGE / "playbook.md").read_text("utf-8"),
        first_letter_rules(),
        f"## MARKET: {m['label']}\nSpelling: {m['spelling']}\n{rules}",
        page_budget(max_pages),
        f"## CAREER TRACK: {tr['label']}\n{tr['brief']}\nHeadline examples for this track: "
        + "; ".join(tr["headline_examples"]) + ". This overrides the generic headline examples; the Lead rule still holds.",
        f"## EXPERIENCE_YEARS\n{years}",
        f"## TARGET\nCompany: {company or 'unknown'}\nRole: {role or 'see JD'}\nLocation: {jd.get('location') or 'see JD'}\n"
        f"Candidate name for sign-offs: {profile['basics']['name']}\nAvailability: {profile['basics'].get('availability', '')}",
        f"## JOB DESCRIPTION\n<<<\n{jd['text']}\n>>>",
        keyword_guidance(jd["text"], profile),
        f"## PROFILE\n```json\n{json.dumps(brief_view(profile, track_id), indent=1, ensure_ascii=False)}\n```",
        f"## OUTPUT\nReturn only this JSON object:\n{OUTPUT_SPEC}",
    ])


# ---------- guards ----------

def _num_tokens(text: str) -> set[str]:
    return {re.sub(r"[\s,~]", "", m) for m in NUM.findall(text)}


def _evidence_terms(text: str) -> set[str]:
    return set(taxonomy().find(text))


def _literal_in(item: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(item.strip()) + r"(?![A-Za-z0-9])", text, re.I) is not None


def validate(raw: dict, profile: dict) -> tuple[dict, list[dict]]:
    flags: list[dict] = []
    flag = lambda level, kind, text: flags.append({"level": level, "kind": kind, "text": text})
    ptext = profile_text(profile)
    evidence = _evidence_terms(ptext)
    bidx = bullet_index(profile)
    roles = {r["id"]: r for r in profile["experience"]}

    headline = (raw.get("headline") or "").strip()
    # The candidate is a Lead; a headline that drops or lowers it undersells them in every search.
    lowered = re.sub(r"^(senior|sr\.?|mid[\s-]*level)\s+", "", headline, flags=re.I)
    if headline and not re.search(r"\blead\b", lowered, re.I):
        flag("warn", "headline", f"Headline \"{headline}\" didn't say Lead; corrected to \"Lead {lowered}\".")
        headline = f"Lead {lowered}"
    if not headline or BANNED_TITLE.search(headline) or len(headline.split()) > 8:
        flag("error", "headline", f"Headline \"{headline}\" overstates or breaks the rules; used \"{profile['basics']['headline']}\" instead.")
        headline = profile["basics"]["headline"]

    skills, removed = [], []
    for g in raw.get("skills") or []:
        items = []
        for item in g.get("items") or []:
            found = _evidence_terms(item)
            ok = (found and found <= evidence | SOFT) or (not found and _literal_in(item, ptext + "\n" + "\n".join(all_skills(profile))))
            (items if ok else removed).append(item)
        if items:
            skills.append({"label": g.get("label", "Skills"), "items": items})
    if removed:
        flag("warn", "skills", "Removed from Skills because your profile doesn't show them: " + ", ".join(removed)
             + ". If you have them, tick them under Gaps and re-run.")

    experience, seen = {}, set()
    for r in raw.get("experience") or []:
        rid = r.get("id")
        if rid not in roles or rid in seen:
            flag("warn", "roles", f"Ignored unknown or repeated role id {rid!r}.")
            continue
        seen.add(rid)
        bullets = []
        for b in r.get("bullets") or []:
            text = re.sub(r"^[\s•\-–]+", "", (b.get("text") or "")).strip()
            if not text:
                continue
            src = b.get("src") or []
            if not src or any(s not in bidx or bidx[s][0] != rid for s in src):
                flag("warn", "trace", f"Bullet has no valid source in this role ({src}): \"{text[:90]}...\"")
            if len(text) > 240:
                flag("warn", "length", f"Long bullet ({len(text)} chars) may run to 3 lines: \"{text[:70]}...\"")
            bullets.append(text)
        if bullets:
            experience[rid] = bullets
    for rid, r in roles.items():
        if rid not in experience and not r.get("optional"):
            flag("error", "roles", f"The model dropped {r['title']}, {r['company']}; restored its first 3 original bullets.")
            experience[rid] = [b["text"] for b in r["bullets"][:3]]
    order = [r["id"] for r in profile["experience"]]
    experience = {rid: experience[rid] for rid in order if rid in experience}

    if not skills:
        flag("error", "skills", "The model returned no usable skills; used your profile's groups.")
        skills = [{"label": k, "items": v} for k, v in list(profile["skills"].items())[:5]]

    summary = (raw.get("summary") or profile["summary"]).strip()
    plan = {"headline": headline, "summary": summary, "skills": skills[:6], "experience": experience}
    return plan, flags


def base_plan(profile: dict) -> dict:
    """The profile as-is, for previewing a layout before any JD."""
    return {
        "headline": profile["basics"]["headline"],
        "summary": profile["summary"],
        "skills": [{"label": k, "items": v} for k, v in list(profile["skills"].items())[:6]],
        "experience": {r["id"]: [b["text"] for b in r["bullets"]] for r in profile["experience"]},
    }


def _text_flags(text: str, ptext: str, allowed: set[str], kws, where: str) -> list[dict]:
    """Numbers and skills the profile doesn't support, plus padded words, in any generated text."""
    flags = []
    flag = lambda level, kind, t: flags.append({"level": level, "kind": kind, "text": t})
    new_nums = sorted(_num_tokens(text) - _num_tokens(ptext) - allowed)
    if new_nums:
        flag("error", "numbers", f"{where}: numbers not in your profile: " + ", ".join(new_nums)
             + ". Remove each unless it's true, then add it to the profile.")
    invented = set(_evidence_terms(text) - _evidence_terms(ptext) - SOFT)
    invented |= {k.name for k in kws.values() if k.custom and covered(k, text) and not covered(k, ptext)}
    if invented:
        flag("error", "evidence", f"{where}: skills or terms your profile doesn't support: " + ", ".join(sorted(invented)) + ".")
    lower = text.lower()
    used = [w for w in BANNED_WORDS if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", lower)]
    if used:
        flag("warn", "tone", f"{where}: words recruiters read as padded or AI-written: " + ", ".join(used))
    return flags


def soften(flags: list[dict]) -> list[dict]:
    """In a letter, a JD term may be describing the role rather than claiming it: warn, and say what to check."""
    out = []
    for f in flags:
        if f["kind"] == "evidence":
            f = f | {"level": "warn", "text": f["text"].rstrip(".") + ". Fine if the letter is describing the role; "
                     "remove it if it reads as your claim."}
        out.append(f)
    return out


def analyse(pdf: Path, doc: dict, profile: dict, jd_text: str, jd_meta: dict, max_pages: int,
            allowed_numbers: set[str], cover_letter: str = "") -> dict:
    ats = atscheck.check(pdf, doc, profile, max_pages)
    text = ats["text"]
    ptext = profile_text(profile)
    # One measure everywhere: the score counts the JD's standard skill keywords (the same list
    # Discover measures), so a job's Discover number and its tailored number agree. Terms only the
    # model picked out of this JD are listed beside it, covered or not, but not counted.
    kws = jd_keywords(jd_text)
    full = jd_keywords(jd_text, jd_meta.get("must_have"), jd_meta.get("nice_to_have"))
    original = ORIGINAL_TXT.read_text("utf-8") if ORIGINAL_TXT.exists() else ""
    before, after, ceiling = score(kws, original), score(kws, text), score(kws, ptext)
    rows = [a | {"covered_before": b["covered"], "in_profile": c["covered"]}
            for a, b, c in zip(after["rows"], before["rows"], ceiling["rows"])]
    jd_terms = [{"term": k.name, "tier": k.tier, "covered": covered(k, text), "in_profile": covered(k, ptext)}
                for k in sorted(full.values(), key=lambda k: -k.weight) if k.name not in kws]
    kws = full  # the honesty checks below look at every term the JD names

    flags = _text_flags(text, ptext, allowed_numbers, kws, "CV")
    from .fit import level_check
    from .profile import experience_months
    from .fit import strong_skills
    lvl = level_check(jd_meta.get("title") or "", jd_text, experience_months(profile) // 12, strong_skills(profile),
                      (profile.get("preferences") or {}).get("max_years_asked", 11))
    if lvl["verdict"] != "ok":
        flags.insert(0, {"level": "error" if lvl["verdict"] == "above" else "warn", "kind": "level",
                         "text": ("Above your level: " if lvl["verdict"] == "above" else "A stretch: ") + "; ".join(lvl["reasons"])
                                 + ". ATS knockout questions usually ask this directly, so expect a screen-out unless you're referred."})
    if text.count("—") > 1:
        flags.append({"level": "warn", "kind": "tone", "text": f"CV: {text.count('—')} em dashes; one at most reads as human."})
    # Stuffing is repeating the JD's exact word, not a concept appearing in several forms.
    stuffed = []
    for k in kws.values():
        word = jd_spelling(k, jd_text)
        n = len(re.findall(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text, re.I))
        if n > 6 and k.name not in SOFT:
            stuffed.append(f"{word} ({n}x)")
    if stuffed:
        flags.append({"level": "warn", "kind": "stuffing",
                      "text": "The JD's exact word repeated more than 6 times (reads as keyword stuffing): " + ", ".join(stuffed)})
    if cover_letter:
        flags += soften(_text_flags(cover_letter, ptext, allowed_numbers, kws, "Cover letter")) + phrase_flags(cover_letter)

    return {
        "scores": {"before": before["score"], "after": after["score"], "ceiling": ceiling["score"]},
        "keywords": rows, "jd_terms": jd_terms, "ats": ats["checks"], "pages": ats["pages"], "analysis_flags": flags,
        "title_match": title_match(jd_meta.get("title") or "", doc["headline"]),
    }


# ---------- storage ----------

def _slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")[:n] or "x"


def new_app_dir(company: str, role: str) -> Path:
    base = f"{dt.date.today().isoformat()}_{_slug(company, 30)}_{_slug(role, 40)}"
    path, i = OUTPUT / base, 2
    while path.exists():
        path, i = OUTPUT / f"{base}-{i}", i + 1
    path.mkdir(parents=True)
    return path


def pdf_name(profile: dict, company: str, role: str) -> str:
    """Company_Position_Name.pdf: sorts by company in Downloads, and still says whose CV it is
    when a recruiter saves it next to two hundred others."""
    part = lambda s, n: re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:n].strip("_") or "x"
    return f"{part(company, 30)}_{part(role, 50)}_{part(profile['basics']['name'], 40)}.pdf"


def _allowed_numbers(header_line: str, years: str) -> set[str]:
    return _num_tokens(header_line) | _num_tokens(GDPR_TEXT) | _num_tokens(years) | {years.rstrip("+")}


def resolve_options(req: dict, market_id: str, profile: dict) -> dict:
    m = markets()[market_id]
    pages = req.get("max_pages") or "auto"
    max_pages = m["max_pages"] if pages == "auto" else int(pages)
    header_line = req.get("header_line")
    if header_line is None:
        header_line = default_header_line(market_id, profile)
    return {"market": market_id, "header_line": header_line,
            "gdpr": m.get("gdpr_clause") if req.get("gdpr") is None else bool(req["gdpr"]),
            "include_interests": req.get("include_interests", True),
            "include_school": req.get("include_school", False),
            "variant": req.get("variant") if req.get("variant") in LAYOUTS else "flowcv",
            "track": req.get("track") if req.get("track") in tracks() or req.get("track") == "auto" else "ic_lead",
            "max_pages": max_pages}


def _print(folder: Path, profile: dict, plan: dict, options: dict, company: str, role: str,
           progress=lambda m: None):
    doc = build_doc(profile, plan, options)
    pdf = folder / pdf_name(profile, company, role)
    for old in folder.glob("*.pdf"):
        if old.name != pdf.name:
            old.unlink(missing_ok=True)
    n = options["max_pages"]
    progress(f"Rendering the PDF and fitting it to {n} page{'s' if n > 1 else ''}")
    doc, html, pages, trims = fit_and_print(doc, pdf, n, log=progress)
    set_metadata(pdf, doc["meta_title"], profile["basics"]["name"], f"CV - {doc['headline']}")
    (folder / "cv.html").write_text(html, "utf-8")
    (folder / "doc.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), "utf-8")
    return doc, pdf, trims


# ---------- the pipeline ----------

def run(req: dict, progress=lambda m: None) -> dict:
    profile = load_profile()
    market_id = req.get("market") or "india"
    jd = parse_jd(req["jd"])
    if len(jd["text"]) < 200:
        raise EngineError("That job description is too short to tailor against. Paste the full posting.")
    company = (req.get("company") or jd["company"] or "").strip()
    role = (req.get("role") or jd["title"] or "").strip()
    if market_id == "auto":
        from .extract import market_for
        market_id = market_for(req.get("location") or jd.get("location") or jd["text"][:800])
        req = req | {"market": market_id}
    years = experience_years_label(profile)
    engine = get_engine(req.get("engine") or "claude:opus")
    options = resolve_options(req, market_id, profile)
    if options["track"] == "auto":
        options["track"] = track_for(role or jd["text"].strip().splitlines()[0][:120])

    brief = build_brief(profile, jd, market_id, company, role, years, options["max_pages"], options["track"])
    progress(f"Writing with {engine.label} (usually 1 to 3 minutes)")
    reply = engine.run(brief)
    try:
        raw = parse_json_reply(reply)
    except EngineError:
        progress("The reply wasn't clean JSON; asking once more")
        reply = engine.run(brief + "\n\nYour previous reply was not valid JSON. Return only the JSON object.")
        raw = parse_json_reply(reply)

    progress("Checking every number and skill against your profile")
    plan, flags = validate(raw, profile)
    jd_meta = raw.get("jd") or {}
    company = company or (jd_meta.get("company") or "").strip() or "Company"
    role = role or (jd_meta.get("title") or "").strip() or "Role"
    jd_meta["title"] = jd_meta.get("title") or role

    folder = new_app_dir(company, role)
    (folder / "jd.txt").write_text(jd["text"], "utf-8")
    (folder / "brief.md").write_text(brief, "utf-8")
    (folder / "model_reply.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), "utf-8")
    (folder / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), "utf-8")
    cover = (raw.get("cover_letter") or "").strip()
    if cover:
        (folder / "cover_letter.txt").write_text(cover, "utf-8")

    doc, pdf, trims = _print(folder, profile, plan, options, company, role, progress)
    progress("Reading the PDF back the way an ATS does")
    result = analyse(pdf, doc, profile, jd["text"], jd_meta, options["max_pages"],
                     _allowed_numbers(options["header_line"], years), cover)

    report = {
        "id": folder.name, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "company": company, "role": role, "market": market_id, "market_label": markets()[market_id]["label"],
        "engine": engine.id, "headline": doc["headline"], "experience_years": years,
        "pdf": pdf.name, "trims": trims, "plan_flags": flags, **result,
        "gaps": raw.get("gaps") or [], "recruiter_note": raw.get("recruiter_note", ""),
        "cover_letter": cover, "fit_notes": raw.get("fit_notes") or [], "jd_meta": jd_meta,
        "options": options, "edited": False,
        "job": {k: req.get(k, "") for k in ("job_url", "job_id", "location", "posted")},
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    tracker.add_from_report(report, req)
    return report


def load_report(app_id: str) -> dict:
    return json.loads((OUTPUT / app_id / "report.json").read_text("utf-8"))


def rerender(app_id: str, changes: dict) -> dict:
    """Same tailored content, different layout / page limit / status line: no model call."""
    folder = OUTPUT / app_id
    report = load_report(app_id)
    profile = load_profile()
    plan = json.loads((folder / "plan.json").read_text("utf-8"))
    req = report["options"] | {k: v for k, v in changes.items() if v is not None}
    if "max_pages" in changes and changes["max_pages"] == "auto":
        req["max_pages"] = "auto"
    options = resolve_options(req, report["market"], profile)
    doc, pdf, trims = _print(folder, profile, plan, options, report["company"], report["role"])
    jd_text = (folder / "jd.txt").read_text("utf-8")
    result = analyse(pdf, doc, profile, jd_text, report["jd_meta"], options["max_pages"],
                     _allowed_numbers(options["header_line"], report["experience_years"]), report.get("cover_letter", ""))
    report.update(result | {"options": options, "trims": trims, "pdf": pdf.name, "edited": False,
                            "headline": doc["headline"]})
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    tracker.sync_scores(report)
    return report


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^[\s•\-–]+", "", (text or "").replace("**", ""))).strip()


def changes(app_id: str) -> dict:
    """What tailoring did, line by line: the headline, the summary, and every bullet against the
    profile bullet(s) it came from. Bullets the CV left out are listed too."""
    folder = OUTPUT / app_id
    profile = load_profile()
    raw = json.loads((folder / "model_reply.json").read_text("utf-8"))
    doc = json.loads((folder / "doc.json").read_text("utf-8"))
    original = {b["id"]: b["text"] for r in profile["experience"] for b in r["bullets"]}
    src_of = {}
    for r in raw.get("experience") or []:
        for b in r.get("bullets") or []:
            src_of[(r.get("id"), _clean(b.get("text", "")))] = [s for s in (b.get("src") or []) if s in original]
    roles, used, counts = [], set(), {"rewritten": 0, "merged": 0, "kept": 0, "left_out": 0}
    for r in doc["experience"]:
        prof = next(p for p in profile["experience"] if p["id"] == r["id"])
        rows = []
        for b in r["bullets"]:
            src = src_of.get((r["id"], _clean(b)), [])
            used |= set(src)
            before = [original[s] for s in src]
            status = ("kept" if len(src) == 1 and _clean(before[0]) == _clean(b) else
                      "merged" if len(src) > 1 else "rewritten")
            counts[status] += 1
            rows.append({"after": b, "before": before, "status": status})
        dropped = [x["text"] for x in prof["bullets"] if x["id"] not in used]
        counts["left_out"] += len(dropped)
        roles.append({"id": r["id"], "title": r["title"], "company": r["company"], "bullets": rows, "left_out": dropped})
    shown = {r["id"] for r in doc["experience"]}
    omitted_roles = [f"{p['title']}, {p['company']}" for p in profile["experience"] if p["id"] not in shown]
    return {
        "headline": {"before": profile["basics"]["headline"], "after": doc["headline"]},
        "summary": {"before": profile["summary"], "after": doc["summary"]},
        "skills": {"before": [{"label": k, "items": v} for k, v in profile["skills"].items()], "after": doc["skills"]},
        "roles": roles, "omitted_roles": omitted_roles, "counts": counts,
    }


def changes_html(app_id: str) -> str:
    from .render import render_html
    folder = OUTPUT / app_id
    doc = json.loads((folder / "doc.json").read_text("utf-8"))
    ch = changes(app_id)
    doc["marks"] = {
        "headline": ch["headline"]["before"] if ch["headline"]["before"] != ch["headline"]["after"] else "",
        "summary": _clean(ch["summary"]["before"]),
        "bullets": {r["id"]: [{"status": b["status"], "before": [_clean(x) for x in b["before"]] or ["(no single source)"]}
                              for b in r["bullets"]] for r in ch["roles"]},
    }
    return render_html(doc)


def rescore(app_id: str) -> dict:
    folder = OUTPUT / app_id
    report = load_report(app_id)
    doc = json.loads((folder / "doc.json").read_text("utf-8"))
    profile = load_profile()
    result = analyse(folder / report["pdf"], doc, profile, (folder / "jd.txt").read_text("utf-8"), report["jd_meta"],
                     report["options"]["max_pages"], _allowed_numbers(report["options"]["header_line"], report["experience_years"]),
                     report.get("cover_letter", ""))
    report.update(result)
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    tracker.sync_scores(report)
    return report


def get_plan(app_id: str) -> dict:
    """The tailored content as editable sections, with each role's fixed facts for labels."""
    folder = OUTPUT / app_id
    plan = json.loads((folder / "plan.json").read_text("utf-8"))
    profile = load_profile()
    roles = {r["id"]: r for r in profile["experience"]}
    from .profile import fmt_month
    return {
        "headline": plan["headline"], "summary": plan["summary"], "skills": plan["skills"],
        "roles": [{"id": rid, "title": roles[rid]["title"], "company": roles[rid]["company"],
                   "dates": f"{fmt_month(roles[rid]['start'])} – {fmt_month(roles[rid].get('end'))}",
                   "bullets": bullets} for rid, bullets in plan["experience"].items() if rid in roles],
        "cover_letter": load_report(app_id).get("cover_letter", ""),
    }


def save_plan(app_id: str, body: dict) -> dict:
    """The candidate's own edits to the sections. Their words are kept as written; the same number and
    skill checks then run on the result, so anything new still shows in red."""
    folder = OUTPUT / app_id
    plan = json.loads((folder / "plan.json").read_text("utf-8"))
    clean = lambda s: re.sub(r"[ \t]+", " ", (s or "").strip())
    if body.get("headline", "").strip():
        plan["headline"] = clean(body["headline"])
    if body.get("summary", "").strip():
        plan["summary"] = clean(body["summary"])
    if body.get("skills") is not None:
        plan["skills"] = [{"label": clean(g.get("label")) or "Skills",
                           "items": [clean(i) for i in g.get("items", []) if clean(i)]}
                          for g in body["skills"] if [i for i in g.get("items", []) if clean(i)]]
    if body.get("roles") is not None:
        known = set(plan["experience"])
        plan["experience"] = {r["id"]: [clean(b) for b in r.get("bullets", []) if clean(b)]
                              for r in body["roles"] if r.get("id") in known}
    (folder / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), "utf-8")
    if "cover_letter" in body and body["cover_letter"].strip():
        from .letters import keep_letter
        keep_letter(app_id, body["cover_letter"], "edited")
    report = rerender(app_id, {})
    report["edited"] = True
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    return report


def apply_edits(app_id: str, html: str) -> dict:
    """The candidate edited the preview by hand: print exactly that HTML and re-check it."""
    folder = OUTPUT / app_id
    report = load_report(app_id)
    doc = json.loads((folder / "doc.json").read_text("utf-8"))
    profile = load_profile()
    html = re.sub(r"\s(contenteditable|spellcheck)=\"[^\"]*\"", "", html)
    if not html.lstrip().lower().startswith("<!doctype"):
        html = "<!doctype html>\n" + html
    (folder / "cv.html").write_text(html, "utf-8")
    pdf = folder / report["pdf"]
    print_html(html, pdf)
    set_metadata(pdf, doc["meta_title"], profile["basics"]["name"], f"CV - {doc['headline']}")
    jd_text = (folder / "jd.txt").read_text("utf-8")
    result = analyse(pdf, doc, profile, jd_text, report["jd_meta"], report["options"]["max_pages"],
                     _allowed_numbers(report["options"]["header_line"], report["experience_years"]),
                     report.get("cover_letter", ""))
    report.update(result | {"edited": True})
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    tracker.sync_scores(report)
    return report


def preview_base(options_req: dict) -> Path:
    """Your profile in a chosen layout before any JD, so layouts can be compared at once."""
    profile = load_profile()
    market_id = options_req.get("market") or "india"
    options = resolve_options(options_req, market_id, profile)
    folder = OUTPUT / "_preview"
    folder.mkdir(parents=True, exist_ok=True)
    doc = build_doc(profile, base_plan(profile), options)
    pdf = folder / f"preview_{options['variant']}_{options['max_pages']}p.pdf"
    fit_and_print(doc, pdf, options["max_pages"])
    return pdf
