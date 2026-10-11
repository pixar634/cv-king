"""Cold emails for one application: written from the same facts and with the same checks as the CV and
cover letter, then saved as a Gmail draft through the Gmail connector signed in to Claude.

The app never sends: a draft lands in Gmail and the candidate sends it there. The drafting run gets exactly
one tool, create_draft, and the recipient, subject and body are fixed by this code."""
import datetime as dt
import json
import re
import uuid

from .config import output_dir
from .engines import get_engine, parse_json_reply
from .keywords import jd_keywords
from .letters import HUMAN_RULES, banned_phrases, phrase_flags, voices
from .profile import brief_view, load_profile, profile_text

FILE = "coldmail.json"
ROLES = {
    "hiring_manager": "the hiring manager for this role",
    "recruiter": "a recruiter or talent partner",
    "engineer": "someone on the team (a peer in the role's function)",
    "leader": "a founder or senior leader",
    "other": "someone at the company",
}
TONES = ("plainspoken", "warm", "proof_points", "formal")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DRAFT_TOOL = "mcp__claude_ai_Gmail__create_draft"

BRIEF = """Write a cold email from {name} to {to_name}, {to_role} at {company}, about the "{role}" opening{job_id}.
{context}
The goal is a reply: a short call, a referral, or the name of the right person. It is not to restate the CV.

Rules:
- Body 90 to 150 words. Subject under 9 words, specific (the role plus one concrete hook), never clickbait.
- Open with why you are writing to this person in particular (their role, their team, or the context above).
  No "I hope this finds you well", no "I came across", no flattery.
- One or two proof points from the PROFILE that match the JD's top needs, with the profile's own numbers,
  copied exactly.
- At most one line on what the company builds, taken only from the RESEARCH (or the JD when there is none).
- One clear, low-effort ask that fits who they are: a hiring manager gets "worth a 15-minute call?", a
  recruiter gets fit and availability, someone on the team gets a question about the team or a referral.
- Say the tailored CV is attached. Sign off with the candidate's name, then their LinkedIn URL on the next line.
- Also write the follow-up to send 5 to 7 days later if there is no reply: 40 to 70 words, polite, adding one
  new and true piece of value rather than "just bumping this".
{human}
Never use these phrases: {banned}.

VOICE: {voice}

Return JSON only:
{{"subject": "...", "body": "...", "follow_up": {{"subject": "Re: ...", "body": "..."}}}}

CANDIDATE: {name}. LinkedIn: {linkedin}. Availability: {availability}.

RESEARCH (from the web, with sources; may be empty):
{research}

JOB DESCRIPTION:
<<<
{jd}
>>>

TAILORED CV (what the candidate is sending):
{plan}

PROFILE (every fact is true; nothing else is):
{profile}"""

DRAFT = """Create one Gmail draft with the create_draft tool, using exactly the fields below. Copy the subject and
the body character for character; do not add, remove or reword anything. Do not send anything.

to: {to}
subject: {subject}
body: the plain text between the two marker lines
<<<BODY
{body}
BODY>>>

After the tool returns, reply with JSON only: {{"draft_id": "the draft's id", "view_url": "the draft's viewUrl"}}"""


CUSTOM = "custom"   # emails to people you found yourself (a post, an event), not tied to an application


def _path(owner: str):
    if owner == CUSTOM:
        folder = output_dir() / "_coldmail"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "custom.json"
    return output_dir() / owner / FILE


def load(owner: str) -> dict:
    p = _path(owner)
    return json.loads(p.read_text("utf-8")) if p.exists() else {"mails": []}


def _save(owner: str, data: dict) -> None:
    _path(owner).write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def _job(owner: str, mail_or_req: dict) -> dict:
    """What the email is about: an application's saved job, or what you gave for a custom one."""
    if owner != CUSTOM:
        folder = output_dir() / owner
        report = json.loads((folder / "report.json").read_text("utf-8"))
        prep = folder / "interview.json"
        return {"company": report["company"], "role": report["role"], "jd": (folder / "jd.txt").read_text("utf-8"),
                "jd_meta": report.get("jd_meta") or {}, "job_id": (report.get("job") or {}).get("job_id", ""),
                "plan": json.loads((folder / "plan.json").read_text("utf-8")),
                "research": json.loads(prep.read_text("utf-8")).get("research", {}) if prep.exists() else {},
                "header_line": report["options"]["header_line"], "years": report["experience_years"]}
    from .profile import experience_years_label
    from .tailor import default_header_line, home_market
    profile = load_profile()
    return {"company": mail_or_req.get("company", ""), "role": mail_or_req.get("role", "") or "the role they posted",
            "jd": mail_or_req.get("job_text", ""), "jd_meta": {}, "job_id": "", "plan": None, "research": {},
            "header_line": default_header_line(home_market(), profile), "years": experience_years_label(profile)}


def _flags(owner: str, mail: dict, text: str, where: str) -> list[dict]:
    from .tailor import _allowed_numbers, _text_flags, soften   # avoid an import cycle
    job = _job(owner, mail)
    kws = jd_keywords(job["jd"], job["jd_meta"].get("must_have"), job["jd_meta"].get("nice_to_have"))
    allowed = _allowed_numbers(job["header_line"], job["years"])
    profile = load_profile()
    # Not claims: the LinkedIn URL in the sign-off and the job's own ID are the candidate's and the employer's.
    allowed |= set(re.findall(r"\d+", profile["basics"].get("linkedin", "")))
    allowed |= set(re.findall(r"\d+", str(job["job_id"])))
    # nor is the length of the call being asked for ("a 15-minute call")
    checked = re.sub(r"\b\d{1,2}[- ]min(ute)?s?\b", "", text, flags=re.I)
    return soften(_text_flags(checked, profile_text(profile), allowed, kws, where)) + phrase_flags(text)


