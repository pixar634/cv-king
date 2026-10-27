"""Find fresh jobs on sites the candidate points at, rank them against the profile, hand them to
the tailoring pipeline.

Order of preference per source: a public ATS job-board API (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Recruitee, Personio) -> schema.org JobPosting data in the page ->
the model reading the rendered page, where every link it returns must be a real link on that page.
Robots.txt is honoured. Sites whose terms forbid automated access are refused outright.
"""
import concurrent.futures as cf
import contextvars
import datetime as dt
import hashlib
import html as htmllib
import json
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from . import strategy, taste
from .config import data_dir, output_dir
from .engines import ENGINES, EngineError, parse_json_reply
from .jd import strip_html
from .untrusted import fence
from .keywords import MIN_TERMS, taxonomy
from .fit import strong_skills
from .profile import load_profile, profile_text



def _sources_file() -> Path:
    return data_dir() / "sources.json"


def _jobs_file() -> Path:
    return output_dir() / "_jobs" / "jobs.json"


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 jobmagnet/1.0"
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
    if not public_url(url):
        raise ValueError(f"Not a public web address: {url[:120]}")
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


def public_url(url: str, resolve: bool = True) -> bool:
    """An http(s) link to a public address. A careers page's own data can name file:///... or http://127.0.0.1/...,
    and opening that would put a local file or a local service's reply in front of the model."""
    import ipaddress
    import socket
    try:
        p = urlparse(url or "")
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.strip("[]").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        if not resolve:
            return True
        try:
            addrs = [ipaddress.ip_address(a[4][0].split("%")[0]) for a in socket.getaddrinfo(host, None)]
        except (OSError, ValueError):
            return True   # unresolvable here: the browser won't reach anything local either
    return not any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast
                   or a.is_unspecified for a in addrs)


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
    url = url if public_url(url or "", resolve=False) else ""   # file:// or a local address: no link at all
    return {"id": hashlib.sha1((url or title + company).encode()).hexdigest()[:12],
            "title": (title or "").strip(), "company": (company or "").strip(), "location": (location or "").strip(),
            "url": url, "posted": posted, "posted_raw": posted_raw, "description": strip_html(description or "")[:12000],
            "remote": remote, "method": method, "source": source}


# ---------- public ATS job-board APIs ----------

def _slug_after(url: str, host_part: str) -> str:
    path = [p for p in urlparse(url).path.split("/") if p]
    return path[0] if path else ""


def from_ats(url: str) -> tuple[str, list[dict]] | None:
    h = _host(url)
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
    if not public_url(url):
        raise ValueError(f"Not a public web page: {url[:120]}")
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
    brief = f"{EXTRACT_BRIEF}\n\nPAGE: {url}\nTITLE: {fence(page['title'])}\n\nTEXT:\n<<<\n{fence(page['text'][:24000])}\n>>>\n\nLINKS:\n{link_lines}"
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


# ---------- Indeed, through Indeed's own Claude connector ----------
# Search results carry job IDs that only live inside one Claude run, so the details are read in the same run.
# The run may use just these two Indeed tools: never get_resume (the candidate's own Indeed profile) or anything else.
INDEED_TOOLS = ["mcp__claude_ai_Indeed__search_jobs", "mcp__claude_ai_Indeed__get_job_details"]
INDEED_DETAILS = 5        # postings read in full per search: Indeed allows only so many calls a minute
INDEED_BRIEF = """Use the Indeed tools to find and read jobs.
1. Call search_jobs once with: search "{query}", location "{location}", country_code "{cc}", job_type "fulltime".
2. For each of the first {n} results, call get_job_details with that result's Job Id.
3. Reply with JSON only:
{{"jobs": [{{"title": "", "company": "", "location": "", "posted": "the Posted on value", "url": "the View Job URL",
            "compensation": "", "description": "the full description from get_job_details, up to 4000 characters, copied word for word, not summarised"}}]}}
Copy every value from the tools' results. Never invent or fill in a value; leave it empty if the tool didn't give it.
Skip a job whose details fail to load. Use no other tools.
If the Indeed tools are missing or a tool call fails (for example a rate limit), stop and reply with JSON only:
{{"jobs": [], "error": "the exact error message you saw"}}"""


def _indeed_date(raw: str) -> str:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            pass
    return parse_posted(raw or "")


def from_indeed(value: str, days: int) -> tuple[str, list[dict]]:
    """value is "query | location" (location defaults to the candidate's city). Indeed limits calls per account per
    minute: when it says to wait, wait (up to 90 seconds) and try once more, then report its message as the error."""
    query, _, location = (x.strip() for x in value.partition("|"))
    eng = ENGINES["claude:sonnet"]
    home = strategy.home()
    brief = INDEED_BRIEF.format(query=query, location=location or home["city"] or home["country"],
                                cc=home["country_code"], n=INDEED_DETAILS)
    for attempt in (1, 2):
        raw = parse_json_reply(eng.run(brief, timeout=420, tools=INDEED_TOOLS))
        err = str(raw.get("error") or "")
        if raw.get("jobs") or not err:
            break
        wait = re.search(r"(\d+)\s*seconds?", err)
        if attempt == 1 and re.search(r"rate limit|429|try again", err, re.I):
            time.sleep(min(90, int(wait.group(1)) + 3 if wait else 60))
            continue
        raise EngineError(f"Indeed: {err[:240]}")
    out = []
    for j in raw.get("jobs") or []:
        if not (j.get("title") and str(j.get("url", "")).startswith("http")):
            continue
        job = _job(j["title"], j.get("company"), j.get("location"), j["url"], _indeed_date(j.get("posted", "")),
                   j.get("posted", ""), description=j.get("description", ""), method="indeed")
        # Indeed's links change every run: key the posting on what it is, so a rescan doesn't make it "new" again
        job["id"] = hashlib.sha1(f"indeed|{job['title']}|{job['company']}|{job['location']}".lower().encode()).hexdigest()[:12]
        if j.get("compensation") and j["compensation"] != "N/A":
            job["compensation"] = str(j["compensation"])
        out.append(job)
    return "Indeed connector", out


