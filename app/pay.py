"""Does a job pay what you're aiming for? Stated pay is read from the posting (range, currency, period) and compared
with your floor for where the job is: your city, remote from home, or abroad, where the visa's own salary threshold
is the least a sponsor can offer. Few postings state pay, so a company pay tier you fill in (from Levels.fyi or
similar) stands in when the posting doesn't say.

Currencies are compared in rupees at rough 2026 rates: good enough to sort and hide, never shown as a converted
figure.
"""
import re

TO_INR = {"INR": 1, "USD": 88, "EUR": 102, "GBP": 118, "PLN": 24, "CHF": 110, "SEK": 9, "DKK": 13.7}
# The least a sponsored work permit pays (gross, a year, 2026). Check the official sites; these change each year.
PERMIT_FLOOR = {"germany": ("EUR", 45934), "netherlands": ("EUR", 71304), "poland": ("PLN", 160260),
                "united kingdom": ("GBP", 41700), "ireland": ("EUR", 38000), "luxembourg": ("EUR", 63408),
                "finland": ("EUR", 39804)}
SYMBOL = {"₹": "INR", "rs": "INR", "inr": "INR", "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP",
          "gbp": "GBP", "pln": "PLN", "zł": "PLN", "zl": "PLN", "chf": "CHF", "sek": "SEK", "dkk": "DKK"}
CUR = r"(₹|rs\.?|inr|\$|usd|€|eur|£|gbp|pln|zł|zl|chf|sek|dkk)"
NUM = r"(\d{1,3}(?:[,.\s]\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(k|m)?"
MONEY = re.compile(CUR + r"\s?" + NUM + r"(?:\s*(?:-|–|—|to)\s*" + CUR + r"?\s?" + NUM + r")?", re.I)
MONEY_AFTER = re.compile(NUM + r"(?:\s*(?:-|–|—|to)\s*" + NUM + r")?\s*" + CUR + r"\b", re.I)
LPA = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:(?:-|–|to)\s*(\d{1,3}(?:\.\d+)?)\s*)?"
                 r"(?:lpa|lakhs?(?:\s*per\s*annum)?|lacs?|l\.p\.a)\b", re.I)
MONTHLY = re.compile(r"^.{0,25}(per\s*month|/\s*month|/\s*mo\b|monthly|a\s*month|pcm|brutto\s*/\s*mies|mies)", re.I | re.S)
HOURLY = re.compile(r"^.{0,25}(per\s*hour|/\s*h(ou)?r\b|/\s*h\b|hourly|an\s*hour)", re.I | re.S)
TIERS = {"top": "pays at the top of the market", "good": "pays well", "low": "pays below your target"}


def _amount(num: str, mult: str | None) -> float:
    n = num.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{2})*,\d{3}", n) or re.fullmatch(r"\d{1,3}(,\d{3})+", n):
        n = n.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", n):   # 60.000 in German
        n = n.replace(".", "")
    n = n.replace(",", ".")
    try:
        v = float(n)
    except ValueError:
        return 0.0
    return v * {"k": 1e3, "m": 1e6}.get((mult or "").lower(), 1)


def parse(text: str) -> dict | None:
    """{"low", "high"} in rupees a year, plus the currency and the words as written; None when no pay is stated."""
    text = text or ""
    if m := LPA.search(text):
        low = float(m.group(1)) * 1e5
        high = float(m.group(2)) * 1e5 if m.group(2) else low
        if 1e5 <= low <= 5e7:
            return {"low": low, "high": max(low, high), "currency": "INR", "text": m.group(0).strip()}
    for m in MONEY.finditer(text):
        cur = SYMBOL[m.group(1).lower().rstrip(".")]
        low, high = _amount(m.group(2), m.group(3)), _amount(m.group(5) or m.group(2), m.group(6) or m.group(3))
        got = _annual(text[m.end():], cur, low, high, m.group(0))
        if got:
            return got
    for m in MONEY_AFTER.finditer(text):
        cur = SYMBOL[m.group(5).lower()]
        low, high = _amount(m.group(1), m.group(2)), _amount(m.group(3) or m.group(1), m.group(4) or m.group(2))
        got = _annual(text[m.end():], cur, low, high, m.group(0))
        if got:
            return got
    return None


def _annual(after: str, cur: str, low: float, high: float, said: str) -> dict | None:
    if HOURLY.search(after):
        return None
    if MONTHLY.search(after):
        low, high = low * 12, high * 12
    rate = TO_INR[cur]
    low_inr, high_inr = low * rate, max(low, high) * rate
    # A real yearly salary, not "$5 off" or "€2m funding"
    if not 2e5 <= low_inr <= 2e8:
        return None
    return {"low": low_inr, "high": high_inr, "currency": cur, "text": said.strip()}


def floor(prefs: dict, where: str, location: str = "") -> tuple[float, str]:
    """(rupees a year, how to say it) for a job at home, remote, or abroad (the permit threshold or your own ask)."""
    if where == "home":
        lpa = float(prefs.get("pay_floor_home_lpa") or 0)
        return lpa * 1e5, f"₹{lpa:g} LPA"
    if where == "remote":
        lpa = float(prefs.get("pay_floor_remote_lpa") or 0)
        return lpa * 1e5, f"₹{lpa:g} LPA"
    from .discover import COUNTRY_PLACES, strategy
    for country, (cur, amount) in PERMIT_FLOOR.items():
        pat = strategy.any_of(COUNTRY_PLACES.get(country, [country]))
        if pat and pat.search(location or ""):
            ask = float(prefs.get("pay_floor_abroad_eur") or 0) * TO_INR["EUR"]
            permit = amount * TO_INR[cur]
            return max(ask, permit), f"the {country.title()} work permit's {cur} {amount:,.0f}" if permit >= ask else \
                f"your €{prefs['pay_floor_abroad_eur']:,.0f} ask"
    ask = float(prefs.get("pay_floor_abroad_eur") or 0)
    return ask * TO_INR["EUR"], f"your €{ask:,.0f} ask"


def tier(company: str, prefs: dict) -> str:
    """Your own pay tier for a company: "top", "good", "low" or ""."""
    from .tracker import _company
    key = _company(company)
    for line in (prefs.get("pay_tiers") or "").splitlines():
        name, _, t = line.partition("|")
        if key and _company(name) == key and t.strip().lower() in TIERS:
            return t.strip().lower()
    return ""


def verdict(job: dict, where: str, prefs: dict) -> dict:
    """{"label": "meets" | "likely" | "unknown" | "below", "why": "..."} for one job."""
    need, said = floor(prefs, where, job.get("location") or "")
    stated = parse(job.get("description") or "")
    t = tier(job.get("company") or "", prefs)
    if stated and need:
        if stated["low"] >= need:
            return {"label": "meets", "why": f"pays {stated['text']}"}
        if stated["high"] >= need:
            return {"label": "likely", "why": f"pays {stated['text']}, your floor is in the range"}
        return {"label": "below", "why": f"pays {stated['text']}, below {said}"}
    if stated:
        return {"label": "unknown", "why": f"pays {stated['text']}"}
    if t:
        return {"label": "likely" if t in ("top", "good") else "unknown", "why": TIERS[t], "tier": t}
    return {"label": "unknown", "why": ""}
