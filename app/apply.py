"""Answers for a job application form, for the browser extension to fill.

The extension scans the form and sends its fields; this decides what goes in each one. Contact details come
straight from the profile and saved answers by code. The model answers the rest from the profile, the
tailored CV and the JD, and must leave a field empty rather than guess: anything legal or personal (visa,
salary, notice, relocation) with no saved answer is handed back to you, and your answer is saved so the next
form asks once fewer. Legal, pay and notice answers are kept per market (India, remote, UK, EU...), because the
true answer depends on where the job is: sponsorship is worked out from the job's market, never copied across.
Nothing is submitted here; the extension submits only when you press Submit.
"""
import json
import re

from .config import data_dir, output_dir
from .engines import ENGINES, light_engine, parse_json_reply
from .profile import availability_for, brief_view, experience_years_label, load_profile, notice_days, release_note
from .tailor import home_market, load_report
from .untrusted import fence

CHOICE = ("select", "radio", "combobox")
# Contact facts the profile is the truth for. A value already in such a box (often the site's own read of the
# uploaded CV, which can put the address in the phone box) is checked against the profile and replaced if it differs.
CODE_FIELD = re.compile(r"country\s*code|dial(l?ing)?\s*code|calling\s*code|phone\s*(country\s*)?code|\bisd\b|"
                        r"phone\s*prefix", re.I)
CHECKED = re.compile(r"\bfirst\s*name|given\s*name|\b(last|family)\s*name|surname|^(full\s*)?name\b|legal\s*name|"
                     r"e-?mail|phone|mobile|telephone|linkedin|github", re.I)
# Questions whose true answer depends on where the job is: saved per market and never used for another one.
SCOPED = re.compile(r"sponsor|visa|work\s*permit|authori[sz]|right\s*to\s*work|eligib|salary|\bctc\b|compensation|"
                    r"\bpay\b|remuneration|notice|start\s*date|joining|relocat", re.I)
# "Will you now or in the future require sponsorship?", not "Can you work without sponsorship?".
NEEDS_SPONSOR = re.compile(r"\b(require|need)\w*\b.*\bsponsor", re.I)
NOTICE = re.compile(r"notice\s*period|how\s*soon\s*can|when\s*can\s*you\s*(start|join)|earliest\s*(start|joining)|"
                    r"\bstart\s*date|date\s*of\s*joining", re.I)
CURRENCIES = {"INR": r"₹|\binr\b|\blpa\b|lakh|crore|rupee", "USD": r"\$|\busd\b|dollar", "EUR": r"€|\beur\b|euro",
              "GBP": r"£|\bgbp\b|pound|sterling"}


def _answers_file():
    return data_dir() / "answers.json"


def saved() -> list[dict]:
    f = _answers_file()
    return json.loads(f.read_text("utf-8")) if f.exists() else []


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (q or "").lower()).strip()


def _market_of(row: dict, home: str) -> str | None:
    """The market a saved answer holds for; None when it holds everywhere. Answers saved before markets were
    kept came from forms at home."""
    if not SCOPED.search(row["q"]):
        return None
    return row.get("market") or home


def _same(row: dict, question: str, market: str | None, home: str) -> bool:
    return _norm(row["q"]) == _norm(question) and _market_of(row, home) in (None, market or home)


def remember(question: str, answer, market: str | None = None) -> list[dict]:
    """Keep your answer to a question so every later form gets it without asking; a legal, pay or notice answer
    only for forms in the same market."""
    q = (question or "").strip()
    if not q or answer in (None, "", []):
        return saved()
    home = home_market()
    row = {"q": q[:300], "a": answer} | ({"market": market or home} if SCOPED.search(q) else {})
    rows = [r for r in saved() if not _same(r, q, row.get("market"), home)] + [row]
    _answers_file().write_text(json.dumps(rows, indent=1, ensure_ascii=False), "utf-8")
    return rows


