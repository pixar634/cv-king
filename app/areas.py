"""Which commute zone a job sits in: from its location, its description, or the candidate's own
"Company | Area" notes. Most postings only say "Bengaluru"; those stay untagged rather than guessed."""
import re
from functools import lru_cache

import yaml

from .config import KNOWLEDGE


@lru_cache(maxsize=1)
def zones() -> list[tuple[str, str, re.Pattern]]:
    raw = yaml.safe_load((KNOWLEDGE / "areas.yaml").read_text("utf-8"))
    out = []
    for city, groups in raw.items():
        for zone, places in groups.items():
            alts = sorted({re.escape(p.strip()) for p in places if p and p.strip()}, key=len, reverse=True)
            out.append((city, zone, re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alts) + r")(?![A-Za-z0-9])", re.I)))
    return out


def detect(text: str) -> list[str]:
    text = text or ""
    return [zone for _, zone, pat in zones() if pat.search(text)]


def offices(notes: str) -> dict[str, list[str]]:
    """'Atlan | HSR Layout' per line -> {'atlan': ['Electronic City & HSR']}."""
    out = {}
    for line in (notes or "").splitlines():
        if "|" not in line:
            continue
        company, place = (x.strip() for x in line.split("|", 1))
        found = detect(place)
        if company and found:
            out[company.lower()] = found
    return out


def tag(job: dict, office_notes: str = "") -> list[str]:
    found = detect(f"{job.get('location', '')}\n{job.get('title', '')}\n{(job.get('description') or '')[:6000]}")
    company = (job.get("company") or "").lower().strip()
    for name, zs in offices(office_notes).items():
        if company and (company == name or company.startswith(name) or name.startswith(company)):
            found += [z for z in zs if z not in found]
    return found


def catalogue() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for city, zone, _ in zones():
        out.setdefault(city, []).append(zone)
    return out
