"""What Discover learns from the jobs you pass on ("Not for me") and the ones you tailor.

Every verdict is kept with the posting's title, company, place, skills and level. From them come plain rules the
candidate can read and take back: a company to hide, title words to push down, a skill or level or place they keep
passing on. A rule needs two or more passes that agree (one click is a data point, not a taste) and gives way when
the candidate tailored jobs that share it. Nothing is a black box: each rule says what it does and how many jobs
taught it, and Discover's "why" line names the rule that moved a job.
"""
import datetime as dt
import re

from . import strategy

REASONS = {"role": "Wrong kind of role", "level": "Wrong level", "company": "Not this company",
           "place": "Wrong place", "stack": "Wrong tech stack"}

STOP = {"and", "or", "of", "the", "for", "in", "at", "to", "with", "a", "an", "on", "from", "by", "&", "-", "/",
        "i", "ii", "iii", "iv", "v", "1", "2", "3", "sr", "jr", "senior", "junior", "lead", "staff", "principal",
        "remote", "hybrid", "onsite", "on-site", "india", "f/m/d", "m/f/d", "all", "genders", "x", "new", "team"}
PUSH_PER_PASS = 10      # each agreeing pass pushes a job further down...
PUSH_CAP = 30           # ...up to this much per rule
HIDE_AT = 4             # this many passes and no tailored job sharing it: hide outright


