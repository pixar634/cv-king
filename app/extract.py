"""A pasted job link -> company, role, location, job ID, posted date and the full JD.

Order: the ATS's own public API for that one posting (Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, Recruitee, Personio) -> the page's schema.org JobPosting -> the model reading the rendered
page for the facts plus where the JD starts and ends, with code cutting the text (the model never
retypes the JD, so nothing in it can drift).
"""
import html as htmllib
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from .discover import _get, _iso, _json, _jsonld_jobs, parse_posted, refused_reason, render_page, robots_allows
from .engines import ENGINES, parse_json_reply
from .jd import strip_html

MARKETS = [
    (r"india|bengaluru|bangalore|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|kolkata|ahmedabad", "india"),
    (r"united kingdom|\buk\b|london|manchester|edinburgh|england|scotland|cambridge|bristol", "uk"),
    (r"dubai|abu dhabi|\buae\b|united arab emirates|sharjah", "uae"),
    (r"singapore", "singapore"),
    (r"australia|sydney|melbourne|brisbane|new zealand|auckland|wellington", "anz"),
    (r"united states|\busa?\b|canada|toronto|vancouver|new york|san francisco|seattle|austin|boston|chicago", "northamerica"),
    (r"netherlands|amsterdam|rotterdam|germany|berlin|munich|hamburg|france|paris|spain|madrid|barcelona|ireland|dublin|"
     r"poland|warsaw|krakow|sweden|stockholm|denmark|copenhagen|finland|helsinki|portugal|lisbon|belgium|brussels|"
     r"austria|vienna|switzerland|zurich|italy|milan|czech|prague|estonia|tallinn|europe|\beu\b", "eu"),
    (r"remote|anywhere|work from home", "remote"),
]


def market_for(location: str, remote=None) -> str:
    text = f"{location or ''} {'remote' if remote else ''}"
    for pat, m in MARKETS:
        if re.search(pat, text, re.I):
            return m
    return "india"


def _job_id_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    for k in ("gh_jid", "jobId", "jobid", "job_id", "currentJobId", "jid", "id", "req", "reqId", "requisitionId"):
        if q.get(k):
            return q[k][0]
    path = urlparse(url).path
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", path, re.I) or \
        re.search(r"(?<![A-Za-z0-9])((?:[A-Z]{1,4}-?)?\d{4,})(?![A-Za-z0-9])", path)
    return m.group(1) if m and m.lastindex else (m.group(0) if m else "")


def _result(title, company, location, description, posted="", job_id="", url="", method="", remote=None):
    description = strip_html(htmllib.unescape(description or ""))
    return {"role": (title or "").strip(), "company": (company or "").strip(), "location": (location or "").strip(),
            "job_id": str(job_id or "").strip(), "posted": posted or "", "url": url, "description": description,
            "method": method, "market": market_for(location, remote), "remote": bool(remote)}