def ensure_indeed_sources(prefs: dict) -> list[dict]:
    """One Indeed search per career track you rank for, in your city. A track switched on later gets its search the
    next time you scan Indeed; a search you deleted stays deleted."""
    rows = load_sources()
    have = [r for r in rows if r["kind"] == "indeed"]
    home = strategy.home()
    location = home["city"] if "city" in regions(prefs) and home["city"] else home["country"] or home["city"]
    titles = strategy.search_titles()
    queries = list(dict.fromkeys(titles[t][0] for t in (prefs.get("tracks") or list(titles)) if titles.get(t)))
    made = set(_load_jobs().get("indeed_made", [])) | {r["value"].split("|")[0].strip().lower() for r in have}
    new = [{"id": hashlib.sha1(f"indeed:{q}|{location}".encode()).hexdigest()[:10], "kind": "indeed",
            "value": f"{q} | {location}", "label": f"Indeed: {q}, {location}", "last_scan": "", "last_count": 0}
           for q in queries if q.lower() not in made]
    if new:
        save_sources(rows + new)
        with _lock:
            state = _load_jobs()
            state["indeed_made"] = sorted(made | {q.lower() for q in queries})
            _save_jobs(state)
    return have + new


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

NO_SPONSOR = re.compile(r"(no|not able to|unable to|cannot|can't|do not|don't)\s+(offer\s+|provide\s+)?(visa\s+)?sponsor|"
                        r"must (already )?have (the )?right to work|citizens? only|without (the need for )?sponsorship|"
                        r"permanent residents? only", re.I)
SPONSOR = re.compile(r"visa sponsorship (is )?(available|offered|provided)|we (can |will )?sponsor|relocation (support|package|assistance)", re.I)
INDIA = re.compile(r"bengaluru|bangalore|india|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|kolkata", re.I)
REMOTE = re.compile(r"\bremote\b|work from home|anywhere|distributed", re.I)
ABROAD_OK = re.compile(r"united kingdom|\buk\b|england|scotland|london|manchester|edinburgh|cambridge|dublin|ireland|"
                       r"amsterdam|netherlands|rotterdam|berlin|munich|hamburg|germany|paris|france|madrid|barcelona|spain|"
                       r"lisbon|portugal|warsaw|krakow|poland|stockholm|sweden|copenhagen|denmark|oslo|norway|helsinki|"
                       r"finland|brussels|belgium|vienna|austria|zurich|zürich|switzerland|milan|italy|prague|czech|"
                       r"tallinn|estonia|luxembourg|dubai|abu dhabi|\buae\b|united arab emirates|doha|qatar|riyadh|saudi|"
                       r"singapore|emea|europe|\beu\b", re.I)


def home_country_pattern() -> re.Pattern:
    """The candidate's own country (and city): India's cities when it's India, else the names themselves."""
    h = strategy.home()
    if h["country"].lower() in ("india", ""):
        return INDIA
    return strategy.any_of([h["country"], *h["city_names"]]) or INDIA


def region(job: dict) -> str:
    """home (the candidate's country) | remote | abroad (a target country) | us | other | unknown, from the location field."""
    where = (job.get("location") or "").strip()
    if not where or where.upper() == "LOCATION":
        return "unknown"
    if home_country_pattern().search(where):
        return "home"
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


RANK_VERSION = 15
RANK_KEYS = ("tracks", "exclude_skills", "include_senior", "include_above", "candidate_years", "max_years_asked",
             "offices", "regions", "places")


# A country's other names and its main tech cities, with their other spellings: postings often name only the city.
COUNTRY_PLACES = {
    "united kingdom": ["United Kingdom", "UK", "U.K.", "Great Britain", "England", "Scotland", "Wales", "London",
                       "Manchester", "Edinburgh", "Cambridge", "Oxford", "Bristol", "Glasgow", "Belfast", "Leeds",
                       "Birmingham", "Reading"],
    "germany": ["Germany", "Deutschland", "Berlin", "Munich", "München", "Muenchen", "Hamburg", "Frankfurt", "Cologne",
                "Köln", "Koeln", "Stuttgart", "Düsseldorf", "Dusseldorf", "Leipzig", "Karlsruhe"],
    "netherlands": ["Netherlands", "The Netherlands", "Holland", "Amsterdam", "Rotterdam", "Utrecht", "Eindhoven",
                    "The Hague", "Den Haag", "Delft", "Leiden"],
    "poland": ["Poland", "Polska", "Warsaw", "Warszawa", "Kraków", "Krakow", "Wrocław", "Wroclaw", "Gdańsk", "Gdansk",
               "Poznań", "Poznan", "Łódź", "Lodz", "Katowice"],
    "ireland": ["Ireland", "Dublin", "Cork", "Galway", "Limerick"],
    "luxembourg": ["Luxembourg"],
    "finland": ["Finland", "Suomi", "Helsinki", "Espoo", "Tampere", "Oulu", "Turku"],
    "sweden": ["Sweden", "Stockholm", "Gothenburg", "Göteborg", "Malmö", "Malmo"],
    "denmark": ["Denmark", "Copenhagen", "København", "Aarhus"],
    "norway": ["Norway", "Oslo"],
    "spain": ["Spain", "Madrid", "Barcelona", "Valencia", "Málaga", "Malaga"],
    "portugal": ["Portugal", "Lisbon", "Lisboa", "Porto"],
    "france": ["France", "Paris", "Lyon", "Toulouse"],
    "belgium": ["Belgium", "Brussels", "Bruxelles", "Antwerp", "Ghent"],
    "austria": ["Austria", "Vienna", "Wien", "Graz"],
    "switzerland": ["Switzerland", "Zurich", "Zürich", "Geneva", "Genève", "Basel", "Lausanne"],
    "czech republic": ["Czech Republic", "Czechia", "Prague", "Praha", "Brno"],
    "estonia": ["Estonia", "Tallinn"],
    "italy": ["Italy", "Milan", "Milano", "Rome", "Turin"],
}
PLACE_ALIASES = {k: k for k in COUNTRY_PLACES} | {
    "uk": "united kingdom", "u.k": "united kingdom", "great britain": "united kingdom", "gb": "united kingdom",
    "deutschland": "germany", "the netherlands": "netherlands", "holland": "netherlands", "nl": "netherlands",
    "polska": "poland", "suomi": "finland", "czechia": "czech republic"}


