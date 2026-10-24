"""Answers for a job application form, for the browser extension to fill.

The extension scans the form and sends its fields; this decides what goes in each one. Contact details come
straight from the profile and saved answers by code. The model answers the rest from the profile, the
tailored CV and the JD, and must leave a field empty rather than guess: anything legal or personal (visa,
salary, notice, relocation) with no saved answer is handed back to you, and your answer is saved so the next
form asks once fewer. Nothing is submitted here; the extension submits only when you press Submit.
"""
import json
import re

from .config import data_dir, output_dir
from .engines import ENGINES, light_engine, parse_json_reply
from .profile import brief_view, experience_years_label, load_profile
from .tailor import load_report

CHOICE = ("select", "radio", "combobox")


def _answers_file():
    return data_dir() / "answers.json"


def saved() -> list[dict]:
    f = _answers_file()
    return json.loads(f.read_text("utf-8")) if f.exists() else []


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (q or "").lower()).strip()


def remember(question: str, answer) -> list[dict]:
    """Keep your answer to a question so every later form gets it without asking."""
    q = (question or "").strip()
    if not q or answer in (None, "", []):
        return saved()
    rows = [r for r in saved() if _norm(r["q"]) != _norm(q)] + [{"q": q[:300], "a": answer}]
    _answers_file().write_text(json.dumps(rows, indent=1, ensure_ascii=False), "utf-8")
    return rows


def forget(question: str) -> list[dict]:
    rows = [r for r in saved() if _norm(r["q"]) != _norm(question)]
    _answers_file().write_text(json.dumps(rows, indent=1, ensure_ascii=False), "utf-8")
    return rows


def _contact(profile: dict, report: dict) -> list[tuple[str, str]]:
    """Label pattern -> value, first match wins. Only facts the profile states."""
    b = profile["basics"]
    name = (b.get("name") or "").split()
    current = next((r for r in profile["experience"] if not r.get("end")), None)
    links = {"github": "", "website": ""}
    for p in profile.get("projects", []):
        url = p.get("url") or ""
        if "github.com" in url and not links["github"]:
            links["github"] = "https://" + "/".join(url.split("//")[-1].split("/")[:2])
    return [
        (r"\bfirst\s*name|given\s*name|forename", name[0] if name else ""),
        (r"\b(last|family)\s*name|surname", name[-1] if len(name) > 1 else ""),
        (r"middle\s*name", ""),
        (r"preferred\s*(first\s*)?name", name[0] if name else ""),
        (r"^(full\s*)?name\b|your\s*name|legal\s*name", b.get("name", "")),
        (r"e-?mail", b.get("email", "")),
        (r"phone|mobile|contact\s*number|telephone", b.get("phone", "")),
        (r"linkedin", b.get("linkedin", "")),
        (r"github", links["github"]),
        (r"current\s*(company|employer)|most\s*recent\s*(company|employer)", current["company"] if current else ""),
        (r"current\s*(job\s*)?title|current\s*(role|position)|headline", report.get("headline") or b.get("headline", "")),
        (r"years\s*of\s*(professional\s*)?experience|total\s*experience", experience_years_label(profile)),
        (r"^(current\s*)?(location|city)\b|where\s*are\s*you\s*(based|located)", b.get("location", "")),
    ]


def _pick(value, options: list[str]):
    """The option the answer names, exactly as the form spells it, or None."""
    if value in (None, "") or not options:
        return None
    v = _norm(str(value))
    for o in options:
        if _norm(o) == v:
            return o
    hits = [o for o in options if v and (v in _norm(o) or _norm(o) in v)]
    return hits[0] if len(hits) == 1 else None


def _fits(field: dict, value):
    """A value the field can actually take, or None."""
    kind, options = field.get("kind"), field.get("options") or []
    if value in (None, ""):
        return None
    if kind in CHOICE:
        return _pick(value, options)
    if kind == "checkgroup":
        picked = [p for p in (_pick(v, options) for v in (value if isinstance(value, list) else [value])) if p]
        return picked or None
    if kind == "checkbox":
        return value if isinstance(value, bool) else str(value).lower() in ("yes", "true", "1", "checked")
    text = str(value).strip()
    if field.get("max"):
        text = text[:int(field["max"])]
    return text or None


