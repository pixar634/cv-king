"""Your network as a web: you in the middle, the companies your connections work at around you, and the people at
each one. A company is coloured by what it means for your search right now (a Strong or Possible job open in
Discover, an application, a favourite), and each person by the kind of help they can give (recruiter, manager or
leader, someone in your own function).

Until your LinkedIn Connections.csv is in, a sample network shows how it will look: invented people, placed at
the companies your own search already knows (Discover, favourites, applications). It is labelled as a sample and
never saved.
"""
import random
import re

from . import strategy, tracker
from .tracker import _company

RECRUITER = re.compile(r"recruit|talent|sourc|\bhr\b|people partner|people ops|hiring", re.I)
LEADER = re.compile(r"manager|director|head of|\bvp\b|vice president|chief|\bc[tpe]o\b|founder|principal|staff", re.I)
SAME = re.compile(r"engineer|developer|architect|\bsde\b|\bswe\b|programmer|tech lead|full[\s-]?stack|backend|frontend|"
                  r"forward deployed|ml|data", re.I)
MAX_COMPANIES = 60       # the most companies drawn; the rest are counted, not drawn
MAX_PEOPLE = 14          # people drawn per company; a bigger company shows "+N more"


def kind(position: str) -> str:
    """What a connection can do for you: recruiter (a referral or the right contact), leader (a hiring manager or
    above), peer (someone in your own function: a referral and the team's view), or other."""
    p = position or ""
    if RECRUITER.search(p):
        return "recruiter"
    if LEADER.search(p):
        return "leader"
    if SAME.search(p):
        return "peer"
    return "other"


def _signals(prefs: dict) -> dict[str, dict]:
    """Per company key: the best open job in Discover, whether you've applied there, whether it's a favourite."""
    from .discover import load_sources, saved
    out: dict[str, dict] = {}
    try:
        jobs = saved(14, prefs)
    except Exception:  # Discover not set up yet: the graph still draws
        jobs = []
    rank = {"Strong": 3, "Possible": 2, "Long shot": 1}
    for j in jobs:
        s = out.setdefault(_company(j.get("company")), {"name": j.get("company"), "jobs": []})
        s["jobs"].append({"id": j["id"], "title": j["title"], "fit": j.get("fit") or "", "url": j.get("url") or ""})
    for s in out.values():
        s["jobs"].sort(key=lambda j: -rank.get(j["fit"], 0))
        s["best"] = s["jobs"][0]["fit"] if s["jobs"] else ""
        s["jobs"] = s["jobs"][:5]
    for r in tracker.load():
        s = out.setdefault(_company(r.get("company")), {"name": r.get("company"), "jobs": [], "best": ""})
        s.setdefault("applied", []).append({"id": r["id"], "role": r.get("role"), "status": r.get("status")})
    for src in load_sources():
        if src.get("favourite"):
            out.setdefault(_company(src.get("company") or src["label"]), {"name": src.get("company") or src["label"],
                                                                          "jobs": [], "best": ""})["favourite"] = True
    out.pop("", None)
    return out


FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vikram", "Isha", "Arjun", "Nisha", "Karan", "Priya", "Sahil",
         "Tara", "Dev", "Leela", "Neel", "Riya", "Zoya", "Aditya", "Maya", "Omar", "Elena", "Jonas", "Sofia", "Lukas", "Hana",
         "Mateo", "Chloe", "Felix"]
LAST = ["Sharma", "Iyer", "Menon", "Rao", "Kapoor", "Nair", "Bose", "Reddy", "Joshi", "Gupta", "Das", "Pillai", "Kulkarni",
        "Verma", "Fischer", "Novak", "Larsen", "Costa", "Weber", "Moreau"]
POSITIONS = ["Senior Software Engineer", "Software Engineer II", "Staff Engineer", "Engineering Manager", "Tech Lead",
             "Technical Recruiter", "Talent Acquisition Partner", "Product Manager", "Director of Engineering",
             "Forward Deployed Engineer", "Data Scientist", "Solutions Architect", "Senior Frontend Engineer",
             "Head of Talent", "Principal Engineer", "Backend Engineer", "VP Engineering", "Designer"]