def place_names(place: str) -> list[str]:
    """Every name a place in your list matches: a country brings its other names and main cities."""
    out = []
    for p in (x.strip() for x in place.split(",")):
        country = PLACE_ALIASES.get(p.lower().rstrip("."))
        if country:
            out += COUNTRY_PLACES[country]
        elif len(p) > 2:
            out.append(p)
    return out


def places_pattern(prefs: dict) -> re.Pattern | None:
    """Places you'd move to ("Amsterdam, Netherlands", "Luxembourg"): a job in one is wanted whatever the region
    switches say. Each part of a place counts, and a country matches its main cities, so a posting that names only
    "Berlin" matches "Germany"."""
    parts = [n for place in prefs.get("places") or [] for n in place_names(place)]
    return strategy.any_of(list(dict.fromkeys(parts))) if parts else None
# Region filter keys. Older settings said "bengaluru" and "india" for the city and the rest of the country.
REGION_KEYS = {"bengaluru": "city", "india": "country"}


def regions(prefs: dict) -> list[str]:
    return list(dict.fromkeys(REGION_KEYS.get(r, r) for r in (prefs.get("regions") or ["city", "country", "remote", "world"])))


def with_profile(prefs: dict) -> dict:
    profile = load_profile()
    return prefs | {"_ptext": profile_text(profile), "_held": [r.get("title", "") for r in profile["experience"]],
                    "_floor": strategy.calibrated_rank(profile)}