def _words(title: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9+#.]+", (title or "").lower()) if w and w.strip(".")]


def _norm(s: str) -> str:
    return " ".join(_words(s))


def _protected() -> set[str]:
    """Words that name what the candidate is looking for, so passing on one Engineering Manager job never teaches
    'avoid manager': the track titles, headline examples, level words and the city's names."""
    words = set(STOP)
    for t in strategy.tracks().values():
        for phrase in (t.get("titles") or []) + (t.get("headline_examples") or []) + [t.get("label", "")]:
            words |= set(_words(phrase))
    words |= {w for name in strategy.home().get("city_names", []) for w in _words(name)}
    return words


def _phrases(title: str, protect: set[str]) -> set[str]:
    """Single words and word pairs of a title that say something about the role (not about the level or place)."""
    ws = [w for w in _words(title) if w not in STOP]
    singles = {w for w in ws if w not in protect and len(w) > 2 and not w.isdigit()}
    pairs = {f"{a} {b}" for a, b in zip(ws, ws[1:]) if a not in protect or b not in protect}
    return singles | pairs


# ---------- recording ----------

def record(state: dict, job: dict, verdict: str, reason: str = "") -> None:
    """verdict: 'no' (passed on) or 'yes' (sent to tailoring). The same job again replaces its earlier verdict."""
    from .discover import job_skills
    fb = state.setdefault("feedback", {})
    prev = fb.get(job["id"]) or {}
    fb[job["id"]] = {"v": verdict, "reason": reason if reason in REASONS else prev.get("reason", "") if verdict == "no" else "",
                     "title": job.get("title", ""), "company": job.get("company", ""), "location": job.get("location", ""),
                     "skills": job.get("skills") or (job_skills(dict(job)) if job.get("description") else []),
                     "rank": strategy.title_level(job.get("title", "")), "url": job.get("url", ""),
                     "at": prev.get("at") or dt.datetime.now().isoformat(timespec="seconds")}


def forget(state: dict, key: str) -> None:
    f = state.setdefault("forgotten", [])
    if key not in f:
        f.append(key)


def unforget(state: dict, key: str) -> None:
    state["forgotten"] = [k for k in state.get("forgotten", []) if k != key]


# ---------- learning ----------

def model(state: dict) -> dict:
    fb = list((state.get("feedback") or {}).values())
    no = [f for f in fb if f["v"] == "no"]
    yes = [f for f in fb if f["v"] == "yes"]
    forgotten = set(state.get("forgotten", []))
    protect = _protected()
    rules: list[dict] = []

    def add(key, kind, value, label, n, kept, hide=False):
        if key in forgotten:
            return
        rules.append({"key": key, "kind": kind, "value": value, "label": label, "n": n, "kept": kept,
                      "hide": hide, "push": 0 if hide else min(PUSH_CAP, PUSH_PER_PASS * n)})

    # Companies: one "not this company" is enough; otherwise three passes and nothing tailored there.
    by_co: dict[str, list[dict]] = {}
    for f in no:
        if f["company"].strip():
            by_co.setdefault(_norm(f["company"]), []).append(f)
    yes_co = {_norm(f["company"]) for f in yes}
    for co, fs in by_co.items():
        said = any(f["reason"] == "company" for f in fs)
        if said or (len(fs) >= 3 and co not in yes_co):
            add(f"company:{co}", "company", co, fs[0]["company"], len(fs), 0, hide=said)

    # The same posting again (a repost, or the same job through another board): hidden.
    dupes = {(_norm(f["title"]), _norm(f["company"])) for f in no}

    # Title words: from passes that said "wrong kind of role", or gave no reason.
    titles: dict[str, set[str]] = {}
    for f in no:
        if f["reason"] in ("", "role"):
            for p in _phrases(f["title"], protect):
                titles.setdefault(p, set()).add(_norm(f["title"]))
    kept_titles: dict[str, int] = {}
    for f in yes:
        for p in _phrases(f["title"], protect):
            kept_titles[p] = kept_titles.get(p, 0) + 1
    picked = {p: ts for p, ts in titles.items() if len(ts) >= 2 and kept_titles.get(p, 0) * 3 <= len(ts)}
    # Phrases taught by the very same jobs are one rule: keep the one that says it best, a pair over a single word
    # ("technical writer", not "writer") and the pair with fewer of your own words ("site reliability", not
    # "reliability engineer").
    best: dict[frozenset, str] = {}
    score = lambda q: (-len(q.split()), sum(w in protect for w in q.split()), q)
    for p, ts in picked.items():
        k = frozenset(ts)
        if k not in best or score(p) < score(best[k]):
            best[k] = p
    picked = {p: picked[p] for p in best.values()}
    for p, ts in picked.items():
        n, kept = len(ts), kept_titles.get(p, 0)
        add(f"title:{p}", "title", p, f"“{p}” in the title", n, kept, hide=n >= HIDE_AT and not kept)

    # Skills, levels and places: only from passes that named that reason.
    def counted(reason, values_of):
        c: dict = {}
        for f in no:
            if f["reason"] == reason:
                for v in values_of(f):
                    c[v] = c.get(v, 0) + 1
        return c

    yes_skills = {s for f in yes for s in f.get("skills") or []}
    for s, n in counted("stack", lambda f: f.get("skills") or []).items():
        if n >= 2 and s not in yes_skills:
            add(f"skill:{s}", "skill", s, f"needs {s}", n, 0)
    yes_ranks = {f["rank"] for f in yes}
    level_of = lambda f: [f["rank"]] if f.get("rank") is not None and not strategy.lateral(f["title"]) else []
    for r, n in counted("level", level_of).items():
        if n >= 2 and r not in yes_ranks:
            add(f"level:{r}", "level", r, f"{strategy.LEVELS.get(r, r)} titles", n, 0)
    for p, n in counted("place", lambda f: [_place(f["location"])] if _place(f["location"]) else []).items():
        if n >= 2:
            add(f"place:{p}", "place", p, f"in {p.title()}", n, 0)
    return {"rules": sorted(rules, key=lambda r: (not r["hide"], -r["n"])), "dupes": dupes, "protect": protect}


def _place(location: str) -> str:
    first = re.split(r"[,;|(/]", location or "")[0]
    return _norm(first)


# ---------- applying ----------

def apply(job: dict, m: dict) -> dict | None:
    """The job with its order lowered by the rules it matches, or None when a rule hides it."""
    if not m["rules"] and not m["dupes"]:
        return job
    title, co = _norm(job.get("title", "")), _norm(job.get("company", ""))
    if (title, co) in m["dupes"]:
        return None
    phrases = None
    hits = []
    for r in m["rules"]:
        k, v = r["kind"], r["value"]
        if k == "company":
            hit = co and co == v
        elif k == "title":
            if phrases is None:
                phrases = _phrases(job.get("title", ""), m["protect"])
            hit = v in phrases
        elif k == "skill":
            hit = v in (job.get("skills") or [])
        elif k == "level":   # a Senior title in a role you'd move sideways into isn't the level you passed on
            hit = strategy.title_level(job.get("title", "")) == v and not strategy.lateral(job.get("title", ""))
        else:
            hit = _place(job.get("location", "")) == v
        if hit:
            if r["hide"]:
                return None
            hits.append(r)
    if not hits:
        return job
    push = min(45, sum(r["push"] for r in hits))
    why = "like jobs you passed on (" + "; ".join(r["label"] for r in hits[:2]) + ")"
    return job | {"order": job.get("order", job.get("match", 0)) - push, "reasons": (job.get("reasons") or []) + [why],
                  "learned": [r["key"] for r in hits]}


def summary(state: dict, limit: int = 60) -> dict:
    """For Discover: the rules in force, the ones taken back, and the jobs passed on, newest first."""
    m = model(state)
    fb = state.get("feedback") or {}
    passed = sorted(({"id": k} | {x: f[x] for x in ("title", "company", "location", "reason", "at", "url")}
                     for k, f in fb.items() if f["v"] == "no"), key=lambda f: f["at"], reverse=True)
    return {"rules": [{k: r[k] for k in ("key", "kind", "label", "n", "kept", "hide", "push")} for r in m["rules"]],
            "forgotten": state.get("forgotten", []), "passed": passed[:limit], "passed_total": len(passed),
            "kept_total": sum(1 for f in fb.values() if f["v"] == "yes"), "reasons": REASONS}