INVENTED = ["Northwind Labs", "Bluefin Systems", "Quartz Analytics", "Harbor AI", "Lumen Pay", "Orbit Health",
            "Cobalt Cloud", "Fernway", "Kestrel Robotics", "Saffron Retail", "Tidepool Data", "Vela Mobility"]


def sample(signals: dict) -> list[dict]:
    """Invented people at real companies from your own search (or invented companies), the same every time."""
    rng = random.Random(7)
    companies = [s["name"] for s in sorted(signals.values(), key=lambda s: (not s.get("best"), s["name"] or ""))][:28]
    companies = (companies + INVENTED)[:max(12, min(28, len(companies) + 6))]
    people = []
    for i, c in enumerate(companies):
        for _ in range(rng.choice([1, 1, 2, 2, 3, 4, 6, 9]) if i else 11):
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            people.append({"name": name, "company": c, "position": rng.choice(POSITIONS),
                           "url": "https://www.linkedin.com/in/" + name.lower().replace(" ", "-") + f"-sample{rng.randint(10, 99)}",
                           "connected": f"{rng.randint(1, 28):02d} {rng.choice(['Jan', 'Apr', 'Jul', 'Oct'])} {rng.randint(2018, 2026)}"})
    return people


def graph(prefs: dict, want_sample: bool = False) -> dict:
    """{"nodes", "links", "sample", "stats"}: you, the companies, and the people at each."""
    from .warm import _index
    signals = _signals(prefs)
    real = [p for ps in _index().values() for p in ps]
    is_sample = want_sample or not real
    people = sample(signals) if is_sample else real
    by_company: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for p in people:
        key = _company(p["company"])
        if key:
            by_company.setdefault(key, []).append(p)
            names.setdefault(key, p["company"])

    def weight(key):
        s = signals.get(key, {})
        return ({"Strong": 4, "Possible": 3}.get(s.get("best"), 0) + (2 if s.get("applied") else 0)
                + (1 if s.get("favourite") else 0), len(by_company[key]))

    keys = sorted(by_company, key=weight, reverse=True)
    nodes = [{"id": "me", "type": "me", "label": "You"}]
    links = []
    for key in keys[:MAX_COMPANIES]:
        s = signals.get(key, {})
        ps = sorted(by_company[key], key=lambda p: ({"leader": 0, "recruiter": 1, "peer": 2}.get(kind(p["position"]), 3),
                                                    -(strategy.title_level(p["position"]) or 0)))
        status = ("strong" if s.get("best") == "Strong" else "possible" if s.get("best") == "Possible"
                  else "applied" if s.get("applied") else "favourite" if s.get("favourite") else "other")
        cid = f"c:{key}"
        nodes.append({"id": cid, "type": "company", "label": names[key], "count": len(ps), "status": status,
                      "jobs": s.get("jobs", []), "applied": s.get("applied", []), "favourite": bool(s.get("favourite")),
                      "more": max(0, len(ps) - MAX_PEOPLE)})
        links.append({"source": "me", "target": cid})
        for i, p in enumerate(ps[:MAX_PEOPLE]):
            pid = f"p:{key}:{i}"
            nodes.append({"id": pid, "type": "person", "label": p["name"], "company": names[key], "position": p["position"],
                          "url": p.get("url", ""), "kind": kind(p["position"]), "level": strategy.title_level(p["position"])})
            links.append({"source": cid, "target": pid})
    kinds = [kind(p["position"]) for p in people]
    return {"nodes": nodes, "links": links, "sample": is_sample,
            "stats": {"people": len(people), "companies": len(by_company), "drawn": min(len(keys), MAX_COMPANIES),
                      "hiring": sum(1 for k in by_company if signals.get(k, {}).get("best") in ("Strong", "Possible")),
                      "recruiters": kinds.count("recruiter"), "leaders": kinds.count("leader"), "peers": kinds.count("peer")}}