def from_ats_job(url: str) -> dict | None:
    p = urlparse(url)
    h, parts = (p.hostname or "").lower(), [x for x in p.path.split("/") if x]
    if "greenhouse.io" in h and len(parts) >= 3 and parts[1] == "jobs":
        slug, jid = parts[0], parts[2]
        j = _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}")
        name = j.get("company_name") or _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}").get("name", slug)
        return _result(j.get("title"), name, (j.get("location") or {}).get("name", ""), j.get("content", ""),
                       _iso(j.get("first_published") or j.get("updated_at")), j.get("requisition_id") or jid, url, "Greenhouse API")
    if h == "jobs.lever.co" and len(parts) >= 2:
        j = _json(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}")
        lists = "\n".join(f"{l.get('text', '')}:\n{strip_html(l.get('content', ''))}" for l in j.get("lists", []))
        cat = j.get("categories") or {}
        return _result(j.get("text"), parts[0].replace("-", " ").title(), cat.get("location", ""),
                       f"{j.get('descriptionPlain', '')}\n{lists}\n{j.get('additionalPlain', '')}", _iso(j.get("createdAt")),
                       parts[1], url, "Lever API", j.get("workplaceType") == "remote")
    if h == "jobs.ashbyhq.com" and len(parts) >= 2:
        data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}")
        j = next((x for x in data.get("jobs", []) if x.get("id") == parts[1] or parts[1] in (x.get("jobUrl") or "")), None)
        if j:
            return _result(j.get("title"), parts[0].replace("-", " ").title(), j.get("location", ""),
                           j.get("descriptionPlain") or j.get("descriptionHtml", ""), _iso(j.get("publishedAt")),
                           parts[1], url, "Ashby API", j.get("isRemote"))
    if h in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and len(parts) >= 2:
        jid = parts[1].split("-")[0]
        j = _json(f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{jid}")
        secs = (j.get("jobAd") or {}).get("sections") or {}
        text = "\n\n".join(f"{v.get('title', '')}\n{v.get('text', '')}" for v in secs.values() if isinstance(v, dict))
        loc = j.get("location") or {}
        return _result(j.get("name"), (j.get("company") or {}).get("name", parts[0]),
                       ", ".join(x for x in (loc.get("city"), loc.get("country")) if x), text, _iso(j.get("releasedDate")),
                       j.get("refNumber") or jid, url, "SmartRecruiters API", loc.get("remote"))
    if h == "apply.workable.com" and len(parts) >= 3 and parts[1] == "j":
        data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{parts[0]}?details=true")
        j = next((x for x in data.get("jobs", []) if x.get("shortcode") == parts[2]), None)
        if j:
            return _result(j.get("title"), data.get("name", parts[0]), ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                           j.get("description", ""), _iso(j.get("published_on") or j.get("created_at")), parts[2], url,
                           "Workable API", j.get("telecommuting"))
    if h.endswith(".recruitee.com") and len(parts) >= 2 and parts[0] == "o":
        slug = h.split(".")[0]
        data = _json(f"https://{slug}.recruitee.com/api/offers/")
        j = next((x for x in data.get("offers", []) if x.get("slug") == parts[1]), None)
        if j:
            return _result(j.get("title"), j.get("company_name") or slug.title(), j.get("location", ""),
                           f"{j.get('description', '')}\n{j.get('requirements', '')}", _iso(j.get("published_at")),
                           j.get("id"), url, "Recruitee API", j.get("remote"))
    if ".jobs.personio." in h and len(parts) >= 2 and parts[0] == "job":
        slug, tld = h.split(".")[0], h.rsplit(".", 1)[-1]
        root = ET.fromstring(_get(f"https://{slug}.jobs.personio.{tld}/xml?language=en", 25, "application/xml"))
        for pos in root.findall("position"):
            if pos.findtext("id") == parts[1]:
                desc = "\n".join(f"{d.findtext('name', '')}\n{d.findtext('value', '')}" for d in pos.findall("jobDescriptions/jobDescription"))
                return _result(pos.findtext("name"), slug.title(), pos.findtext("office", ""), desc,
                               _iso(pos.findtext("createdAt")), parts[1], url, "Personio feed")
    return None


READ_BRIEF = """This is the text of one job posting page. Return JSON only:
{"company": "", "role": "", "location": "", "job_id": "", "posted": "", "remote": false,
 "jd_start": "", "jd_end": ""}
- company, role, location: as the page states them; "" if it doesn't.
- job_id: the posting's own ID or requisition number if the page shows one (e.g. "Req ID: R-12345"); else "".
- posted: the page's own wording for when it was posted ("3 days ago", "Oct 2, 2026"); "" if absent. Never guess.
- jd_start: the first 8 to 12 words of the job description itself (where the role description begins),
  copied EXACTLY from the text. jd_end: the last 8 to 12 words of the job description (before footer,
  "apply" buttons, similar-jobs lists), copied EXACTLY."""


def _slice(text: str, start: str, end: str) -> str:
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    t = norm(text)
    i = t.find(norm(start)) if start else -1
    j = t.find(norm(end), i if i >= 0 else 0) if end else -1
    if i >= 0 and j > i:
        return t[i:j + len(norm(end))]
    return t[i:] if i >= 0 else t


def ats_candidates(page: dict, url: str) -> list[str]:
    """Careers sites often embed the real posting from an ATS (Greenhouse iframes, Lever/Ashby links).
    Find it, so the posting is read from the ATS's API instead of the site's wrapper."""
    blob = "\n".join([page.get("html", ""), *page.get("frames", []), *(l["h"] for l in page.get("links", []))])
    out = []
    for m in re.finditer(r"greenhouse\.io/(?:embed/job_app\?(?:[^\"'\s]*?&)?for=)?([\w-]+)(?:/jobs/|[^\"'\s]*?token=)(\d{5,})", blob):
        out.append(f"https://boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}")
    url_id = _job_id_from_url(url)
    if url_id.isdigit():
        for slug in set(re.findall(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|v1/boards/|)([a-z0-9][\w-]{1,40})(?=[/?&\"'])", blob)):
            if slug not in ("embed", "v1", "boards", "job-boards", "js"):
                out.append(f"https://boards.greenhouse.io/{slug}/jobs/{url_id}")
    for host, pat in (("jobs.lever.co", r"jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})"),
                      ("jobs.ashbyhq.com", r"jobs\.ashbyhq\.com/([\w-]+)/([0-9a-f-]{36})")):
        for m in re.finditer(pat, blob):
            out.append(f"https://{host}/{m.group(1)}/{m.group(2)}")
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq[:6]


def from_page(url: str) -> dict:
    page = render_page(url, wait_ms=7000)
    for cand in ats_candidates(page, url):
        try:
            got = from_ats_job(cand)
        except Exception:
            continue
        if got and len(got["description"]) > 300:
            return got | {"url": url, "method": got["method"] + " (embedded)"}
    ld = _jsonld_jobs(page["jsonld"], url)
    if ld and len(ld[0]["description"]) > 300:
        j = ld[0]
        jid = ""
        for blob in page["jsonld"]:
            m = re.search(r'"identifier"\s*:\s*\{[^}]*"value"\s*:\s*"?([^",}]+)', blob)
            if m:
                jid = m.group(1)
                break
        return _result(j["title"], j["company"], j["location"], j["description"], j["posted"],
                       jid or _job_id_from_url(url), url, "schema.org JobPosting", j.get("remote"))
    text = page["text"][:30000]
    from .engines import light_engine
    raw = parse_json_reply(ENGINES[light_engine()].run(f"{READ_BRIEF}\n\nURL: {url}\nTITLE: {page['title']}\n\n<<<\n{text}\n>>>", timeout=120))
    desc = _slice(text, raw.get("jd_start", ""), raw.get("jd_end", ""))
    if not (raw.get("role") or "").strip():
        raise ValueError("Couldn't find a single job posting on that page (it may be a list or need sign-in). "
                         "Open the posting, copy its description, and paste the text here.")
    return _result(raw.get("role"), raw.get("company"), raw.get("location"), desc, parse_posted(raw.get("posted", "")),
                   raw.get("job_id") or _job_id_from_url(url), url, "page read by model", raw.get("remote"))


def extract(value: str, progress=lambda m: None) -> dict:
    value = value.strip()
    if not re.match(r"^https?://\S+$", value):
        raise ValueError("That isn't a link. Paste the job's URL, or paste the job description text itself.")
    why = refused_reason(value)
    if why:
        raise ValueError(f"{why} Open the posting, copy the description, and paste the text here instead.")
    progress("Reading the posting")
    got = from_ats_job(value)
    if got is None:
        if not robots_allows(value):
            raise ValueError("This site asks automated tools not to read that page. Paste the job description text instead.")
        progress("Opening the page")
        got = from_page(value)
    if len(got["description"]) < 200:
        raise ValueError("Couldn't find a job description on that page. Paste the description text instead.")
    return got
