"""People to write to about an application: the managers, product leads and recruiters who hire for the role,
found by web search. An email address is kept only when it was published on a page, with that page's link:
nothing is guessed or built from a naming pattern. People without a public address keep their public profile
link, for a message there instead."""
import datetime as dt
import json
import re

from .coldmail import EMAIL_RE
from .config import output_dir
from .engines import get_engine, parse_json_reply
from .untrusted import fence

FILE = "contacts.json"

BRIEF = """Find the people at {company} a candidate should write to about this opening: "{role}"{where}.

Search the web properly: run at least six searches before you answer, for example
  "{company}" {function} manager {city}
  "{company}" head of {function} {country}
  "{company}" talent acquisition {city}
  "{company}" recruiter {country}
  site:linkedin.com/in "{company}" {city} {function} manager
  site:linkedin.com/in "{company}" talent acquisition
  "{company}" careers email
plus searches for any team, product or manager the job description names. Search result titles and snippets
for LinkedIn profiles are a fine source (do not open LinkedIn itself).

Who to include, most useful first:
- the hiring manager or the manager of the team this role joins (for example the engineering manager or head of
  engineering for an engineering role, the product lead for a product role, the head of business analysis,
  delivery or operations for an analyst role);
- leads or senior people on that team;
- the HR head, talent acquisition lead or recruiters who hire for this kind of role, ideally in {region}.
Include everyone you find who plausibly fits, up to 12, with a "confidence": "high" when a source clearly shows
them in that role at this company now, "medium" when it's likely (for example a profile snippet), "low" when the
source may be old. An empty list is only right if the searches truly found nobody.

Contact details, the strictest rule here:
- An email goes in only if you saw that exact address published on a page or in a snippet (a careers page, a post
  saying "send your CV to", a team page, the job ad). Put that URL in "email_source".
- Never construct, guess or complete an email address from a name and a domain pattern. If you didn't see it
  published, leave "email" empty; the profile link is enough.
- Generic hiring inboxes (careers@, talent@, jobs@) count when the company publishes them.
- No phone numbers, home addresses or personal social accounts unrelated to work.

Return JSON only:
{{"people": [{{"name": "...", "title": "their title", "team": "team or area, if known",
               "location": "city, if known", "why": "one line: why they're relevant to this role",
               "confidence": "high" | "medium" | "low",
               "email": "a published address, or empty", "email_source": "URL where the address appears, or empty",
               "profile_url": "a public profile URL (LinkedIn, GitHub, personal site), or empty",
               "source": "URL that shows them in this role"}}],
  "inboxes": [{{"email": "careers@...", "what": "what it's for, per the page", "source": "URL"}}]}}

JOB DESCRIPTION (for the team, location and hiring manager it may name):
<<<
{jd}
>>>"""


def load(app_id: str) -> dict | None:
    p = output_dir() / app_id / FILE
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def load_all() -> dict:
    return {d.name: json.loads((d / FILE).read_text("utf-8")) for d in output_dir().iterdir() if (d / FILE).exists()}


def _url(u) -> str:
    u = str(u or "").strip()
    return u if re.match(r"https?://", u) else ""


def _email(e, source) -> str:
    """An address survives only when it has the page it was published on."""
    e = str(e or "").strip().strip(".,;<>()[]").lower()
    return e if EMAIL_RE.match(e) and _url(source) else ""


def find(app_id: str, progress=lambda m: None) -> dict:
    folder = output_dir() / app_id
    report = json.loads((folder / "report.json").read_text("utf-8"))
    jd = (folder / "jd.txt").read_text("utf-8")
    loc = (report.get("job") or {}).get("location", "")
    progress(f"Searching the web for the people who hire for this role at {report['company']} (about a minute)")
    from . import strategy
    home = strategy.home()
    country = home["country"] or "India"
    city = (loc.split(",")[0].strip() or home["city"] or country) if loc else (home["city"] or country)
    function = re.sub(r"(?i)\b(junior|jr\.?|associate|senior|sr\.?|lead|principal|staff|head of|manager|i{1,3})\b|[,(].*$", "",
                      report["role"]).strip() or report["role"]
    brief = BRIEF.format(company=report["company"], role=report["role"], where=f" in {loc}" if loc else "",
                         city=city, country=country, function=function, region=loc or "the role's location", jd=fence(jd[:8000]))
    raw = parse_json_reply(get_engine("claude:sonnet").run(brief, timeout=420, tools=["WebSearch"]))
    people, seen = [], set()
    for p in raw.get("people") or []:
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            continue
        key = p["name"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        email = _email(p.get("email"), p.get("email_source"))
        people.append({"name": p["name"].strip(), "title": str(p.get("title") or "").strip(), "team": str(p.get("team") or "").strip(),
                       "location": str(p.get("location") or "").strip(), "why": str(p.get("why") or "").strip(),
                       "confidence": p.get("confidence") if p.get("confidence") in ("high", "medium", "low") else "medium",
                       "email": email, "email_source": _url(p.get("email_source")) if email else "",
                       "profile_url": _url(p.get("profile_url")), "source": _url(p.get("source"))})
    inboxes = []
    for b in raw.get("inboxes") or []:
        if isinstance(b, dict):
            e = _email(b.get("email"), b.get("source"))
            if e and e not in {x["email"] for x in inboxes}:
                inboxes.append({"email": e, "what": str(b.get("what") or "").strip(), "source": _url(b.get("source"))})
    rank = {"high": 0, "medium": 1, "low": 2}
    people.sort(key=lambda x: (not x["email"], rank[x["confidence"]]))
    out = {"found_at": dt.datetime.now().isoformat(timespec="seconds"), "company": report["company"],
           "people": people[:12], "inboxes": inboxes[:4]}
    (folder / FILE).write_text(json.dumps(out, indent=2, ensure_ascii=False), "utf-8")
    return out
