"""What your own applications say about a kind of role: the scan marks down a kind that never brings a callback.

A role's kind comes from its title alone (people manager, product or programme manager, architect, research), so a
new posting can be matched against the applications already sent. Only settled applications count: a reply, or no
reply after SETTLE_DAYS. A kind with no callback in MIN_SETTLED settled applications costs points in the fit label;
in COLD_SETTLED, enough to drop below Possible. One callback in a kind clears it.
"""
import datetime as dt
import re

SETTLE_DAYS = 14     # an application with no reply after this long counts as silence (Insights' "waiting" too)
MIN_SETTLED = 3
COLD_SETTLED = 5
CALLBACK = {"Recruiter call", "Interviewing", "Offer"}

# Checked in order: "Technical Program Manager" is a programme role, not a people manager.
KINDS = [
    ("product or programme manager", re.compile(
        r"(?i)\b(product|program|programme|project|delivery)\s+(manager|management|owner)\b|\btpm\b|\bproduct owner\b")),
    ("people manager", re.compile(
        r"(?i)\bmanager\b|\bdirector\b|\bhead of\b|\bvp\b|\bvice president\b|\bchief\b|\bcto\b")),
    ("architect", re.compile(r"(?i)\barchitect\b")),
    ("research", re.compile(r"(?i)\bresearch(er)?\b|\bscientist\b")),
]


def kind(title: str) -> str | None:
    """The kind of role a title names, or None for an engineer's title the history doesn't split further."""
    return next((name for name, pat in KINDS if pat.search(title or "")), None)


def _history() -> dict[str, set[str]]:
    """Every status each application reached: a rejection after an interview was still a callback."""
    from contextlib import closing

    from . import db
    out: dict[str, set[str]] = {}
    try:
        with closing(db.connect()) as con:
            for app_id, status in con.execute("SELECT app_id, to_status FROM status_events"):
                out.setdefault(app_id, set()).add(status)
    except Exception:   # the history is a nicety; the tracker row is what counts
        pass
    return out


def by_kind(rows: list[dict] | None = None, today: dt.date | None = None) -> dict[str, dict]:
    """{kind: {"settled": n, "callbacks": n, "rejected": n}} over the applications that went out."""
    from .tracker import load, went_out
    today = today or dt.date.today()
    out: dict[str, dict] = {}
    events = _history()
    for r in load() if rows is None else rows:
        k = kind(r.get("role", ""))
        if not k or not went_out(r) or r.get("status") == "Withdrawn":
            continue
        reached = events.get(r["id"], set()) | {r.get("status")}
        try:
            age = (today - dt.date.fromisoformat((r.get("applied_on") or r.get("created") or "")[:10])).days
        except ValueError:
            age = 0
        callback, rejected = bool(reached & CALLBACK), "Rejected" in reached
        if not (callback or rejected or r.get("status") == "No response" or age >= SETTLE_DAYS):
            continue    # still waiting: says nothing yet
        g = out.setdefault(k, {"settled": 0, "callbacks": 0, "rejected": 0})
        g["settled"] += 1
        g["callbacks"] += callback
        g["rejected"] += rejected
    return out


def penalty(title: str, history: dict[str, dict]) -> tuple[int, str]:
    """(points to take off, why) for a job whose kind of role has gone cold in your own applications."""
    k = kind(title)
    g = (history or {}).get(k or "")
    if not g or g["callbacks"] or g["settled"] < MIN_SETTLED:
        return 0, ""
    why = f"no callback from your {g['settled']} {k} applications"
    if g["rejected"]:
        why += f" ({g['rejected']} rejected)"
    return (4 if g["settled"] >= COLD_SETTLED else 2), why
