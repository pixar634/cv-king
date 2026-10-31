"""One label for the chance a job turns into a callback: Strong, Possible or Long shot.

A keyword match says how much of a posting your profile can cover; it doesn't say whether a screener calls. What
does is mostly known before applying: a title you've held, your level, the must-haves covered, a fresh posting, a
job open to you, pay that meets your floor, applying on the company's own site, and someone you know there. The
hard screens (level far above, closed to you, below your pay floor) are hidden before this is reached.
"""
import datetime as dt
import re

LEVEL = re.compile(r"(?i)(?<![a-z])(junior|jr\.?|associate|senior|sr\.?|lead|principal|staff|head of|i{1,3}|iv|[1-4])(?![a-z])")
ATS = re.compile(r"greenhouse|lever|ashby|smartrecruiters|workable|personio|recruitee|careers page|company site", re.I)
STRONG, POSSIBLE = 6, 3
THIN_TERMS = 8   # specific skills a posting must name before its keyword match says much


def core(title: str) -> str:
    """A title without its level and extras: "Senior Software Engineer, Payments" -> "software engineer"."""
    t = re.split(r"[,(|/–-]\s", (title or "").lower())[0]
    t = LEVEL.sub(" ", t.replace("full stack", "full-stack").replace("fullstack", "full-stack").replace("developer", "engineer"))
    return " ".join(re.findall(r"[a-z+#-]+", t))


def held(title: str, held_titles: list[str]) -> bool:
    """You've held this kind of role: the job's title, without its level, is one of yours or contains one, and it is
    the same kind of role ("Manager of Software Engineering" contains "Software Engineer" but is a manager's job)."""
    from .outcomes import kind
    c, k = core(title), kind(title)
    return bool(c) and any(h and (c == h or h in c or c in h) and kind(t) == k
                           for t, h in ((t, core(t)) for t in held_titles))


def days_old(job: dict) -> int | None:
    for k in ("posted", "first_seen"):
        try:
            return (dt.date.today() - dt.date.fromisoformat(str(job.get(k) or "")[:10])).days
        except ValueError:
            continue
    return None


def compute(job: dict, *, target: bool, held_titles: list[str], level: str, at_level: bool, must: tuple[int, int],
            terms: int, match: int, pay: dict, open_state: str, warm: bool = False, inbound: bool = False,
            history: tuple[int, str] = (0, "")) -> dict:
    """{"label", "points", "why"}: points from what predicts a callback; why lists the ones that counted.
    match is 0 for a posting not scored yet. history: (points, why) off for a kind of role that has never brought
    you a callback (outcomes.penalty)."""
    pts, why = 0, []

    def add(n, reason):
        nonlocal pts
        pts += n
        why.append(reason)

    if warm:
        add(3, "you know someone there")
    if inbound:
        add(2, "a recruiter wrote to you")
    if target:
        add(2, "one of your tracks")
    if held(job.get("title", ""), held_titles):
        add(2, "a title you've held")
    if level == "stretch":
        add(-2, "a stretch")
    elif at_level:
        add(1, "at your level")
    covered, required = must
    if match and terms < THIN_TERMS:
        # A handful of generic skills (Python, LLMs) covers almost anyone: a high share proves little, a low one still counts.
        add(-1, f"names only {terms} skills to measure")
        if required >= 3 and covered / required < .4:
            add(-2, f"{covered} of {required} must-haves")
    elif required >= 3:
        share = covered / required
        add(2 if share >= .8 else 1 if share >= .6 else -2 if share < .4 else 0,
            f"{covered} of {required} must-haves")
    elif terms >= THIN_TERMS:
        add(1 if match >= 80 else -1 if match < 55 else 0, f"{match}% of its skills")
    if history[0]:
        add(-history[0], history[1])
    age = days_old(job)
    if age is not None and age <= 3:
        add(1, "posted in the last 3 days")
    elif age is not None and age > 14:
        add(-1, f"{age} days old")
    if pay.get("label") == "meets":
        add(1, "pay meets your floor")
    elif pay.get("tier") == "low":
        add(-1, "pays below your target")
    if open_state == "unclear":
        add(-1, "eligibility unclear")
    if ATS.search(job.get("method") or job.get("source") or ""):
        add(1, "on the company's own site")
    label = "Strong" if pts >= STRONG else "Possible" if pts >= POSSIBLE else "Long shot"
    return {"label": label, "points": pts, "why": [w for w in why if w]}