def _check(owner: str, mail: dict) -> dict:
    mail["flags"] = _flags(owner, mail, mail["body"], "Email") + (
        _flags(owner, mail, mail["follow_up"]["body"], "Follow-up") if mail.get("follow_up", {}).get("body") else [])
    mail["words"] = len(mail["body"].split())
    return mail


def write(owner: str, req: dict, engine_id: str = "claude:opus", progress=lambda m: None) -> dict:
    to_email = str(req.get("to_email") or "").strip()
    if not EMAIL_RE.match(to_email):
        raise ValueError("Give the recipient's email address.")
    custom = {}
    if owner == CUSTOM:
        custom = {k: str(req.get(k) or "").strip() for k in ("company", "role", "job_url", "job_text")}
        if not custom["company"]:
            raise ValueError("Give the company's name.")
    job = _job(owner, custom)
    profile = load_profile()
    b = profile["basics"]
    role_key = req.get("to_role") if req.get("to_role") in ROLES else "hiring_manager"
    tone = req.get("tone") if req.get("tone") in TONES else "plainspoken"
    v = voices()[tone]
    to_name = str(req.get("to_name") or "").strip() or "the hiring team"
    context = str(req.get("context") or "").strip()
    progress(f"Writing to {to_name} at {job['company']}")
    jd = job["jd"] or "Not given: the candidate only knows the company and that this person is hiring. Keep the email about fit with the company and ask what they're hiring for."
    brief = BRIEF.format(
        name=b["name"], to_name=to_name, to_role=ROLES[role_key], company=job["company"], role=job["role"],
        job_id=f" (job ID {job['job_id']})" if job["job_id"] else "",
        context=f"CONTEXT FROM THE CANDIDATE: {context}" if context else "No shared context: this is a genuinely cold email.",
        human=HUMAN_RULES, banned="; ".join(banned_phrases()), voice=f"{v['label']}: {v['brief']}",
        linkedin=b.get("linkedin", ""), availability=b.get("availability", ""),
        research=json.dumps(job["research"], ensure_ascii=False)[:8000], jd=jd[:10000],
        plan=json.dumps(job["plan"], ensure_ascii=False)[:10000] if job["plan"] else
             "None tailored yet: write from the PROFILE, leading with what matches this job, and say a CV is attached.",
        profile=json.dumps(brief_view(profile), ensure_ascii=False)[:18000])
    raw = parse_json_reply(get_engine(engine_id).run(brief, timeout=300))
    fu = raw.get("follow_up") or {}
    mail = {"id": uuid.uuid4().hex[:8], "created": dt.datetime.now().isoformat(timespec="seconds"),
            "to_name": to_name if req.get("to_name") else "", "to_email": to_email, "to_role": role_key, "context": context,
            "tone": tone, "subject": str(raw.get("subject") or "").strip(), "body": str(raw.get("body") or "").strip(),
            "follow_up": {"subject": str(fu.get("subject") or "").strip(), "body": str(fu.get("body") or "").strip()},
            "drafts": {}, "sent_at": ""} | custom
    if not mail["body"]:
        raise RuntimeError("The model returned no email. Try again.")
    data = load(owner)
    data["mails"].insert(0, _check(owner, mail))
    _save(owner, data)
    return mail


def _find(data: dict, mail_id: str) -> dict:
    mail = next((m for m in data["mails"] if m["id"] == mail_id), None)
    if not mail:
        raise KeyError(mail_id)
    return mail


def update(owner: str, mail_id: str, body: dict) -> dict:
    data = load(owner)
    mail = _find(data, mail_id)
    for k in ("subject", "body"):
        if isinstance(body.get(k), str):
            mail[k] = body[k].strip()
    if isinstance(body.get("follow_up"), dict):
        for k in ("subject", "body"):
            if isinstance(body["follow_up"].get(k), str):
                mail["follow_up"][k] = body["follow_up"][k].strip()
    if "sent" in body:
        mail["sent_at"] = dt.date.today().isoformat() if body["sent"] else ""
    _check(owner, mail)
    _save(owner, data)
    return mail


def remove(owner: str, mail_id: str) -> None:
    data = load(owner)
    data["mails"] = [m for m in data["mails"] if m["id"] != mail_id]
    _save(owner, data)


def save_draft(owner: str, mail_id: str, which: str = "first", progress=lambda m: None) -> dict:
    """Put the email (or its follow-up) into Gmail Drafts. Claude only: the Gmail connector is signed in there."""
    data = load(owner)
    mail = _find(data, mail_id)
    part = mail["follow_up"] if which == "follow_up" else mail
    if not part.get("body"):
        raise ValueError("There's nothing to save yet.")
    progress("Saving the draft to your Gmail")
    reply = get_engine("claude:sonnet").run(DRAFT.format(to=mail["to_email"], subject=part["subject"], body=part["body"]),
                                            timeout=240, tools=[DRAFT_TOOL])
    try:
        got = parse_json_reply(reply)
    except Exception:
        got = {}
    url = str(got.get("view_url") or "")
    if not got.get("draft_id") and not url.startswith("https://mail.google.com/"):
        raise RuntimeError("Gmail didn't confirm the draft. Check that the Gmail connector is connected in claude.ai, then try again. "
                           f"({reply.strip()[:200]})")
    mail["drafts"][which] = {"id": str(got.get("draft_id") or ""), "url": url if url.startswith("https://mail.google.com/") else "",
                             "at": dt.datetime.now().isoformat(timespec="seconds")}
    _save(owner, data)
    return mail
