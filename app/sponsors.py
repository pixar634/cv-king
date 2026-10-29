"""Employers on the public visa sponsor registers: the UK's "Register of licensed sponsors: workers" (gov.uk, Open
Government Licence) and the Netherlands' IND register of recognised sponsors. A UK Skilled Worker visa needs a
licensed sponsor; the Dutch highly skilled migrant route needs a recognised one (the EU Blue Card doesn't).

Both lists are public and shared by every profile, so they live in `.cache/` at the repo root and are refreshed
monthly in the background. Names are matched without their legal suffixes ("Monzo Bank Ltd" is "monzo bank").
"""
import bisect
import csv
import datetime as dt
import io
import json
import re
import threading
import urllib.request

from .config import ROOT

CACHE = ROOT / ".cache" / "sponsors.json"
UK_API = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
IND_PAGE = "https://ind.nl/en/public-register-recognised-sponsors/public-register-regular-labour-and-highly-skilled-migrants"
MAX_AGE_DAYS = 30
SUFFIX = re.compile(r"\b(ltd|limited|llp|plc|inc|incorporated|llc|corp|corporation|co|company|gmbh|ag|bv|b v|nv|n v|"
                    r"holding|holdings|group|uk|u k|nederland|netherlands|europe|emea|international|the)\b")
UK_PLACES = re.compile(r"united kingdom|\buk\b|\bu\.k\.|great britain|england|scotland|wales|northern ireland|london|"
                       r"manchester|edinburgh|cambridge|oxford|bristol|glasgow|belfast|leeds|birmingham|reading", re.I)
NL_PLACES = re.compile(r"netherlands|holland|amsterdam|rotterdam|utrecht|eindhoven|the hague|den haag|delft|leiden", re.I)
TRADING_AS = re.compile(r" (?:t a|ta|trading as) ")   # in a normalised name: "t/a" has become "t a"
_lock = threading.Lock()
_loaded: dict = {}


def norm(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower().replace("&", " and "))
    return " ".join(SUFFIX.sub(" ", s).split())


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "JobMagnet (job search; public sponsor register)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_uk() -> list[str]:
    """Skilled Worker sponsors from the latest CSV gov.uk lists for the register."""
    meta = json.loads(_get(UK_API))
    url = next(a["url"] for a in meta["details"]["attachments"] if a.get("url", "").endswith(".csv"))
    rows = csv.DictReader(io.StringIO(_get(url, 120).decode("utf-8-sig", "replace")))
    return sorted({r["Organisation Name"].strip() for r in rows if "skilled worker" in (r.get("Route") or "").lower()})


def fetch_nl() -> list[str]:
    """Recognised sponsors for regular labour and highly skilled migrants, from the IND's public register page."""
    import html
    page = _get(IND_PAGE, 120).decode("utf-8", "replace")
    return sorted({html.unescape(m).strip().strip('"') for m in re.findall(r'<th scope="row">(.*?)</th>', page)})


def refresh() -> dict:
    data = {"fetched": dt.date.today().isoformat(), "uk": [], "nl": []}
    for key, fetch in (("uk", fetch_uk), ("nl", fetch_nl)):
        try:
            data[key] = sorted({norm(n) for n in fetch() if norm(n)})
        except Exception as e:  # one register being down must not lose the other
            data[f"{key}_error"] = str(e)[:200]
            data[key] = _read().get(key, [])
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(data), "utf-8")
    _loaded.clear()
    return data


def _read() -> dict:
    try:
        return json.loads(CACHE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def refresh_soon() -> None:
    """Refresh in the background when the lists are missing or a month old; scans call this, never wait on it."""
    fetched = _read().get("fetched") or "2000-01-01"
    if (dt.date.today() - dt.date.fromisoformat(fetched)).days < MAX_AGE_DAYS or not _lock.acquire(blocking=False):
        return

    def run():
        try:
            refresh()
        finally:
            _lock.release()
    threading.Thread(target=run, daemon=True).start()


def _lists() -> dict:
    if not _loaded:
        data = _read()
        _loaded.update({k: v for k, v in data.items() if k == "fetched"})
        for key in ("uk", "nl"):
            if key in data:
                # "Roofoods t/a Deliveroo": postings use the trading name, so both names count.
                _loaded[key] = sorted({n for name in data[key] for n in TRADING_AS.split(name) if n})
                _loaded[f"{key}_joined"] = {n.replace(" ", "") for n in _loaded[key]}
    return _loaded


def on_register(company: str, register: str) -> bool | None:
    """True when the employer is on the register ("uk" or "nl"), False when it isn't, None when the list isn't here.
    A posting's short name matches a longer registered one ("Monzo" is "Monzo Bank Ltd") when it has 4+ letters."""
    names = _lists().get(register)
    if not names:
        return None
    c = norm(company)
    if not c:
        return False
    i = bisect.bisect_left(names, c)
    if i < len(names) and names[i] == c:
        return True
    if len(c) >= 4 and i < len(names) and names[i].startswith(c + " "):
        return True
    # A job board's slug runs words together and adds to them ("Sohohouseco" is "Soho House"): spaces aside,
    # a posting name that starts with a registered name of 6+ letters counts.
    joined, flat = _lists().get(f"{register}_joined") or set(), c.replace(" ", "")
    return any(flat[:k] in joined for k in range(6, len(flat) + 1))


def check(company: str, location: str) -> dict | None:
    """{"register": "uk" | "nl", "listed": bool} for a job in the UK or the Netherlands, else None."""
    for register, places in (("uk", UK_PLACES), ("nl", NL_PLACES)):
        if places.search(location or ""):
            listed = on_register(company, register)
            return None if listed is None else {"register": register, "listed": listed}
    return None