def forget(question: str, market: str | None = None) -> list[dict]:
    """Drop a saved answer; with a market, only that market's answer to it."""
    home = home_market()
    rows = [r for r in saved() if _norm(r["q"]) != _norm(question) or
            (market and _market_of(r, home) not in (None, market))]
    _answers_file().write_text(json.dumps(rows, indent=1, ensure_ascii=False), "utf-8")
    return rows


def _currency(text) -> str | None:
    hits = [c for c, pat in CURRENCIES.items() if re.search(pat, str(text or ""), re.I)]
    return hits[0] if len(hits) == 1 else None


def _by_code(label: str, market: str, home: str, profile: dict, jd: str = "") -> list:
    """Answers code works out from where the job is and the profile, best first; [] when it can't."""
    if NEEDS_SPONSOR.search(label) and not re.search(r"without|not\s+require", label, re.I):
        # Working at home, or remote from home, needs no visa; a job abroad does.
        return ["No"] if market in (home, "remote") else ["Yes"]
    days = notice_days(profile)
    if days is None or not NOTICE.search(label):
        return []
    if re.search(r"start\s*date|date\s*of\s*joining|earliest", label, re.I):
        return [f"{days} days after an offer" if days else "Immediately"]
    if not days:
        return ["Immediate", "Immediately", "0 days"]
    words = {1: "one month", 2: "two months", 3: "three months"}
    months = days // 30 if days % 30 == 0 else 0
    note = release_note(profile, jd)   # a text field can carry it; a dropdown only takes the plain period
    return ([f"{days} days ({note})"] if note else []) + [f"{days} days"] + ([f"{months} month{'s' if months > 1 else ''}", words.get(months, "")] if months else [])


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
        # Your real title, as a background check would find it, never the CV's tailored headline.
        (r"current\s*(job\s*)?title|current\s*(role|position)|present\s*(job\s*)?title",
         current["title"] if current else b.get("headline", "")),
        (r"years\s*of\s*(professional\s*)?experience|total\s*experience", experience_years_label(profile)),
        (r"^(current\s*|present\s*)?(location|city)\b|where\s*(are|do)\s*you\s*(currently\s*|presently\s*)?"
         r"(based|located|live|living|reside)|current\s*(location|city|place\s*of\s*residence)", b.get("location", "")),
    ]


def _same_fact(now: str, fact: str) -> bool:
    """Does a box's value say the same as the profile? A phone may be written with or without the country code."""
    a, b = _norm(now).replace(" ", ""), _norm(fact).replace(" ", "")
    if re.fullmatch(r"\+?[\d\s().-]{7,}", fact.strip()):
        return len(re.sub(r"\D", "", now)) >= 7 and a[-10:] == b[-10:]
    return a == b


def _split_phone(phone: str) -> tuple[str, str]:
    """"+91 90000 00000" -> ("91", "9000000000"); no country code -> ("", the digits)."""
    digits = re.sub(r"\D", "", phone or "")
    return (digits[:-10], digits[-10:]) if len(digits) > 10 else ("", digits)


def _is_code_field(field: dict) -> bool:
    """A box for the phone's country code: named so, or a choice whose options are dialling codes."""
    if CODE_FIELD.search(field.get("label") or ""):
        return True
    options = field.get("options") or []
    return field.get("kind") in CHOICE and len(options) >= 3 and \
        sum(bool(re.search(r"\+\s?\d{1,4}\b", o)) for o in options) >= len(options) / 2


def _code_option(field: dict, code: str, location: str):
    """The country-code answer as the field takes it: the option with +<code> (the one naming your country when
    several share it, like +1), or "+<code>" in a text box."""
    options = field.get("options") or []
    if field.get("kind") not in CHOICE:
        return f"+{code}"
    hits = [o for o in options if re.search(rf"\+\s?{code}(?!\d)", o)]
    words = [w for w in re.findall(r"[a-z]{4,}", (location or "").lower())]
    return next((o for o in hits if any(w in o.lower() for w in words)), hits[0] if hits else None)


