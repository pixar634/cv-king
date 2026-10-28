"""LinkedIn links for the extension's Easy Apply session. Opening a search link is ordinary navigation: nothing here
browses, clicks or applies, which LinkedIn's terms forbid and which would put your account at risk."""
import re
from urllib.parse import parse_qs, urlencode, urlparse

# LinkedIn's own search filters: workplace type (f_WT) and time posted (f_TPR, in seconds).
MODES = {"on-site": "1", "remote": "2", "hybrid": "3"}


def search_url(title: str, modes=(), place: str = "", days: int = 7) -> str:
    """A LinkedIn job search showing only Easy Apply jobs, newest first."""
    q = {"keywords": (title or "").strip(), "f_AL": "true", "sortBy": "DD"}
    if (place or "").strip():
        q["location"] = place.strip()
    wt = [code for mode, code in MODES.items() if mode in (modes or ())]
    if wt:
        q["f_WT"] = ",".join(wt)
    if days:
        q["f_TPR"] = f"r{int(days) * 86400}"
    return "https://www.linkedin.com/jobs/search/?" + urlencode(q)


def job_id(url: str) -> str:
    """The job open on a LinkedIn page: /jobs/view/<id> or a search with ?currentJobId=<id>. "" elsewhere."""
    u = urlparse(url or "")
    if not (u.hostname or "").endswith("linkedin.com"):
        return ""
    m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d{6,})", u.path)
    if m:
        return m.group(1)
    return next((v for v in parse_qs(u.query).get("currentJobId", []) if v.isdigit()), "")


def job_url(jid: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{jid}/"