def rank_signature(prefs: dict, evidence: set[str]) -> str:
    # RANK_VERSION changes whenever the ranking rules do, so stored ranks never outlive the code that made them;
    # the strategy's own file changes them too (new tracks, a new level).
    sp = strategy.path()
    from .sponsors import _lists
    blob = json.dumps([RANK_VERSION, _lists().get("fetched"), {k: prefs.get(k) for k in RANK_KEYS}, sorted(evidence),
                       hashlib.sha1(prefs.get("_ptext", "").encode()).hexdigest(),
                       sp.stat().st_mtime if sp.exists() else 0], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
US_ONLY = re.compile(r"remote[\s,-]*(us|usa|united states|canada|amer)\b|\b(us|usa)[\s-]*(remote|only)\b|united states|"
                     r"\bcanada\b|new york|\bnyc\b|san francisco|\bsf\b|seattle|austin|boston|chicago|atlanta|denver|"
                     r"los angeles|bay area|toronto|vancouver|washington|california|texas|colorado", re.I)


# Words every JD shares (communication, mentoring, scalability...): they say nothing about fit or level.
GENERIC = {"Communication", "Problem solving", "Ownership", "Product thinking", "Clean code", "Mentoring",
           "Stakeholder management", "Scalability", "Technical documentation", "Agile", "Requirements gathering",
           "Data visualisation", "Enterprise software", "Startup experience", "Technical leadership", "Code review",
           "Performance", "Automation", "Security", "Monitoring"}


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
    keep = {k: out[k] for k in ("match", "order", "reasons", "excluded", "areas", "skills", "estimate", "unscored", "open")
            if k in out}
    return out | {"_r": keep | {"sig": sig}}


def _rank(job: dict, evidence: set[str], prefs: dict) -> dict:
    title, desc = job["title"], job.get("description", "")
    reasons, score = [], 0
    tracks = prefs.get("tracks") or list(strategy.tracks())
    relevant, ruled_out = strategy.relevant_pattern(), strategy.excluded_pattern()
    if (ruled_out and ruled_out.search(title)) or (relevant and not relevant.search(title)):
        return job | {"match": 0, "reasons": ["not a target role"], "excluded": True}
    reg = region(job)
    wanted = set(regions(prefs))
    city = strategy.city_pattern()
    bucket = {"home": "city" if city and city.search(job.get("location") or "") else "country",
              "abroad": "world", "remote": "remote", "unknown": "unknown"}.get(reg, "excluded")
    places = places_pattern(prefs)
    if places and reg != "home" and places.search(job.get("location") or ""):
        bucket, wanted = "place", wanted | {"place"}
    if bucket == "excluded" or (bucket != "unknown" and bucket not in wanted):
        return job | {"match": 0, "reasons": [f"outside your regions ({(job.get('location') or '')[:40]})"], "excluded": True}
    for skill in prefs.get("exclude_skills") or []:
        if needs_skill(job, skill):
            return job | {"match": 0, "reasons": [f"needs {skill}"], "excluded": True}
    # Open to you at all: full-time, a normal working day at home, remote that hires where you live, abroad with a visa.
    from .gate import openness
    o = openness(desc, title, job.get("location") or "", job.get("remote"))
    if o["state"] == "closed":
        return job | {"match": 0, "reasons": [o["why"]], "excluded": True, "open": "closed"}
    from .fit import level_check
    years = prefs.get("candidate_years", 3)
    level = level_check(title, desc, years, evidence, prefs.get("max_years_asked", years + 2),
                        {"experience": [{"title": t} for t in prefs.get("_held", [])]})
    if level["verdict"] == "above" and not prefs.get("include_above"):
        return job | {"match": 0, "reasons": ["above your level: " + "; ".join(level["reasons"])], "excluded": True}
    # Below the candidate's level only when the title says so; a sideways move into another kind of role isn't a step down.
    # A services firm's Lead is a product company's Senior, so that title is at your level, not below it.
    mine = strategy.level()["rank"]
    floor = min(mine, prefs.get("_floor", mine))
    named = strategy.title_level(title)
    sideways = strategy.lateral(title)
    if named is not None and named < floor and not sideways and not prefs.get("include_senior"):
        return job | {"match": 0, "reasons": ["below your level"], "excluded": True}
    target = bool(strategy.matching_tracks(title, tracks))
    reasons.append("target role" if target else "related role")
    if named is not None and floor <= named <= mine:
        reasons.append("at your level")
    elif named is not None and named > mine:
        reasons.append("a step up")
    elif sideways:
        reasons.append("career move")
    elif named is not None and named < floor:
        reasons.append("below your level")
    skills = job_skills(job)
    from .keywords import jd_keywords, score as keyword_score
    kws = jd_keywords(desc) if len(desc) >= 300 else {}
    # Too few specific skills to measure (or no description at all): no number, listed apart in Discover. Generic
    # terms every posting shares (Performance, Documentation) don't count toward the floor.
    unscored = len([k for k in kws if k not in GENERIC]) < MIN_TERMS
    if not unscored:
        # Same scorer, same keyword list rules as the Tailor screen's "most your profile can reach".
        score = keyword_score(kws, prefs.get("_ptext", ""))["score"]
        reasons.append(f"you can cover {score}% of its keywords")
        # A short text is often a search snippet, not the posting: the full page can score quite differently.
        estimate = len(desc) < SNIPPET
    else:
        score, estimate = 40, True
        reasons.append("no description to measure yet" if len(desc) < 300 else "names too few skills to measure")
        if not target:   # with nothing to measure, only a title that is one of your tracks earns a place
            return job | {"match": 0, "reasons": ["not scored, and the title isn't one of your tracks"], "excluded": True}
    order = score - (0 if target else 10)
    # Level decides the order among jobs that match equally: at your level first, a step down last.
    if named is not None and floor <= named <= mine:
        order += 8
    elif named is not None and named < floor and not sideways:
        order -= 12
    loc = f"{job.get('location', '')} {title} {desc[:3000]}"
    where = job.get("location", "") or ""
    # The location field decides; the description only when the posting gives no location at all.
    remote = (job.get("remote") or bool(REMOTE.search(where + " " + title))) and not US_ONLY.search(where)
    home = home_country_pattern()
    if (home.search(where) or (not where.strip() and home.search(loc))) and prefs.get("india", True):
        reasons.append(strategy.home()["country"] or "home")
    elif remote and prefs.get("remote", True):
        reasons.append("remote")
    elif SPONSOR.search(desc) or reg == "abroad":
        reasons.append("sponsors visas" if SPONSOR.search(desc) else "abroad, check visa")
    # The UK and Dutch sponsor registers settle most "doesn't say" cases for jobs there.
    from .sponsors import check as sponsor_check
    reg_hit = sponsor_check(job.get("company") or "", job.get("location") or "") if reg != "home" else None
    if reg_hit and reg_hit["listed"]:
        reasons.append("licensed UK sponsor" if reg_hit["register"] == "uk" else "IND recognised sponsor")
        order += 6
        if o["state"] == "unclear":
            o = {"state": "open", "why": "on a sponsor register"}
    elif reg_hit and reg_hit["register"] == "uk":
        reasons.append("unlikely: not a licensed UK sponsor")
        order -= 15
    elif reg_hit:
        reasons.append("not an IND recognised sponsor (the EU Blue Card still works)")
    # Open first; one that doesn't say is labelled and sits lower (opening it reads the full posting again).
    if o["state"] == "unclear":
        order -= 8
        reasons.append(o["why"])
    elif o["why"] != "at home":
        order += 4
    if level["verdict"] == "stretch":
        order -= 10
        reasons += [f"stretch: {r}" for r in level["reasons"]]
    from .areas import tag
    return job | {"skills": skills, "match": max(0, min(100, score)), "estimate": estimate, "unscored": unscored, "order": order,
                  "reasons": reasons, "excluded": False, "open": o["state"],
                  "areas": tag(job, prefs.get("offices", ""))}


# ---------- sources and scans ----------

# ---------- remote and Europe job boards with public APIs ----------
# Each board asks for credit and a link back: every card says "via <board>" and opens the board's own posting.

BOARDS = {
    "remotive": "Remotive: remote jobs",
    "jobicy": "Jobicy: remote jobs in Europe",
    "himalayas": "Himalayas: remote jobs",
    "arbeitnow": "Arbeitnow: Germany and Europe, many with visa sponsorship",
    "workinfinland": "Work in Finland: jobs in Finland open to international talent",
}

WIF_API = "https://www.workinfinland.com/api/jobs/?"   # the feed behind workinfinland.com/en/open-jobs; robots.txt allows it
DESCRIBED_HOSTS = ("thehub.fi", "thehub.io")           # posting sites that allow reading and carry schema.org job data
MAX_DESCRIBED = 60


def from_work_in_finland() -> list[dict]:
    """Work in Finland lists every job with its title, employer, city and link, but no description. Jobs titled like
    your roles are kept; those on a site that allows reading and carries schema.org job data (The Hub) get their
    description now, the rest arrive unscored until you open them (the posting is read then)."""
    relevant = strategy.relevant_pattern()
    jobs = []
    for j in _json(WIF_API, timeout=40).get("jobs") or []:
        title, url = (j.get("title") or "").strip(), j.get("externalUrl") or ""
        if not url.startswith("http") or (relevant and not relevant.search(title)):
            continue
        emp = j.get("employer") or {}
        city = (emp.get("city") or "").strip()
        jobs.append(_job(title, emp.get("name"), f"{city}, Finland" if city else "Finland", url, method="workinfinland"))

    def describe(job):
        if _host(job["url"]).removeprefix("www.") not in DESCRIBED_HOSTS or not robots_allows(job["url"]):
            return
        try:
            page = _get(job["url"], timeout=15, accept="text/html").decode("utf-8", "replace")
        except Exception:   # one posting that won't load leaves that job unscored, nothing more
            return
        ld = _jsonld_jobs(re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S), job["url"])
        if ld and ld[0]["description"]:
            job["description"], job["posted"] = ld[0]["description"], job["posted"] or ld[0]["posted"]

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(describe, [j for j in jobs if _host(j["url"]).removeprefix("www.") in DESCRIBED_HOSTS][:MAX_DESCRIBED]))
    return jobs


LEVEL_WORDS = {"lead", "senior", "sr", "staff", "principal", "junior", "jr", "associate", "head"}


def board_terms(prefs: dict) -> list[str]:
    """Search terms from your career tracks without the level word: boards match "Software Engineer" better than
    "Lead Software Engineer", and "Forward Deployed Engineer" stays whole."""
    out = []
    for q in jobspy_queries(prefs):
        words = re.sub(r"[^\w\s+#-]", " ", q).split()
        while len(words) > 2 and words[0].lower() in LEVEL_WORDS:
            words = words[1:]
        out.append(" ".join(words))
    return list(dict.fromkeys(t for t in out if t))[:8]


def _epoch(v) -> str:
    """Boards send epoch seconds as text ("1793110671") as often as numbers."""
    return _iso(int(v)) if str(v or "").isdigit() else _iso(v)


