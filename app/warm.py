"""Warm paths: people you already know at the companies you're looking at, from your own LinkedIn data export
(Settings, Data privacy, Get a copy of your data, Connections). Nothing is scraped: you download the file and add
it here, and it stays in your profile's data folder.

A referral is the biggest lever on a callback, so Discover and the fit label count a company where you know
someone, and each application lists them with a referral ask written from the same facts, and the same honesty
checks, as the cold mail.
"""
import csv
import datetime as dt
import io
import json
import re
import uuid

from .config import data_dir, output_dir
from .engines import get_engine, parse_json_reply
from .letters import HUMAN_RULES, banned_phrases
from .profile import brief_view, load_profile
from .tracker import _company
from .tracker import load as tracker_rows
from .untrusted import fence

_cache: dict = {}


def _file():
    return data_dir() / "connections.json"


def import_csv(text: str) -> dict:
    """LinkedIn's Connections.csv: a few lines of notes, then First Name, Last Name, URL, Email Address, Company,
    Position, Connected On."""
    lines = (text or "").lstrip("﻿").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("First Name")), None)
    if start is None:
        raise ValueError("That isn't LinkedIn's Connections.csv: its header row (First Name, Last Name, URL...) is missing.")
    people = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        name = f"{(r.get('First Name') or '').strip()} {(r.get('Last Name') or '').strip()}".strip()
        company = (r.get("Company") or "").strip()
        if name and company:
            people.append({"name": name, "company": company, "position": (r.get("Position") or "").strip(),
                           "url": (r.get("URL") or "").strip(), "connected": (r.get("Connected On") or "").strip()})
    _file().write_text(json.dumps({"imported": dt.date.today().isoformat(), "people": people}, ensure_ascii=False), "utf-8")
    _cache.clear()
    return summary()


def _index() -> dict[str, list[dict]]:
    f = _file()
    stamp = f.stat().st_mtime if f.exists() else 0
    if _cache.get("stamp") != (str(f), stamp):
        people = json.loads(f.read_text("utf-8")).get("people", []) if f.exists() else []
        idx: dict[str, list[dict]] = {}
        for p in people:
            if key := _company(p["company"]):
                idx.setdefault(key, []).append(p)
        _cache.update(stamp=(str(f), stamp), idx=idx)
    return _cache["idx"]


def companies() -> set[str]:
    """Employer keys where you know someone: what Discover and the fit label count."""
    return set(_index())


LEVELS = {0: "Intern", 1: "Junior", 2: "Mid-level", 3: "Senior", 4: "Lead", 5: "Staff / principal", 6: "Head / director",
          7: "VP / C-level"}
FUNCTION_WORDS = re.compile(r"engineer|developer|architect|sde|swe|software|platform|backend|frontend|full[\s-]?stack|"
                            r"data|ml|machine learning|ai|forward deployed|solutions|product|devops|infrastructure|security|"
                            r"mobile|cloud|applied", re.I)


def _words(text: str) -> set[str]:
    return {w.lower() for w in FUNCTION_WORDS.findall(text or "")}


def who_to_ask(company: str, role: str = "", limit: int = 8) -> list[dict]:
    """The people you know at a company, most useful first for this role, each with why and the kind of message
    that fits: someone who manages or leads in the role's own area may be the hiring manager or know them; a peer
    in that area can refer you and tell you about the team; a recruiter can route you to the right person."""
    from .network import kind
    from .strategy import title_level
    want = _words(role)
    out = []
    for p in at(company):
        k, level = kind(p["position"]), title_level(p["position"])
        near = bool(want & _words(p["position"])) or not want
        if k == "leader" and near:
            score, why, ask = 0, "Manages or leads in this area: may be the hiring manager, or know who is", "hiring_manager"
        elif k == "peer" and near:
            score, why, ask = 1, "Works in this area: can refer you and tell you what the team is like", "referral"
        elif k == "recruiter":
            score, why, ask = 2, "Recruiter: can pass your CV to the recruiter for this role", "recruiter"
        elif k == "leader":
            score, why, ask = 3, "A leader in another area: can point you to the right manager", "intro"
        elif k == "peer":
            score, why, ask = 4, "An engineer in another area: can still refer you", "referral"
        else:
            score, why, ask = 5, "Works there: can refer you or introduce you", "intro"
        out.append({k2: p[k2] for k2 in ("name", "position", "url")} | {
            "kind": k, "level": LEVELS.get(level, ""), "why": why, "ask": ask, "_s": (score, -(level or 0))})
    out.sort(key=lambda p: p.pop("_s"))
    return out[:limit]


def at(company: str) -> list[dict]:
    return _index().get(_company(company), [])


def summary() -> dict:
    idx = _index()
    f = _file()
    imported = json.loads(f.read_text("utf-8")).get("imported", "") if f.exists() else ""
    return {"people": sum(len(v) for v in idx.values()), "companies": len(idx), "imported": imported}


BRIEF = """Write a short LinkedIn message from {name} to {to_name} ({to_position} at {company}), someone they are
already connected to, asking whether they'd refer them for the "{role}" opening{job_url}.

Rules:
- 60 to 110 words. Plain and direct; they know each other only as LinkedIn connections unless CONTEXT says more.
- Say which role and link it; one or two proof points from the PROFILE that match the JD's top needs, with the
  profile's own numbers copied exactly; say the tailored CV is ready to send.
- Make it easy to say no: ask whether they'd be comfortable referring, or who the right person is.
- No flattery, no "I hope you're well", never claim to know them better than CONTEXT says.
{human}
Never use these phrases: {banned}.

Return JSON only: {{"message": "..."}}

CONTEXT FROM THE CANDIDATE: {context}

JOB DESCRIPTION:
<<<
{jd}
>>>

PROFILE (every fact is true; nothing else is):
{profile}"""


def referral(app_id: str, person_url: str, context: str = "", engine_id: str = "claude:opus",
             progress=lambda m: None) -> dict:
    """A referral ask for one application to one of your connections there, checked like a cold email."""
    from .coldmail import _flags
    folder = output_dir() / app_id
    report = json.loads((folder / "report.json").read_text("utf-8"))
    person = next((p for p in at(report["company"]) if p["url"] == person_url), None)
    if not person:
        raise ValueError("That person isn't among your connections at this company.")
    profile = load_profile()
    progress(f"Writing to {person['name']}")
    jd = (folder / "jd.txt").read_text("utf-8") if (folder / "jd.txt").exists() else ""
    job_url = next((r.get("job_url") for r in tracker_rows() if r["id"] == app_id), "")
    raw = parse_json_reply(get_engine(engine_id).run(BRIEF.format(
        name=profile["basics"]["name"], to_name=person["name"], to_position=person["position"] or "a role",
        company=report["company"], role=report["role"], job_url=f" ({job_url})" if job_url else "",
        human=HUMAN_RULES, banned="; ".join(banned_phrases()), context=context.strip() or "none",
        jd=fence(jd[:8000]), profile=json.dumps(brief_view(profile), ensure_ascii=False)[:15000]), timeout=240))
    text = str(raw.get("message") or "").strip()
    if not text:
        raise RuntimeError("The model returned no message. Try again.")
    ask = {"id": uuid.uuid4().hex[:8], "created": dt.datetime.now().isoformat(timespec="seconds"), "to": person,
           "message": text, "flags": _flags(app_id, {}, text, "Referral ask")}
    f = folder / "referrals.json"
    saved = json.loads(f.read_text("utf-8")) if f.exists() else []
    f.write_text(json.dumps([ask] + saved, indent=2, ensure_ascii=False), "utf-8")
    return ask