BRIEF = """You are filling in a job application form for the candidate below, on their behalf. They will
review every answer before submitting. Return JSON only:
{"answers": {"<field id>": <value or null>}, "why": {"<field id>": "why it was left empty, in a few words"}}

Rules:
- Use only facts in CANDIDATE, SAVED ANSWERS, TAILORED CV and COVER LETTER. Never invent a fact, a number,
  a date, a salary, a person, or a skill. If the facts don't answer a field, its value is null.
- Legal and personal questions (work authorisation, visa sponsorship, salary, notice period, start date,
  relocation, willingness to travel, background checks, criminal record, age, references) are answered
  ONLY from SAVED ANSWERS or a fact CANDIDATE states. Otherwise null with why "needs you".
- Demographic and equal-opportunity questions (gender, race, ethnicity, veteran, disability, sexual
  orientation, pronouns): pick the option that declines to answer ("Decline to self-identify", "Prefer not to
  say", "I don't wish to answer"). If there is no such option, null.
- select / radio / combobox: the value is one option copied EXACTLY from that field's options.
  checkgroup: a list of options copied exactly. checkbox: true or false; a consent or terms box is null
  (the candidate ticks it themselves).
- Questions like "Why do you want to work here?" or "Tell us about a project": write 60 to 150 words in the
  first person, plain and specific, from the candidate's real experience and the JD. No em dashes. Within
  the field's max length if it has one.
- "How did you hear about us": the company's careers page, if offered; else null.
- Fields already answered by code are not listed; answer only the fields given."""


def answer(app_id: str, fields: list[dict], url: str = "", progress=lambda m: None) -> dict:
    """fields: [{id, kind, label, options?, required?, max?}] -> {answers, needs, files, from_model}."""
    profile, report = load_profile(), load_report(app_id)
    contact, mine = _contact(profile, report), saved()
    answers, needs, files, ask = {}, [], {}, []
    progress("Matching the fields to your profile")
    first_file = True
    for f in fields:
        fid, kind, label = f["id"], f.get("kind"), (f.get("label") or "").strip()
        if kind == "file":
            if re.search(r"cover|letter|motivation", label, re.I):
                files[fid] = "letter"
            elif re.search(r"resum|\bcv\b|curriculum", label, re.I) or first_file:
                files[fid] = "cv"
            else:
                needs.append({"id": fid, "label": label, "why": "a file only you have"})
            first_file = False
            continue
        known = next((r["a"] for r in mine if _norm(r["q"]) == _norm(label)), None)
        if known is None and kind not in CHOICE + ("checkgroup", "checkbox"):
            known = next((v for pat, v in contact if v and re.search(pat, label, re.I)), None)
        value = _fits(f, known)
        if value is not None:
            answers[fid] = value
        elif label:
            ask.append(f)
    if ask:
        progress(f"Answering {len(ask)} question{'s' if len(ask) != 1 else ''} from your profile")
        facts = {
            "CANDIDATE": brief_view(profile) | {"name": profile["basics"].get("name", ""),
                                                "location": profile["basics"].get("location", ""),
                                                "nationality": profile["basics"].get("nationality", ""),
                                                "availability": profile["basics"].get("availability", ""),
                                                "years of experience": experience_years_label(profile)},
            "SAVED ANSWERS": mine,
            "TAILORED CV": {"headline": report.get("headline", ""), "recruiter note": report.get("recruiter_note", ""),
                            "company": report.get("company", ""), "role": report.get("role", "")},
            "COVER LETTER": report.get("cover_letter", ""),
        }
        slim = [{k: f[k] for k in ("id", "kind", "label", "options", "required", "max") if f.get(k) not in (None, "", [])}
                for f in ask]
        jd_file = output_dir() / app_id / "jd.txt"
        jd = jd_file.read_text("utf-8") if jd_file.exists() else ""
        raw = parse_json_reply(ENGINES[light_engine()].run(
            f"{BRIEF}\n\n{json.dumps(facts, ensure_ascii=False)}\n\nJOB DESCRIPTION:\n{jd[:8000]}\n\n"
            f"FORM ({url}):\n{json.dumps(slim, ensure_ascii=False)}", timeout=240))
        got, why = raw.get("answers") or {}, raw.get("why") or {}
        for f in ask:
            value = _fits(f, got.get(f["id"]))
            if value is not None:
                answers[f["id"]] = value
            elif f.get("required") or f.get("kind") not in ("checkbox",):
                needs.append({"id": f["id"], "label": f["label"], "why": why.get(f["id"]) or "needs you"})
    return {"answers": answers, "needs": needs, "files": files, "asked_model": len(ask)}