def from_board(board: str, prefs: dict) -> tuple[str, list[dict]]:
    terms = board_terms(prefs) or ["engineer"]
    out = []
    if board == "remotive":
        for t in terms:
            data = _json(f"https://remotive.com/api/remote-jobs?search={quote(t)}&limit=100", timeout=25)
            out += [_job(j.get("title"), j.get("company_name"), j.get("candidate_required_location") or "Worldwide", j.get("url"),
                         _iso(j.get("publication_date")), description=j.get("description", ""), remote=True, method="remotive")
                    for j in data.get("jobs", [])]
        return "Remotive API", out
    if board == "jobicy":
        geos = list(dict.fromkeys(["europe"] + [p.split(",")[-1].strip().lower() for p in prefs.get("places") or []]))[:2]
        for geo in geos:
            for t in terms[:4]:
                try:
                    data = _json(f"https://jobicy.com/api/v2/remote-jobs?count=50&geo={quote(geo)}&tag={quote(t)}", timeout=25)
                except urllib.error.HTTPError:   # a country Jobicy doesn't list (Luxembourg): skip it, keep the rest
                    break
                out += [_job(j.get("jobTitle"), j.get("companyName"), j.get("jobGeo") or "Europe", j.get("url"),
                             _iso(j.get("pubDate")), description=j.get("jobDescription", ""), remote=True, method="jobicy")
                        for j in data.get("jobs", []) or []]
        return "Jobicy API", out
    if board == "himalayas":
        for t in terms[:6]:
            for page in (1,):
                data = _json(f"https://himalayas.app/jobs/api/search?q={quote(t)}&page={page}{_himalayas_country()}",
                             timeout=25)
                for j in data.get("jobs", []):
                    where = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
                    out.append(_job(j.get("title"), j.get("companyName"), where, j.get("applicationLink") or j.get("guid"),
                                    _epoch(j.get("pubDate")), description=j.get("description", ""), remote=True, method="himalayas"))
        return "Himalayas API", out
    if board == "workinfinland":
        return "Work in Finland feed", from_work_in_finland()
    if board == "arbeitnow":
        for page in (1, 2):
            data = _json(f"https://www.arbeitnow.com/api/job-board-api?page={page}", timeout=30)
            for j in data.get("data", []):
                remote = bool(j.get("remote"))
                where = j.get("location") or ""
                out.append(_job(j.get("title"), j.get("company_name"), f"Remote, {where}" if remote and where else where,
                                j.get("url"), _epoch(j.get("created_at")), description=j.get("description", ""),
                                remote=remote, method="arbeitnow"))
        return "Arbeitnow API", out
    raise ValueError(f"Unknown job board {board!r}")


def _himalayas_country() -> str:
    """Himalayas' country filter (ISO code) for where you live: it keeps jobs open worldwide too."""
    code = strategy.COUNTRY_CODES.get((strategy.home()["country"] or "india").lower())
    return f"&country={code}" if code else ""


def set_boards(on: list[str]) -> list[dict]:
    """Switch the public job boards on or off: each one on is a source in every scan."""
    every = load_sources()
    keep = {r["value"]: r for r in every if r["kind"] == "board"}
    rows = [r for r in every if r["kind"] != "board"]
    for b in on:
        if b in BOARDS:
            rows.append(keep.get(b) or {"id": hashlib.sha1(f"board:{b}".encode()).hexdigest()[:10], "kind": "board", "value": b,
                                        "label": BOARDS[b], "last_scan": "", "last_count": 0})
    save_sources(rows)
    return rows


def load_sources() -> list[dict]:
    f = _sources_file()
    return json.loads(f.read_text("utf-8")) if f.exists() else []


def save_sources(rows: list[dict]) -> None:
    _sources_file().write_text(json.dumps(rows, indent=2, ensure_ascii=False), "utf-8")


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
    default_label = {"url": _host(value) if kind == "url" else "", "websearch": value[:60], "indeed": f"Indeed: {value[:50]}",
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
    learned = taste.model(state)
    out, changed = [], False
    for jid, j in state["jobs"].items():
        if jid in dismissed or (j["posted"] and j["posted"] < cutoff):
            continue
        r = rank(j, evidence, prefs, sig)
        if r.get("_r") is not j.get("_r"):
            state["jobs"][jid]["_r"], changed = r["_r"], True
        if not r["excluded"] and (r := taste.apply(r, learned)):
            out.append({k: v for k, v in r.items() if k not in ("description", "_r")})
    if changed:
        with _lock:
            _save_jobs(state)
    return _dedupe_sort(out)


def scan_one(src: dict, days: int, prefs: dict | None = None) -> dict:
    t = time.time()
    try:
        if src["kind"] == "board":
            method, jobs = from_board(src["value"], prefs or {})
        elif src["kind"] == "websearch":
            method, jobs = from_websearch(src["value"], days)
        elif src["kind"] == "indeed":
            method, jobs = from_indeed(src["value"], days)
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
    f = _jobs_file()
    return json.loads(f.read_text("utf-8")) if f.exists() else {"jobs": {}, "dismissed": []}


def _save_jobs(state: dict) -> None:
    f = _jobs_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=1, ensure_ascii=False), "utf-8")