def _pick(value, options: list[str]):
    """The option the answer names, exactly as the form spells it, or None."""
    if value in (None, "") or not options:
        return None
    v = _norm(str(value))
    for o in options:
        if _norm(o) == v:
            return o
    # "No" is the option "No, I don't need sponsorship", not "Yes, now or in the future".
    lead = [o for o in options if v and _norm(o).startswith(v + " ")]
    if len(lead) == 1:
        return lead[0]
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
- A salary answer must be in the currency the field asks for. Never convert a figure between currencies:
  if the only figure is in another currency, null with why "needs you".
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
    home = home_market()
    market = report.get("market") or home
    # Another market's legal or pay answer is never used here, by code or by the model.
    mine = [r for r in saved() if _market_of(r, home) in (None, market)]
    contact = _contact(profile, report)
    jd_file = output_dir() / app_id / "jd.txt"
    jd = jd_file.read_text("utf-8") if jd_file.exists() else ""
    answers, needs, files, ask = {}, [], {}, []
    # A form with its own country-code box: the code goes there and the phone box gets the number without it,
    # never "+91" twice.
    code, national = _split_phone(profile["basics"].get("phone", ""))
    coded = bool(code) and any(_is_code_field(f) for f in fields)
    if coded:
        contact = [(pat, national if pat.startswith("phone") else v) for pat, v in contact]
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
        now = f.get("value")
        if coded and _is_code_field(f):
            pick = _code_option(f, code, profile["basics"].get("location", ""))
            if pick is not None:
                if _norm(str(now or "")) != _norm(pick):
                    answers[fid] = pick
                continue
        if isinstance(now, str) and now.strip():
            fact = next((v for pat, v in contact if v and re.search(pat, label, re.I)), None) \
                if kind not in CHOICE + ("checkgroup", "checkbox") and CHECKED.search(label) else None
            twice = coded and fact == national and re.sub(r"\D", "", now) != national
            if fact and (twice or not _same_fact(now, fact)):
                answers[fid] = _fits(f, fact)
            continue   # anything else already filled is kept as it is
        value = next((v for v in (_fits(f, c) for c in _by_code(label, market, home, profile, jd)) if v is not None), None)
        known = next((r["a"] for r in mine if _norm(r["q"]) == _norm(label)), None)
        if known is not None and _currency(label) and _currency(known) not in (None, _currency(label)):
            known = None   # a rupee figure is not an answer to a dollar question
        if known is None and kind not in CHOICE + ("checkgroup", "checkbox"):
            known = next((v for pat, v in contact if v and re.search(pat, label, re.I)), None)
        if value is None:
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
                                                "availability": availability_for(profile, jd),
                                                "notice period in days": notice_days(profile),
                                                "the job's market": market,
                                                "years of experience": experience_years_label(profile)},
            "SAVED ANSWERS": mine,
            "TAILORED CV": {"headline": report.get("headline", ""), "recruiter note": report.get("recruiter_note", ""),
                            "company": report.get("company", ""), "role": report.get("role", "")},
            "COVER LETTER": report.get("cover_letter", ""),
        }
        slim = [{k: f[k] for k in ("id", "kind", "label", "options", "required", "max") if f.get(k) not in (None, "", [])}
                for f in ask]
        raw = parse_json_reply(ENGINES[light_engine()].run(
            f"{BRIEF}\n\n{json.dumps(facts, ensure_ascii=False)}\n\nJOB DESCRIPTION:\n<<<\n{fence(jd[:8000])}\n>>>\n\n"
            f"FORM ({fence(url)}), its labels and options as the page wrote them:\n<<<\n{fence(json.dumps(slim, ensure_ascii=False))}\n>>>",
            timeout=240))
        got, why = raw.get("answers") or {}, raw.get("why") or {}
        for f in ask:
            value = _fits(f, got.get(f["id"]))
            if value is not None:
                answers[f["id"]] = value
            elif f.get("required") or f.get("kind") not in ("checkbox",):
                needs.append({"id": f["id"], "label": f["label"], "why": why.get(f["id"]) or "needs you"})
    return {"answers": answers, "needs": needs, "files": files, "asked_model": len(ask)}
