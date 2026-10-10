"""Find fresh jobs on sites the candidate points at, rank them against the profile, hand them to
the tailoring pipeline.

Order of preference per source: a public ATS job-board API (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Recruitee, Personio) -> schema.org JobPosting data in the page ->
the model reading the rendered page, where every link it returns must be a real link on that page.
Robots.txt is honoured. Sites whose terms forbid automated access are refused outright.
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import html as htmllib
import json
import re
import threading
import time
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .config import DATA, OUTPUT
from .engines import ENGINES, EngineError, parse_json_reply
from .jd import strip_html
from .keywords import taxonomy
from .fit import strong_skills
from .profile import load_profile, profile_text

SOURCES = DATA / "sources.json"
JOBS = OUTPUT / "_jobs" / "jobs.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 cv-tailor/1.0"
_lock = threading.Lock()

REFUSED = {
    "linkedin.com": "LinkedIn's terms forbid automated access and it restricts accounts that use it. Use a saved post search or job alert, then paste the JD.",
    "naukri.com": "Naukri's terms forbid automated access. Use its job alerts, then paste the JD.",
    "indeed.com": "Indeed's terms forbid scraping. Use its email alerts, then paste the JD.",
    "indeed.co.in": "Indeed's terms forbid scraping. Use its email alerts, then paste the JD.",
    "glassdoor.com": "Glassdoor's terms forbid scraping.",
    "glassdoor.co.in": "Glassdoor's terms forbid scraping.",
    "monster.com": "Monster's terms forbid scraping.",
    "foundit.in": "foundit's terms forbid scraping.",
}


# ---------- small helpers ----------

def _get(url: str, timeout: int = 15, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str, timeout: int = 15):
    return json.loads(_get(url, timeout))


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def refused_reason(url: str) -> str | None:
    h = _host(url)
    for dom, why in REFUSED.items():
        if h == dom or h.endswith("." + dom):
            return why
    return None


def robots_allows(url: str) -> bool:
    p = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(_get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=6, accept="text/plain").decode("utf-8", "replace").splitlines())
    except Exception:
        return True  # no robots.txt (or unreachable): nothing forbids it
    return rp.can_fetch("*", url)


def _iso(ts) -> str:
    """Epoch ms/s or an ISO-ish string -> YYYY-MM-DD, else ''."""
    if ts in (None, ""):
        return ""
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, dt.timezone.utc).date().isoformat()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(ts))
    return "-".join(m.groups()) if m else parse_posted(str(ts))


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_posted(raw: str, today: dt.date | None = None) -> str:
    """'3 days ago', 'Posted yesterday', '2w', '30+ days ago', 'Oct 1, 2026', '1 October 2026' -> ISO date."""
    if not raw:
        return ""
    today = today or dt.date.today()
    s = raw.lower().strip()
    if re.search(r"\b(just now|today|moments? ago|hours? ago|\d+\s*h\b|minutes? ago|\d+\s*mins?\b)", s):
        return today.isoformat()
    if "yesterday" in s:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*(d|day|days|w|wk|week|weeks|mo|month|months|y|yr|year|years)\b", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * (1 if unit.startswith("d") else 7 if unit.startswith("w") else 30 if unit.startswith("mo") or unit == "month" or unit == "months" else 365)
        return (today - dt.timedelta(days=days)).isoformat()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return "-".join(m.groups())
    m = re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s) or None
    if m and m.group(1) in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))).isoformat()
    m = re.search(r"(\d{1,2})\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})", s)
    if m and m.group(2) in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat()
    return ""


def _job(title, company, location, url, posted="", posted_raw="", description="", remote=None, method="", source=""):
    return {"id": hashlib.sha1((url or title + company).encode()).hexdigest()[:12],
            "title": (title or "").strip(), "company": (company or "").strip(), "location": (location or "").strip(),
            "url": url, "posted": posted, "posted_raw": posted_raw, "description": strip_html(description or "")[:12000],
            "remote": remote, "method": method, "source": source}


# ---------- public ATS job-board APIs ----------

def _slug_after(url: str, host_part: str) -> str:
    path = [p for p in urlparse(url).path.split("/") if p]
    return path[0] if path else ""


def from_ats(url: str) -> tuple[str, list[dict]] | None:
    h, path = _host(url), urlparse(url).path
    if "greenhouse.io" in h:
        slug = _slug_after(url, "greenhouse") if "boards" in h or "job-boards" in h else ""
        m = re.search(r"[?&]for=([\w-]+)", url)
        slug = m.group(1) if m else slug
        if not slug or slug == "embed":
            return None
        name = _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}").get("name", slug)
        data = _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", timeout=25)
        return "Greenhouse API", [
            _job(j["title"], name, (j.get("location") or {}).get("name", ""), j["absolute_url"],
                 _iso(j.get("first_published") or j.get("updated_at")), description=htmllib.unescape(j.get("content", "")),
                 method="greenhouse") for j in data.get("jobs", [])]
    if h == "jobs.lever.co" or h.endswith(".lever.co"):
        slug = _slug_after(url, "lever")
        data = _json(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=25)
        out = []
        for j in data:
            lists = "\n".join(f"{l.get('text', '')}:\n{strip_html(l.get('content', ''))}" for l in j.get("lists", []))
            cat = j.get("categories") or {}
            out.append(_job(j.get("text"), slug.replace("-", " ").title(), cat.get("location", ""), j.get("hostedUrl"),
                            _iso(j.get("createdAt")), description=(j.get("descriptionPlain", "") + "\n" + lists + "\n" + j.get("additionalPlain", "")),
                            remote=(j.get("workplaceType") == "remote"), method="lever"))
        return "Lever API", out
    if h == "jobs.ashbyhq.com":
        slug = _slug_after(url, "ashby")
        data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=25)
        return "Ashby API", [
            _job(j.get("title"), slug.replace("-", " ").title(), j.get("location", ""), j.get("jobUrl"),
                 _iso(j.get("publishedAt")), description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
                 remote=j.get("isRemote"), method="ashby") for j in data.get("jobs", [])]
    if h in ("careers.smartrecruiters.com", "jobs.smartrecruiters.com"):
        slug = _slug_after(url, "smartrecruiters")
        data = _json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", timeout=25)
        out = []
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append(_job(j.get("name"), (j.get("company") or {}).get("name", slug), ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                            f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", _iso(j.get("releasedDate")),
                            remote=loc.get("remote"), method="smartrecruiters") | {"detail_api": j.get("ref", "")})
        return "SmartRecruiters API", out
    if h == "apply.workable.com":
        slug = _slug_after(url, "workable")
        data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", timeout=25)
        return "Workable API", [
            _job(j.get("title"), data.get("name", slug), ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                 j.get("url") or j.get("shortlink"), _iso(j.get("published_on") or j.get("created_at")),
                 description=j.get("description", ""), remote=j.get("telecommuting"), method="workable")
            for j in data.get("jobs", [])]
    if h.endswith(".recruitee.com"):
        slug = h.split(".")[0]
        data = _json(f"https://{slug}.recruitee.com/api/offers/", timeout=25)
        return "Recruitee API", [
            _job(j.get("title"), j.get("company_name") or slug.title(), j.get("location", ""), j.get("careers_url"),
                 _iso(j.get("published_at") or j.get("created_at")),
                 description=(j.get("description", "") + "\n" + j.get("requirements", "")), remote=j.get("remote"),
                 method="recruitee") for j in data.get("offers", [])]
    if ".jobs.personio." in h:
        slug, tld = h.split(".")[0], h.rsplit(".", 1)[-1]
        root = ET.fromstring(_get(f"https://{slug}.jobs.personio.{tld}/xml?language=en", 25, "application/xml"))
        out = []
        for p in root.findall("position"):
            desc = "\n".join(f"{d.findtext('name', '')}\n{d.findtext('value', '')}" for d in p.findall("jobDescriptions/jobDescription"))
            out.append(_job(p.findtext("name"), slug.title(), p.findtext("office", ""),
                            f"https://{slug}.jobs.personio.{tld}/job/{p.findtext('id')}", _iso(p.findtext("createdAt")),
                            description=htmllib.unescape(desc), method="personio"))
        return "Personio feed", out
    return None


# ---------- any other page ----------

def _jsonld_jobs(blobs: list[str], base: str) -> list[dict]:
    found = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            t = o.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                found.append(o)
            for k in ("@graph", "itemListElement", "item", "mainEntity"):
                if k in o:
                    walk(o[k])

    for b in blobs:
        try:
            walk(json.loads(b))
        except json.JSONDecodeError:
            continue
    out = []
    for j in found:
        valid = j.get("validThrough")
        if valid and _iso(valid) and _iso(valid) < dt.date.today().isoformat():
            continue  # expired
        org = j.get("hiringOrganization") or {}
        loc = j.get("jobLocation") or {}
        loc = loc[0] if isinstance(loc, list) and loc else loc
        addr = (loc.get("address") if isinstance(loc, dict) else {}) or {}
        addr = addr if isinstance(addr, dict) else {}
        location = ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else "") if x)
        out.append(_job(j.get("title"), org.get("name", "") if isinstance(org, dict) else str(org), location,
                        urljoin(base, j.get("url") or base), _iso(j.get("datePosted")),
                        description=j.get("description", ""), remote=(j.get("jobLocationType") == "TELECOMMUTE"),
                        method="schema.org"))
    return out


def render_page(url: str, wait_ms: int = 9000) -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                page.wait_for_load_state("networkidle", timeout=wait_ms)
            except Exception:
                pass
            return page.evaluate("""() => ({
                title: document.title,
                text: document.body ? document.body.innerText : '',
                links: [...document.querySelectorAll('a[href]')].map(a => ({t: (a.innerText || a.title || '').trim().slice(0, 160), h: a.href}))
                        .filter(l => l.h.startsWith('http')),
                jsonld: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent),
                frames: [...document.querySelectorAll('iframe[src]')].map(f => f.src),
                html: document.documentElement.outerHTML.slice(0, 600000),
            })""")
        finally:
            browser.close()


EXTRACT_BRIEF = """Extract the job postings listed on this web page. Return JSON only:
{"jobs": [{"title": "", "company": "", "location": "", "url": "", "posted": ""}]}
Rules: "url" must be copied exactly from LINKS (the posting's own link). "posted" is the page's own
wording for when it was posted ("3 days ago", "Oct 2, 2026") or "" if the page doesn't say; never guess.
"company" is "" if the page doesn't say. Skip navigation, ads, and anything that is not a job. Up to 60 jobs."""


def from_page(url: str, engine_id: str = "") -> tuple[str, list[dict]]:
    from .engines import light_engine
    engine_id = engine_id or light_engine()
    page = render_page(url)
    jobs = _jsonld_jobs(page["jsonld"], url)
    if jobs:
        return "schema.org JobPosting", jobs
    links = {l["h"]: l["t"] for l in page["links"]}
    link_lines = "\n".join(f"{t} -> {h}" for h, t in list(links.items())[:600] if t)
    brief = f"{EXTRACT_BRIEF}\n\nPAGE: {url}\nTITLE: {page['title']}\n\nTEXT:\n<<<\n{page['text'][:24000]}\n>>>\n\nLINKS:\n{link_lines}"
    raw = parse_json_reply(ENGINES[engine_id].run(brief, timeout=90))
    out = []
    for j in raw.get("jobs") or []:
        u = j.get("url", "")
        if u not in links:  # a link the page doesn't contain is a link the model made up
            continue
        out.append(_job(j.get("title"), j.get("company"), j.get("location"), u, parse_posted(j.get("posted", "")),
                        j.get("posted", ""), method="page+model"))
    return "page read by model", out


WEB_BRIEF = """Use web search to find job postings and public "I'm hiring" posts from the LAST {days} DAYS that match:
{query}
Prefer the employer's own careers page or ATS link over aggregators. Public LinkedIn posts found through
search are allowed as links; do not try to open LinkedIn. Return JSON only:
{{"jobs": [{{"title": "", "company": "", "location": "", "url": "", "posted": "", "why": ""}}]}}
"posted" is the date or wording the result shows, "" if unknown; never guess a date. Up to 25 results."""


def from_websearch(query: str, days: int) -> tuple[str, list[dict]]:
    eng = ENGINES["claude:sonnet"]
    raw = parse_json_reply(eng.run(WEB_BRIEF.format(query=query, days=days), timeout=170, tools=["WebSearch"]))
    out = [_job(j.get("title"), j.get("company"), j.get("location"), j.get("url"), parse_posted(j.get("posted", "")),
                j.get("posted", ""), description=j.get("why", ""), method="web search (verify)")
           for j in raw.get("jobs") or [] if j.get("url", "").startswith("http")]
    return "web search", out


PEOPLE_BRIEF = """These are hiring managers and leaders the candidate follows. Use web search to find what each has
posted publicly in the LAST {days} DAYS announcing open roles in their team or company ("hiring", "join my team",
"we're looking for"), and the matching opening on the company's careers page if you can find it.
Search engines index public LinkedIn posts; search for them that way (e.g. the person's name plus "hiring",
or site:linkedin.com/posts/<handle>). Do not try to open LinkedIn itself.

PEOPLE:
{people}

Return JSON only: {{"jobs": [{{"title": "", "company": "", "location": "", "url": "", "posted": "", "why": "who posted it and what they said, one line"}}]}}
"posted" is the date the result shows, "" if unknown; never guess a date. Only roles posted in the window. Up to 30 results."""


def parse_people(value: str) -> list[dict]:
    """One person per line: Name | Company | LinkedIn URL (company and URL optional)."""
    out = []
    for line in value.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 1 and parts[0].startswith("http"):
            m = re.search(r"/in/([^/?#]+)", parts[0])
            handle = m.group(1) if m else parts[0]
            parts = [re.sub(r"[-_]+", " ", re.sub(r"-?[0-9a-f]{6,}$", "", handle)).title(), "", parts[0]]
        if parts and parts[0]:
            out.append({"name": parts[0], "company": parts[1] if len(parts) > 1 else "",
                        "url": parts[2] if len(parts) > 2 else ""})
    return out


def from_people(value: str, days: int) -> tuple[str, list[dict]]:
    people = parse_people(value)[:20]
    lines = "\n".join(f"- {p['name']}" + (f", {p['company']}" if p["company"] else "") + (f" ({p['url']})" if p["url"] else "")
                      for p in people)
    raw = parse_json_reply(ENGINES["claude:sonnet"].run(PEOPLE_BRIEF.format(days=days, people=lines), timeout=170,
                                                        tools=["WebSearch"]))
    out = [_job(j.get("title"), j.get("company"), j.get("location"), j.get("url"), parse_posted(j.get("posted", "")),
                j.get("posted", ""), description=j.get("why", ""), method="people you follow (verify)")
           for j in raw.get("jobs") or [] if j.get("url", "").startswith("http")]
    return "people you follow", out


# ---------- ranking ----------

TRACK_TITLES = {
    "ic_lead": r"full[\s-]?stack|product engineer|founding engineer|software engineer|software developer|\bsde\b|"
               r"front[\s-]?end|back[\s-]?end|react|python|tech(nical)? lead|lead engineer|member of technical staff|web developer",
    "engineering_manager": r"engineering manager|\bem\b|tech(nical)? lead manager|team lead|engineering lead|head of engineering|development manager",
    "product_manager": r"product manager|product owner|\bapm\b|technical product|\btpm\b|product lead|ai product",
    "delivery_lead": r"project lead|delivery lead|delivery manager|program manager|project manager|scrum master|engagement lead",
    "staff": r"staff engineer|staff software|principal engineer|principal software|architect",
    "forward_deployed": r"forward[\s-]?deployed|deployment engineer|applied ai|implementation (engineer|lead)|field engineer|"
                        r"customer engineer|resident (solutions )?architect|ai architect",
    "solutions_gtm": r"solutions? (engineer|architect)|pre[\s-]?sales|sales engineer|gtm engineer|growth engineer|"
                     r"developer advocate|devrel|partner (solutions )?engineer",
    "ai_engineer": r"\bai engineer|agentic|full[\s-]?stack ai|ai platform|llm engineer|genai engineer",
}
# Families that are a lateral or upward move from Lead: "Senior" here is not a step down.
LATERAL = re.compile(r"forward[\s-]?deployed|deployment|solutions?|applied ai|product manager|product owner|product lead|"
                     r"technical product|gtm|growth|developer advocate|devrel|ai engineer|agentic|ai architect|implementation", re.I)
NEGATIVE = re.compile(r"\b(intern|internship|junior|graduate|trainee|fresher|apprentice|associate|entry[\s-]level|qa|sdet|test engineer|data scientist|"
                      r"sales(?![\s-]*(engineer|solutions|architect))|marketing|account executive|recruiter|talent|designer|support engineer|android|ios|embedded|"
                      r"brand|public relations|pr\b|communications|legal|counsel|content\b|copywriter|"
                      r"firmware|mechanical|civil|electrical|nurse|teacher|accountant|hr\b)", re.I)
TECH_TITLE = re.compile(r"engineer|engineering|developer|programmer|architect|\bsde\b|\bswe\b|software|full[\s-]?stack|"
                        r"forward[\s-]?deployed|deployment|solutions?|applied ai|developer advocate|devrel|"
                        r"front[\s-]?end|back[\s-]?end|tech(nical)? lead|\bcto\b|product manager|product owner|product lead|"
                        r"\bapm\b|\btpm\b|program manager|project manager|project lead|delivery (lead|manager)|scrum master|"
                        r"member of technical staff|\bmts\b", re.I)
NO_SPONSOR = re.compile(r"(no|not able to|unable to|cannot|can't|do not|don't)\s+(offer\s+|provide\s+)?(visa\s+)?sponsor|"
                        r"must (already )?have (the )?right to work|citizens? only|without (the need for )?sponsorship|"
                        r"permanent residents? only", re.I)
SPONSOR = re.compile(r"visa sponsorship (is )?(available|offered|provided)|we (can |will )?sponsor|relocation (support|package|assistance)", re.I)
BENGALURU = re.compile(r"bengaluru|bangalore|blr\b", re.I)
INDIA = re.compile(r"bengaluru|bangalore|india|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|kolkata", re.I)
REMOTE = re.compile(r"\bremote\b|work from home|anywhere|distributed", re.I)
ABROAD_OK = re.compile(r"united kingdom|\buk\b|england|scotland|london|manchester|edinburgh|cambridge|dublin|ireland|"
                       r"amsterdam|netherlands|rotterdam|berlin|munich|hamburg|germany|paris|france|madrid|barcelona|spain|"
                       r"lisbon|portugal|warsaw|krakow|poland|stockholm|sweden|copenhagen|denmark|oslo|norway|helsinki|"
                       r"finland|brussels|belgium|vienna|austria|zurich|zürich|switzerland|milan|italy|prague|czech|"
                       r"tallinn|estonia|luxembourg|dubai|abu dhabi|\buae\b|united arab emirates|doha|qatar|riyadh|saudi|"
                       r"singapore|emea|europe|\beu\b", re.I)


def region(job: dict) -> str:
    """india | remote | abroad (a target country) | us | other | unknown, from the location field."""
    where = (job.get("location") or "").strip()
    if not where or where.upper() == "LOCATION":
        return "unknown"
    if INDIA.search(where):
        return "india"
    if US_ONLY.search(where):
        return "us"
    if ABROAD_OK.search(where):
        return "abroad"
    # "Remote" counts only when no other place is named: an ATS's remote flag on "Mexico" means remote
    # within Mexico, not remote from India.
    if re.search(r"\b(apac|asia|anywhere|global|worldwide)\b", where, re.I):
        return "remote"
    rest = re.sub(r"(?i)\b(remote|hybrid|work from home|wfh|first|only|friendly|or|and|in|the)\b|[^A-Za-z]", "", where)
    if (REMOTE.search(where) or job.get("remote")) and not rest:
        return "remote"
    return "other"


RANK_VERSION = 6
RANK_KEYS = ("tracks", "exclude_skills", "include_senior", "include_above", "candidate_years", "max_years_asked",
             "offices", "regions")


def with_profile(prefs: dict) -> dict:
    return prefs | {"_ptext": profile_text(load_profile())}


def rank_signature(prefs: dict, evidence: set[str]) -> str:
    # RANK_VERSION changes whenever the ranking rules do, so stored ranks never outlive the code that made them.
    blob = json.dumps([RANK_VERSION, {k: prefs.get(k) for k in RANK_KEYS}, sorted(evidence),
                       hashlib.sha1(prefs.get("_ptext", "").encode()).hexdigest()], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
US_ONLY = re.compile(r"remote[\s,-]*(us|usa|united states|canada|amer)\b|\b(us|usa)[\s-]*(remote|only)\b|united states|"
                     r"\bcanada\b|new york|\bnyc\b|san francisco|\bsf\b|seattle|austin|boston|chicago|atlanta|denver|"
                     r"los angeles|bay area|toronto|vancouver|washington|california|texas|colorado", re.I)


LEAD_TITLE = re.compile(r"\b(lead|staff|principal|manager|architect|founding)\b", re.I)
# Words every JD shares (communication, mentoring, scalability...): they say nothing about fit or level.
GENERIC = {"Communication", "Problem solving", "Ownership", "Product thinking", "Clean code", "Mentoring",
           "Stakeholder management", "Scalability", "Technical documentation", "Agile", "Requirements gathering",
           "Data visualisation", "Enterprise software", "Startup experience", "Technical leadership", "Code review",
           "Performance", "Automation", "Security", "Monitoring"}
BELOW_LEAD = re.compile(r"\b(senior|sr\.?|mid[\s-]*level)\b|\b(sde|swe|engineer|developer)[\s-]*(i{1,3}|[123])\b", re.I)


def needs_skill(job: dict, skill: str) -> bool:
    """A job 'needs' a skill when its title names it, its requirements list it, or the JD repeats it.
    One passing mention ("exposure to Java is a plus") does not count. JavaScript never counts as Java."""
    from .jd import sections
    tax = taxonomy()
    canon = tax.canonical_for(skill) or skill
    pats = tax.terms[canon].patterns if canon in tax.terms else [re.compile(r"(?<![A-Za-z0-9])" + re.escape(skill) + r"(?![A-Za-z0-9#+])", re.I)]
    if any(p.search(job["title"]) for p in pats):
        return True
    desc = job.get("description", "")[:8000]
    if not desc:
        return False
    if len({m.start() for p in pats for m in p.finditer(desc)}) >= 2:  # unique places, not alias hits
        return True
    return any(tag == "required" and any(p.search(line) for p in pats) for tag, line in sections(desc))


def job_skills(job: dict) -> list[str]:
    """Taxonomy skills in a posting, computed once per scan (regex over a long JD is the slow part)."""
    if "skills" not in job:
        job["skills"] = sorted(taxonomy().find(f"{job['title']}\n{job.get('description', '')[:6000]}"))
    return job["skills"]


def rank(job: dict, evidence: set[str], prefs: dict, sig: str | None = None) -> dict:
    """Score a posting; cached per posting under the settings that produced it."""
    cached = job.get("_r")
    if cached and (cached.get("sig") == sig or job.get("_trimmed")):
        return job | {k: v for k, v in cached.items() if k != "sig"}
    out = _rank(job, evidence, prefs)
    keep = {k: out[k] for k in ("match", "order", "reasons", "excluded", "areas", "skills") if k in out}
    return out | {"_r": keep | {"sig": sig}}


def _rank(job: dict, evidence: set[str], prefs: dict) -> dict:
    title, desc = job["title"], job.get("description", "")
    reasons, score = [], 0
    tracks = prefs.get("tracks") or ["ic_lead"]
    if NEGATIVE.search(title) or not TECH_TITLE.search(title):
        return job | {"match": 0, "reasons": ["not a target role"], "excluded": True}
    reg = region(job)
    wanted = set(prefs.get("regions") or ["remote", "bengaluru", "india", "world"])
    bucket = {"india": "bengaluru" if BENGALURU.search(job.get("location") or "") else "india",
              "abroad": "world", "remote": "remote", "unknown": "unknown"}.get(reg, "excluded")
    if bucket == "excluded" or (bucket != "unknown" and bucket not in wanted):
        return job | {"match": 0, "reasons": [f"outside your regions ({(job.get('location') or '')[:40]})"], "excluded": True}
    for skill in prefs.get("exclude_skills") or []:
        if needs_skill(job, skill):
            return job | {"match": 0, "reasons": [f"needs {skill}"], "excluded": True}
    from .fit import level_check
    level = level_check(title, desc, prefs.get("candidate_years", 7), evidence, prefs.get("max_years_asked", 11))
    if level["verdict"] == "above" and not prefs.get("include_above"):
        return job | {"match": 0, "reasons": ["above your level: " + "; ".join(level["reasons"])], "excluded": True}
    lead = bool(LEAD_TITLE.search(title))
    if not lead and BELOW_LEAD.search(title) and not LATERAL.search(title) and not prefs.get("include_senior"):
        return job | {"match": 0, "reasons": ["below lead level"], "excluded": True}
    target = any(re.search(TRACK_TITLES[t], title, re.I) for t in tracks if t in TRACK_TITLES)
    reasons.append("target role" if target else "engineering role")
    if lead:
        reasons.append("lead level")
    elif LATERAL.search(title):
        reasons.append("career move")
    elif BELOW_LEAD.search(title):
        reasons.append("senior, below lead")
    skills = job_skills(job)
    from .keywords import jd_keywords, score as keyword_score
    if len(desc) >= 300:
        # Same scorer, same keyword list rules as the Tailor screen's "most your profile can reach".
        score = keyword_score(jd_keywords(desc), prefs.get("_ptext", ""))["score"]
        reasons.append(f"you can cover {score}% of its keywords")
    else:
        score = 40
        reasons.append("no description to measure yet")
    order = score - (0 if target else 10)
    loc = f"{job.get('location', '')} {title} {desc[:3000]}"
    where = job.get("location", "") or ""
    # The location field decides; the description only when the posting gives no location at all.
    remote = (job.get("remote") or bool(REMOTE.search(where + " " + title))) and not US_ONLY.search(where)
    if (INDIA.search(where) or (not where.strip() and INDIA.search(loc))) and prefs.get("india", True):
        reasons.append("India")
    elif remote and prefs.get("remote", True):
        reasons.append("remote")
    elif SPONSOR.search(desc) or reg == "abroad":
        reasons.append("sponsors visas" if SPONSOR.search(desc) else "abroad, check visa")
    if NO_SPONSOR.search(desc):
        order -= 25
        reasons.append("says no sponsorship")
    if level["verdict"] == "stretch":
        order -= 10
        reasons += [f"stretch: {r}" for r in level["reasons"]]
    from .areas import tag
    return job | {"skills": skills, "match": max(0, min(100, score)), "order": order, "reasons": reasons, "excluded": False,
                  "areas": tag(job, prefs.get("offices", ""))}


# ---------- sources and scans ----------

def load_sources() -> list[dict]:
    return json.loads(SOURCES.read_text("utf-8")) if SOURCES.exists() else []


def save_sources(rows: list[dict]) -> None:
    SOURCES.write_text(json.dumps(rows, indent=2, ensure_ascii=False), "utf-8")


def add_source(value: str, kind: str = "url", label: str = "") -> dict:
    value = value.strip()
    if kind == "url":
        if not value.startswith("http"):
            value = "https://" + value
        why = refused_reason(value)
        if why:
            raise ValueError(why)
    rows = load_sources()
    if any(r["value"] == value for r in rows):
        raise ValueError("That source is already in the list.")
    default_label = {"url": _host(value) if kind == "url" else "", "websearch": value[:60],
                     "people": f"People to watch ({len(parse_people(value))})"}[kind]
    row = {"id": hashlib.sha1(value.encode()).hexdigest()[:10], "kind": kind, "value": value,
           "label": label or default_label, "last_scan": "", "last_count": 0}
    save_sources(rows + [row])
    return row


def remove_source(source_id: str) -> None:
    save_sources([r for r in load_sources() if r["id"] != source_id])


def update_source(source_id: str, value: str | None = None, label: str | None = None) -> dict:
    rows = load_sources()
    for r in rows:
        if r["id"] == source_id:
            if value is not None:
                r["value"] = value.strip()
                if r["kind"] == "people" and not label:
                    r["label"] = f"People to watch ({len(parse_people(r['value']))})"
            if label:
                r["label"] = label
            save_sources(rows)
            return r
    raise KeyError(source_id)


# ---------- favourite companies: scanned first, pinned at the top of Discover ----------

# Where a company's jobs live when it uses a public job board: (board, API to probe, board page to scan).
ATS_BOARDS = [
    ("Greenhouse", "https://boards-api.greenhouse.io/v1/boards/{s}/jobs", "https://boards.greenhouse.io/{s}"),
    ("Lever", "https://api.lever.co/v0/postings/{s}?mode=json&limit=1", "https://jobs.lever.co/{s}"),
    ("Ashby", "https://api.ashbyhq.com/posting-api/job-board/{s}", "https://jobs.ashbyhq.com/{s}"),
    ("SmartRecruiters", "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1", "https://careers.smartrecruiters.com/{s}"),
    ("Workable", "https://apply.workable.com/api/v1/widget/accounts/{s}", "https://apply.workable.com/{s}"),
]


def _board_answers(api: str, name: str) -> bool:
    """A board counts only if it lists openings now and, where it names its company, the name matches.
    Boards answer for placeholder accounts too (Workable has empty "samsung" and "akamai" accounts), so an
    answer alone proves nothing."""
    try:
        data = _json(api, timeout=8)
    except Exception:
        return False
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    def same(n) -> bool:
        n = re.sub(r"[^a-z0-9]", "", str(n or "").lower())
        return not n or n in want or want in n
    if isinstance(data, list):                      # Lever: the postings themselves
        return len(data) > 0
    if not isinstance(data, dict):
        return False
    if "totalFound" in data:                        # SmartRecruiters answers for any name
        posts = data.get("content") or []
        return data["totalFound"] > 0 and same((posts[0].get("company") or {}).get("name") if posts else "")
    jobs = data.get("jobs")                         # Greenhouse, Ashby, Workable
    return isinstance(jobs, list) and len(jobs) > 0 and same(data.get("name"))


def find_board(name: str) -> tuple[str, str] | None:
    """Probe the public job boards for a company by name (e.g. "Eleven Labs" -> elevenlabs, eleven-labs)."""
    base = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    slugs = list(dict.fromkeys(x for x in (base.replace(" ", ""), base.replace(" ", "-")) if x))
    cands = [(board, api.format(s=sl), page.format(s=sl)) for sl in slugs for board, api, page in ATS_BOARDS]
    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        hits = list(pool.map(lambda c: c if _board_answers(c[1], name) else None, cands))
    hit = next((h for h in hits if h), None)
    return (hit[0], hit[2]) if hit else None


def _company_from_url(url: str) -> str:
    h = _host(url)
    if any(b in h for b in ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com")):
        slug = _slug_after(url, "") or h.split(".")[0]
    else:
        parts = [x for x in h.split(".") if x not in ("www", "careers", "jobs", "apply", "boards", "com", "io", "co", "in", "org", "net", "ai")]
        slug = parts[0] if parts else h
    return slug.replace("-", " ").title()


def add_favourite(value: str) -> dict:
    """A company to watch: its job board if it has a public one, else a web search for its openings."""
    value = value.strip()
    if not value:
        raise ValueError("Type a company name, or paste its careers page link.")
    looks_like_link = value.startswith("http") or (" " not in value and re.search(r"\.[a-z]{2,}(/|$)", value))
    if looks_like_link:
        url = value if value.startswith("http") else "https://" + value
        why = refused_reason(url)
        if why:
            raise ValueError(why)
        company, kind, found_via = _company_from_url(url), "url", "its careers page"
    else:
        company = value
        board = find_board(value)
        if board:
            kind, url, found_via = "url", board[1], f"its {board[0]} board"
        else:
            kind, url, found_via = "websearch", f"open roles at {value} (any team), from its own careers page or job board", "a web search"
    rows = load_sources()
    for r in rows:
        if r["value"] == url or (r.get("favourite") and r.get("company", "").lower() == company.lower()):
            r.update(favourite=True, company=r.get("company") or company, found_via=r.get("found_via") or found_via)
            save_sources(rows)
            return r
    row = {"id": hashlib.sha1(url.encode()).hexdigest()[:10], "kind": kind, "value": url, "label": company,
           "favourite": True, "company": company, "found_via": found_via, "last_scan": "", "last_count": 0}
    save_sources(rows + [row])
    return row


def saved(days: int, prefs: dict) -> list[dict]:
    """Jobs from earlier scans, re-ranked and re-filtered, without touching the network."""
    state = _load_jobs()
    evidence = strong_skills(load_profile())
    prefs = with_profile(prefs)
    sig = rank_signature(prefs, evidence)
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    dismissed = set(state.get("dismissed", []))
    out, changed = [], False
    for jid, j in state["jobs"].items():
        if jid in dismissed or (j["posted"] and j["posted"] < cutoff):
            continue
        r = rank(j, evidence, prefs, sig)
        if r.get("_r") is not j.get("_r"):
            state["jobs"][jid]["_r"], changed = r["_r"], True
        if not r["excluded"]:
            out.append({k: v for k, v in r.items() if k not in ("description", "_r")})
    if changed:
        with _lock:
            _save_jobs(state)
    return _dedupe_sort(out)


def scan_one(src: dict, days: int) -> dict:
    t = time.time()
    try:
        if src["kind"] == "websearch":
            method, jobs = from_websearch(src["value"], days)
        elif src["kind"] == "people":
            method, jobs = from_people(src["value"], days)
        else:
            why = refused_reason(src["value"])
            if why:
                return {"source": src["id"], "error": why, "jobs": []}
            got = from_ats(src["value"])
            if got is None:
                if not robots_allows(src["value"]):
                    return {"source": src["id"], "error": "This site's robots.txt asks automated tools not to read that page.", "jobs": []}
                got = from_page(src["value"])
            method, jobs = got
        for j in jobs:
            j["source"] = src["label"]
            if src.get("favourite"):
                j["favourite"] = True
        return {"source": src["id"], "method": method, "jobs": jobs, "seconds": round(time.time() - t, 1)}
    except (EngineError, Exception) as e:  # one bad source must not sink the scan
        return {"source": src["id"], "error": f"{type(e).__name__}: {str(e)[:300]}", "jobs": [], "seconds": round(time.time() - t, 1)}


def _load_jobs() -> dict:
    return json.loads(JOBS.read_text("utf-8")) if JOBS.exists() else {"jobs": {}, "dismissed": []}


def _save_jobs(state: dict) -> None:
    JOBS.parent.mkdir(parents=True, exist_ok=True)
    JOBS.write_text(json.dumps(state, indent=1, ensure_ascii=False), "utf-8")


def scan(source_ids: list[str] | None, days: int, prefs: dict, budget_s: int = 60, progress=lambda m: None) -> dict:
    sources = [s for s in load_sources() if not source_ids or s["id"] in source_ids]
    url_sources = [s for s in sources if s["kind"] == "url"]
    web_sources = [s for s in sources if s["kind"] in ("websearch", "people")]
    progress(f"Scanning {len(sources)} source{'s' if len(sources) != 1 else ''} in parallel")
    profile = load_profile()
    evidence = strong_skills(profile)
    results = []
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        # Favourite companies go to the front of the queue.
        ordered = sorted(url_sources + web_sources, key=lambda s: not s.get("favourite"))
        futs = {pool.submit(scan_one, s, days): s for s in ordered}
        deadline = time.time() + budget_s + (120 if web_sources else 0)
        for fut in cf.as_completed(futs, timeout=None):
            s = futs[fut]
            res = fut.result()
            results.append(res)
            progress(f"{s['label']}: {len(res['jobs'])} jobs" + (f" ({res['error'][:80]})" if res.get("error") else ""))
            if time.time() > deadline:
                break
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    keep_from = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    prefs = with_profile(prefs)
    sig = rank_signature(prefs, evidence)
    progress(f"Ranking {sum(len(r['jobs']) for r in results)} postings against your profile")
    state = _load_jobs()
    now = dt.datetime.now().isoformat(timespec="seconds")
    fresh, new_ids = [], set()
    with _lock:
        for res in results:
            for j in res["jobs"]:
                if not j.get("url") or (j["posted"] and j["posted"] < keep_from):
                    continue  # older than any window the app offers: never analysed, never stored
                prev = state["jobs"].get(j["id"])
                if not prev:
                    new_ids.add(j["id"])
                j = rank(j, evidence, prefs, sig)
                j["first_seen"] = prev["first_seen"] if prev else now
                if j["excluded"]:
                    j = {k: v for k, v in j.items() if k != "description"} | {"_trimmed": True}
                state["jobs"][j["id"]] = j
                if j["posted"] and j["posted"] < cutoff:
                    continue
                fresh.append(j)
        state["jobs"] = {k: v for k, v in state["jobs"].items()
                         if (v.get("posted") or v.get("first_seen", now)[:10]) >= keep_from}
        kept_new = [j for j in fresh if j["id"] in new_ids and not j.get("excluded")]
        state.setdefault("scans", []).append({"at": now, "sources": len(results),
                                              "read": sum(len(r["jobs"]) for r in results), "new": len(kept_new)})
        state["scans"] = state["scans"][-30:]
        _save_jobs(state)
        rows = load_sources()
        for r in rows:
            for res in results:
                if res["source"] == r["id"]:
                    r["last_scan"], r["last_count"] = now, len(res["jobs"])
                    r["last_error"] = res.get("error", "")
                    r["method"] = res.get("method", "")
        save_sources(rows)
    dismissed = set(state.get("dismissed", []))
    fresh = _dedupe_sort([{k: v for k, v in j.items() if k not in ("description", "_r")}
                          for j in fresh if j["id"] not in dismissed and not j.get("excluded")])
    return {"jobs": fresh, "results": [{k: v for k, v in r.items() if k != "jobs"} | {"count": len(r["jobs"])} for r in results],
            "cutoff": cutoff}


def _dedupe_sort(jobs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for j in sorted(jobs, key=lambda j: (j.get("order", j["match"]), j["posted"] or "0000"), reverse=True):
        key = (j["title"].lower().strip(), j["company"].lower().strip(), j["location"].lower().strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def scans() -> list[dict]:
    return _load_jobs().get("scans", [])


def dismiss(job_id: str) -> None:
    with _lock:
        state = _load_jobs()
        state.setdefault("dismissed", []).append(job_id)
        _save_jobs(state)


def job_detail(job_id: str) -> dict:
    """Full JD for the tailoring form. Most ATS APIs already returned it; otherwise read the page."""
    state = _load_jobs()
    j = state["jobs"].get(job_id)
    if not j:
        raise KeyError(job_id)
    if j.get("_trimmed") or len(j.get("description", "")) < 400:
        if j.get("detail_api"):
            d = _json(j["detail_api"])
            secs = (d.get("jobAd") or {}).get("sections") or {}
            j["description"] = strip_html("\n\n".join(f"{v.get('title', '')}\n{v.get('text', '')}" for v in secs.values() if isinstance(v, dict)))
        elif not refused_reason(j["url"]) and robots_allows(j["url"]):
            page = render_page(j["url"], wait_ms=6000)
            ld = _jsonld_jobs(page["jsonld"], j["url"])
            if ld and len(ld[0]["description"]) > 200:
                j["description"] = ld[0]["description"]
                j["posted"] = j["posted"] or ld[0]["posted"]
                j["company"] = j["company"] or ld[0]["company"]
            else:
                j["description"] = page["text"][:20000]
        with _lock:
            state["jobs"][job_id] = j
            _save_jobs(state)
    return j