def scan(source_ids: list[str] | None, days: int, prefs: dict, budget_s: int = 60, progress=lambda m: None) -> dict:
    from .sponsors import refresh_soon
    refresh_soon()   # the public sponsor registers, at most monthly, in the background
    # Indeed searches run only when asked for by id (Discover's Scan Indeed button), not on every scan.
    every = load_sources()
    sources = [s for s in every if s["id"] in source_ids] if source_ids else [s for s in every if s["kind"] != "indeed"]
    url_sources = [s for s in sources if s["kind"] in ("url", "board")]
    web_sources = [s for s in sources if s["kind"] in ("websearch", "people", "indeed")]
    n_indeed = sum(1 for x in sources if x["kind"] == "indeed")
    if n_indeed == len(sources):
        progress(f"Searching Indeed for {n_indeed} career track{'s' if n_indeed != 1 else ''}, one after another")
    else:
        progress(f"Scanning {len(sources)} source{'s' if len(sources) != 1 else ''} in parallel")
    profile = load_profile()
    evidence = strong_skills(profile)
    results = []
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        # Favourite companies go to the front of the queue. Indeed searches share one worker and run in turn:
        # in parallel they trip Indeed's per-account rate limit.
        ordered = sorted((x for x in url_sources + web_sources if x["kind"] != "indeed"), key=lambda s: not s.get("favourite"))
        indeed = [x for x in web_sources if x["kind"] == "indeed"]

        def indeed_in_turn():
            out = []
            for k, src in enumerate(indeed):
                progress(f"Indeed {k + 1} of {len(indeed)}: {src['label'].removeprefix('Indeed: ')}")
                res = scan_one(src, days)
                progress(f"{src['label']}: {len(res['jobs'])} jobs" + (f" ({res['error'][:120]})" if res.get("error") else ""))
                out.append(res)
            return out

        in_profile = lambda fn, *a: pool.submit(contextvars.copy_context().run, fn, *a)
        futs = {in_profile(scan_one, s, days, prefs): s for s in ordered}
        if indeed:
            futs[in_profile(indeed_in_turn)] = None
        deadline = time.time() + budget_s + (120 if web_sources else 0) + 150 * len(indeed)
        for fut in cf.as_completed(futs, timeout=None):
            s = futs[fut]
            res = fut.result()
            if s is None:                      # the Indeed batch: its own progress lines are already out
                results.extend(res)
            else:
                results.append(res)
                progress(f"{s['label']}: {len(res['jobs'])} jobs" + (f" ({res['error'][:80]})" if res.get("error") else ""))
            if time.time() > deadline:
                break
    return _store(results, days, prefs, evidence, progress)


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


def dismiss(job_id: str, reason: str = "") -> None:
    """'Not for me': hidden now, and remembered so Discover learns what to push down (see taste.py).
    Called again with a reason, it adds the reason to the same verdict."""
    with _lock:
        state = _load_jobs()
        if job_id not in state.setdefault("dismissed", []):
            state["dismissed"].append(job_id)
        if job_id in state["jobs"]:
            taste.record(state, state["jobs"][job_id], "no", reason)
        _save_jobs(state)


def restore(job_id: str) -> None:
    """Undo a 'Not for me': the job shows again and stops teaching anything."""
    with _lock:
        state = _load_jobs()
        state["dismissed"] = [i for i in state.get("dismissed", []) if i != job_id]
        (state.get("feedback") or {}).pop(job_id, None)
        _save_jobs(state)


def keep(job_id: str) -> None:
    """A job sent to tailoring: the opposite signal, so a rule never buries the kind of job you act on."""
    with _lock:
        state = _load_jobs()
        if job_id in state["jobs"]:
            taste.record(state, state["jobs"][job_id], "yes")
            _save_jobs(state)


def taste_summary() -> dict:
    return taste.summary(_load_jobs())


def taste_rule(key: str, on: bool) -> None:
    """Take back a learned rule (on=False), or bring it back."""
    with _lock:
        state = _load_jobs()
        (taste.unforget if on else taste.forget)(state, key)
        _save_jobs(state)


SNIPPET = 1200   # characters: a description shorter than this is scored as an estimate until the full page is read


def job_detail(job_id: str, prefs: dict | None = None) -> dict:
    """Full JD for the tailoring form. Most ATS APIs already returned it; otherwise read the page.
    prefs: the profile's Discover settings, to rank the job again once its full text is in."""
    state = _load_jobs()
    j = state["jobs"].get(job_id)
    if not j:
        raise KeyError(job_id)
    if j.get("_trimmed") or len(j.get("description", "")) < SNIPPET:
        short = len(j.get("description", ""))
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
        if len(j.get("description", "")) > short:
            # Scored on a snippet: rank it again on the full posting, so the card shows what Tailor will.
            # Without the settings, dropping the stored rank makes the next Discover load rank it afresh.
            j = {k: v for k, v in j.items() if k not in ("_r", "_trimmed")}
            if prefs is not None:
                prefs, evidence = with_profile(prefs), strong_skills(load_profile())
                ranked = rank(j, evidence, prefs, rank_signature(prefs, evidence))
                j = j | {k: ranked[k] for k in ("match", "estimate", "unscored", "reasons", "order", "excluded", "areas", "skills", "_r")
                         if k in ranked}
        with _lock:
            state["jobs"][job_id] = j
            _save_jobs(state)
    return j


def _store(results: list[dict], days: int, prefs: dict, evidence: set[str], progress=lambda m: None) -> dict:
    """Rank what a scan (or a paste) found, keep it, and return the fresh, non-excluded jobs."""
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
    learned = taste.model(state)
    fresh = [taste.apply(j, learned) for j in fresh if j["id"] not in dismissed and not j.get("excluded")]
    fresh = _dedupe_sort([{k: v for k, v in j.items() if k not in ("description", "_r")} for j in fresh if j])
    from . import db
    db.sync_soon()   # the local database gets this scan's jobs too
    return {"jobs": fresh, "results": [{k: v for k, v in r.items() if k != "jobs"} | {"count": len(r["jobs"])} for r in results],
            "cutoff": cutoff}


# ---------- JobSpy: LinkedIn, Indeed, Google Jobs and Naukri through the python-jobspy library ----------
# The Discover "Scan with JobSpy" button. It scrapes those boards, which their terms of service don't allow
# and which they block now and then, so it is a separate button that is never part of a normal scan. What it
# searches for comes from the profile: the title queries of your career tracks, in your city.

JOBSPY_SITES = ["linkedin", "indeed", "google", "naukri"]
JOBSPY_MAX_QUERIES = 10


def jobspy_queries(prefs: dict) -> list[str]:
    """Title queries for the career tracks you rank for (your profile's strategy), else your headline. Tracks take
    turns: every track's first title, then every track's second, so the cap never leaves a track unsearched."""
    titles = strategy.search_titles()
    picked = [t for t in (prefs.get("tracks") or list(titles)) if t in titles] or list(titles)
    # The track at your own level goes first in each turn.
    picked.sort(key=lambda t: t != strategy.default_track())
    queries = [titles[t][i] for i in range(2) for t in picked if len(titles[t]) > i]
    if not queries:
        try:
            queries = [load_profile()["basics"].get("headline") or ""]
        except FileNotFoundError:
            queries = []
    return list(dict.fromkeys(q for q in queries if q))[:JOBSPY_MAX_QUERIES]


def _txt(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def from_jobspy(query: str, location: str, country: str, days: int) -> list[dict]:
    from jobspy import scrape_jobs
    # Naukri is India only; Indeed needs a country it has a site for (without one it's left out).
    sites = [s for s in JOBSPY_SITES if (s != "naukri" or country.lower() == "india") and (s != "indeed" or country)]
    df = scrape_jobs(site_name=sites, search_term=query, google_search_term=f"{query} jobs near {location}",
                     location=location, results_wanted=20, hours_old=max(24, days * 24),
                     country_indeed=country.lower() or "india", linkedin_fetch_description=True,
                     description_format="markdown", verbose=0)
    out = []
    for r in df.to_dict("records"):
        title, url = _txt(r.get("title")), _txt(r.get("job_url"))
        if not (title and url.startswith("http")):
            continue
        posted = _txt(str(r.get("date_posted") or "")[:10])
        job = _job(title, _txt(r.get("company")), _txt(r.get("location")), url,
                   posted if re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted) else "", "",
                   description=_txt(r.get("description")), remote=True if r.get("is_remote") is True else None,
                   method="jobspy")
        # The same posting turns up on several boards and under several links: key it on what it is
        job["id"] = hashlib.sha1(f"{title}|{job['company']}|{job['location']}".lower().encode()).hexdigest()[:12]
        job["source"] = f"JobSpy: {_txt(r.get('site')) or 'board'}"
        lo, hi = r.get("min_amount"), r.get("max_amount")
        if isinstance(lo, (int, float)) and lo == lo:
            cur = _txt(r.get("currency"))
            job["compensation"] = f"{cur} {int(lo):,}" + (f" to {int(hi):,}" if isinstance(hi, (int, float)) and hi == hi else "") \
                + (f" per {_txt(r.get('interval'))}" if _txt(r.get("interval")) else "")
        out.append(job)
    return out


def indeed_country(place: str) -> str:
    """The Indeed site JobSpy can search for a place ("Amsterdam, Netherlands" -> netherlands), or "" if none."""
    from jobspy.model import Country
    name = place.split(",")[-1].strip().lower()
    try:
        return Country(name).value
    except ValueError:
        return ""


def scan_jobspy(days: int, prefs: dict, progress=lambda m: None) -> dict:
    home = strategy.home()
    queries = jobspy_queries(prefs)
    if not queries:
        raise EngineError("Your profile has no career tracks or headline to search for yet. Set them up first.")
    # Home with every query; each place you'd move to with the two queries at your own level, to keep scans short.
    searches = [(q, home["city"] or home["country"], home["country"]) for q in queries]
    for place in prefs.get("places") or []:
        searches += [(q, place.split(",")[0].strip(), indeed_country(place)) for q in queries[:2]]
    evidence = strong_skills(load_profile())
    results = []
    for k, (q, location, country) in enumerate(searches):
        progress(f"JobSpy {k + 1} of {len(searches)}: {q}, {location}")
        t = time.time()
        try:
            jobs = from_jobspy(q, location, country, days)
            res = {"source": f"jobspy:{q}|{location}", "method": "jobspy", "jobs": jobs}
        except Exception as e:  # one search failing must not sink the rest
            res = {"source": f"jobspy:{q}|{location}", "jobs": [], "error": f"{type(e).__name__}: {str(e)[:240]}"}
        res["seconds"] = round(time.time() - t, 1)
        progress(f"{q}: {len(res['jobs'])} jobs" + (f" ({res['error'][:100]})" if res.get("error") else ""))
        results.append(res)
    return _store(results, days, prefs, evidence, progress)


# ---------- jobs gathered by Claude in Chrome, pasted in ----------
# The candidate's own Claude in Chrome browses job sites in their own signed-in browser and returns this app's job
# format; pasting it here ranks it like any scan. Nothing is fetched by this app.

def _plain(words: list[str], n: int = 30) -> list[str]:
    return [w for w in words or [] if w and not w.startswith("re:")][:n]


def chrome_prompt(prefs: dict, days: int) -> str:
    """A self-contained prompt for Claude in Chrome, written from this profile's strategy."""
    from .profile import experience_years_label
    s, lv, home = strategy.load(), strategy.level(), strategy.home()
    profile = load_profile()
    tracks = [t for tid, t in strategy.tracks().items() if tid in (prefs.get("tracks") or list(strategy.tracks()))]
    titles = list(dict.fromkeys(x for t in tracks for x in (t.get("titles") or [])))[:14]
    wanted = regions(prefs)
    where = []
    if "city" in wanted and home["city"]:
        where.append(f"{home['city']} (on-site or hybrid)")
    if "country" in wanted and home["country"]:
        where.append(f"elsewhere in {home['country']}")
    if "remote" in wanted:
        where.append(f"fully remote roles open to people in {home['country'] or 'my country'}")
    if "world" in wanted:
        where.append("abroad (UK, EU, Gulf, Singapore) where the employer sponsors visas")
    favs = [r.get("company") or r["label"] for r in load_sources() if r.get("favourite")][:25]
    skip = _plain((s.get("discover") or {}).get("exclude_title_words") or [], 30) + (prefs.get("exclude_skills") or [])
    return f"""You are helping me find jobs. Use my browser: I am already signed in to the sites below. Collect job postings that fit me and give them back as one JSON object that my job-tracking app can read. Do not apply to anything, do not message anyone, do not save, follow or change anything on any site, and do not fill in any form. Only read.

WHO I AM
{s.get('field') or 'a professional'}, {experience_years_label(profile)} years of experience, level: {lv['label']} (current title: {lv.get('current_title') or profile['basics'].get('headline', '')}).
What I want next (notes about me): {s.get('career_wants') or 'roles at my level or one step up in my field.'}

WHAT TO SEARCH FOR
Job titles (search each one, and close variants): {'; '.join(titles)}.
Places: {'; '.join(where) or 'anywhere that fits'}.
Posted within the last {days} days only. Skip anything older, and skip expired or closed postings.
Skip titles or roles that are clearly something else, such as: {', '.join(skip) or 'anything outside my field'}.
Skip roles that are well above my level (director, head, VP) or clearly below it (internships, trainees).

WHERE TO LOOK (my own signed-in sessions)
LinkedIn Jobs (use the "Past week" or "Past month" filter that fits {days} days, and sort by most recent), Naukri, Indeed, Instahyre, and the careers pages of these employers: {', '.join(favs) or 'the main employers in my field'}. Work through the titles on each site. Aim for 40 to 60 distinct postings in total; stop earlier if the searches run dry. If a site asks you to log in, solve a captcha or says you are being rate limited, skip that site and say so in "notes".

FOR EACH POSTING
Open it and read the whole description. Copy the details exactly as the page shows them. Never invent or guess a value; use "" when the page does not say.
- "title": the job title as posted.
- "company": the employer's name.
- "location": the location line as posted (for example "Bengaluru, Karnataka, India (Hybrid)" or "Remote, India").
- "url": the address of that one posting, from the browser's address bar. Remove tracking parameters after "?" when the posting still opens without them.
- "posted": the posting date or the page's own wording ("2 days ago", "Oct 3, 2026").
- "compensation": the pay if the posting states it, else "".
- "description": the full description, word for word, up to 4000 characters. Do not summarise it. Include the requirements and responsibilities sections.
If the same job appears on two sites, list it once, using the employer's own careers-page link when you can find it.

HOW TO ANSWER
Reply with one JSON object and nothing else, no commentary and no code fences, in exactly this shape:
{{"jobs": [{{"title": "", "company": "", "location": "", "url": "", "posted": "", "compensation": "", "description": ""}}], "notes": "sites you could not read, and anything I should know"}}"""


def chrome_start_url(prefs: dict, days: int) -> str:
    """Where the new tab opens: a LinkedIn Jobs search for the first target title, in the candidate's city, in the window."""
    from urllib.parse import quote_plus
    titles = [x for t in strategy.search_titles().values() for x in t]
    window = "r86400" if days <= 1 else "r604800" if days <= 7 else "r2592000"
    return (f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(titles[0] if titles else '')}"
            f"&location={quote_plus(strategy.home()['city'] or strategy.home()['country'])}&f_TPR={window}&sortBy=DD")


def _json_candidates(t: str):
    """The same text, repaired step by step. A reply copied out of a formatted chat often arrives with curly quotes in
    place of the JSON's straight ones; only the quotes that sit where JSON puts its own are converted, so a quoted
    phrase inside a job description (see “Who This Role Is Open To” below) is left as it is."""
    yield t
    structural = re.sub(r"(?<=[\{\[:,])(\s*)[\u201c\u201d\u201e]", lambda m: m.group(1) + '"', t)
    structural = re.sub(r"[\u201c\u201d\u201e](?=\s*[:,\}\]])", '"', structural)
    yield structural
    yield re.sub(r"[\u201c\u201d\u201e]", '"', t)


def _parse_pasted(text: str) -> tuple[list[dict], str]:
    """The JSON object (or bare list) the browser assistant returned, tolerating code fences, a stray sentence around it,
    curly quotes, and raw line breaks inside strings."""
    t = re.sub(r"^```[a-zA-Z]*\s*|```\s*$", "", (text or "").strip(), flags=re.M).strip()
    t = t.replace("\u00a0", " ")
    problem = ""
    for candidate in _json_candidates(t):
        for opener, closer in (("{", "}"), ("[", "]")):
            a, b = candidate.find(opener), candidate.rfind(closer)
            if a < 0 or b <= a:
                continue
            try:
                data = json.loads(candidate[a:b + 1], strict=False)
            except json.JSONDecodeError as e:
                if opener == "{" and not problem:   # where the first attempt broke, for the message
                    chunk = candidate[a:b + 1]
                    problem = f" It breaks at character {e.pos:,} of {len(chunk):,} ({e.msg}), near: “{chunk[max(0, e.pos - 40):e.pos + 40]!s}”."
                continue
            jobs = data.get("jobs") if isinstance(data, dict) else data
            if isinstance(jobs, list):
                return [j for j in jobs if isinstance(j, dict)], str(data.get("notes") or "") if isinstance(data, dict) else ""
    raise ValueError("That isn't the JSON the prompt asks for." + (problem or " Paste the whole reply, starting at { and ending at }.")
                     + " If it was copied from a formatted chat, ask Claude to \"reply again as a code block\" and copy from the block.")


def import_jobs(text: str, days: int, prefs: dict) -> dict:
    rows, notes = _parse_pasted(text)
    jobs, skipped, seen = [], 0, set()
    for r in rows:
        title, url = str(r.get("title") or "").strip(), str(r.get("url") or "").strip()
        if not title or not url.startswith("http"):
            skipped += 1
            continue
        raw_date = str(r.get("posted") or "")
        job = _job(title, r.get("company"), r.get("location"), url, _indeed_date(raw_date), raw_date,
                   description=str(r.get("description") or ""), method="browser (Claude in Chrome)", source="Claude in Chrome")
        # Keyed on what the posting is, not on its link: the same job pasted again isn't "new" again.
        job["id"] = hashlib.sha1(f"chrome|{job['title']}|{job['company']}|{job['location']}".lower().encode()).hexdigest()[:12]
        comp = str(r.get("compensation") or "").strip()
        if comp and comp.upper() != "N/A":
            job["compensation"] = comp
        if job["id"] in seen:
            continue
        seen.add(job["id"])
        jobs.append(job)
    if not jobs:
        raise ValueError("No postings with a title and a link were found in that paste." + (f" Notes from the browser: {notes[:200]}" if notes else ""))
    evidence = strong_skills(load_profile())
    shown = _store([{"source": "chrome", "method": "browser (Claude in Chrome)", "jobs": jobs}], days, prefs, evidence)["jobs"]
    state = _load_jobs()
    ids, shown_ids = {j["id"] for j in jobs}, {j["id"] for j in shown}
    hidden = [state["jobs"][i] for i in ids if i in state["jobs"] and i not in shown_ids]
    why: dict[str, int] = {}
    for j in hidden:
        reason = (j.get("reasons") or ["filtered out"])[0].split("(")[0].strip()
        why[reason] = why.get(reason, 0) + 1
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    older = sum(1 for j in jobs if j["posted"] and j["posted"] < cutoff)
    return {"read": len(jobs), "skipped": skipped, "older": older, "shown": len(shown_ids & ids), "hidden": len(hidden), "why": why,
            "no_description": sum(1 for j in jobs if len(j["description"]) < 300), "notes": notes[:400]}
