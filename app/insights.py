"""What your own search says so far, from the local database (db.py).

Advisory only: nothing here changes a score or a filter. A rate is withheld below MIN_N applications, because two
replies out of three says nothing yet."""
import datetime as dt
import statistics
from contextlib import closing

import json

from . import db, strategy
from .config import output_dir
from .keywords import taxonomy
from .profile import load_profile, profile_text

MIN_N = 5
REPLIED = {"Recruiter call", "Interviewing", "Offer", "Rejected"}   # a rejection is a reply too
# A callback: someone at the employer invites you to a next step. A rejection is a reply, never a callback.
CALLBACK = {"Recruiter call", "Interviewing", "Offer"}
TARGET = 30          # the callback rate to aim for, in percent
PAUSE_AFTER = 10     # applications in a track with no callback before Insights suggests pausing it
AGES = [(0, 3, "0 to 3 days"), (4, 7, "4 to 7 days"), (8, 14, "8 to 14 days"), (15, 10_000, "15 days or more")]
INTERVIEW = {"Interviewing", "Offer"}
BANDS = [(0, 50, "Under 50%"), (50, 60, "50 to 59%"), (60, 70, "60 to 69%"), (70, 80, "70 to 79%"), (80, 101, "80% and up")]
WAIT_DAYS = 14


def _day(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _report(app_id: str) -> dict:
    try:
        return json.loads((output_dir() / app_id / "report.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def most_missed(con) -> list[dict]:
    """Required skills the tailored jobs asked for that the profile lacked, most asked first: [{term, jobs}].
    Each report says what the profile lacked when it was tailored; what the profile (or memory) has now is skipped."""
    gaps = [dict(r) for r in con.execute(
        "SELECT term, COUNT(DISTINCT app_id) AS jobs FROM keywords WHERE in_profile = 0 AND tier = 'required' "
        "GROUP BY term ORDER BY jobs DESC, term")]
    ptext, terms = profile_text(load_profile()), taxonomy().terms
    return [g for g in gaps if not (g["term"] in terms and any(p.search(ptext) for p in terms[g["term"]].patterns))]


def compute(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    db.sync()
    with closing(db.connect()) as con:
        apps = [dict(r) for r in con.execute("SELECT id, company, role, status, score_ceiling, applied_on, created, channel, "
                                             "posting_age FROM applications")]
        events: dict[str, list[dict]] = {}
        for e in con.execute("SELECT app_id, to_status, at, source FROM status_events ORDER BY at, id"):
            events.setdefault(e["app_id"], []).append(dict(e))
        missing = most_missed(con)[:10]

    rows = []
    for a in apps:
        ev = events.get(a["id"], [])
        reached = {e["to_status"] for e in ev} | {a["status"]}
        applied_at = next((e["at"] for e in ev if e["to_status"] != "Generated"), None) or a["applied_on"]
        reply = next((e for e in ev if e["to_status"] in REPLIED), None)
        # A reply seeded from before the history began carries the applied day, not its own: its date is unknown.
        reply_day = _day(reply["at"]) if reply and reply["source"] != "seed" else None
        rep = _report(a["id"])
        rows.append(a | {
            "applied": bool(reached - {"Generated"}), "callback": bool(reached & CALLBACK),
            "track": ((rep.get("options") or {}).get("track") or ""), "fit": rep.get("fit_label") or "",
            "replied": bool(reached & REPLIED), "interview": bool(reached & INTERVIEW), "offer": "Offer" in reached,
            "applied_day": _day(applied_at or ""), "reply_day": reply_day,
            "approx": any(e["source"] == "seed" for e in ev if e["to_status"] != "Generated"),
        })
    applied = [r for r in rows if r["applied"]]

    def rate(part: int, n: int) -> int | None:
        return round(100 * part / n) if n >= MIN_N else None

    bands = []
    for lo, hi, label in BANDS:
        inb = [r for r in applied if r["score_ceiling"] is not None and lo <= r["score_ceiling"] < hi]
        bands.append({"band": label, "applied": len(inb), "replied": sum(r["replied"] for r in inb),
                      "interviews": sum(r["interview"] for r in inb),
                      "reply_rate": rate(sum(r["replied"] for r in inb), len(inb)),
                      "interview_rate": rate(sum(r["interview"] for r in inb), len(inb))})

    waits = [(r["reply_day"] - r["applied_day"]).days for r in applied
             if r["applied_day"] and r["reply_day"] and r["reply_day"] >= r["applied_day"]]
    waiting = sorted(({"id": r["id"], "company": r["company"], "role": r["role"], "days": (today - r["applied_day"]).days}
                      for r in applied if not r["replied"] and r["status"] not in ("Withdrawn", "No response")
                      and r["applied_day"] and (today - r["applied_day"]).days >= WAIT_DAYS), key=lambda w: -w["days"])
    labels = {tid: t.get("label", tid) for tid, t in strategy.tracks().items()}

    def by(key, name=lambda v: v or "Not recorded"):
        groups: dict[str, list] = {}
        for r in applied:
            groups.setdefault(name(r[key]), []).append(r)
        return sorted(({"group": g, "applied": len(rs), "callbacks": sum(r["callback"] for r in rs),
                        "rate": rate(sum(r["callback"] for r in rs), len(rs))} for g, rs in groups.items()),
                      key=lambda x: (-x["applied"], x["group"]))

    age_band = lambda n: next((label for lo, hi, label in AGES if n is not None and lo <= n <= hi), None)
    tracks = by("track", lambda v: labels.get(v, v) or "Not recorded")
    pause = [t["group"] for t in tracks if t["applied"] >= PAUSE_AFTER and not t["callbacks"] and t["group"] != "Not recorded"]
    return {
        "callback_rate": rate(sum(r["callback"] for r in applied), len(applied)), "target": TARGET,
        "callbacks": {"by_channel": by("channel"), "by_track": tracks, "by_fit": by("fit"),
                      "by_age": by("posting_age", lambda v: age_band(v) or "Not recorded"), "pause": pause,
                      "pause_after": PAUSE_AFTER},
        "funnel": {"tailored": len(rows), "applied": len(applied), "replied": sum(r["replied"] for r in applied),
                   "callbacks": sum(r["callback"] for r in applied),
                   "interviews": sum(r["interview"] for r in applied), "offers": sum(r["offer"] for r in applied)},
        "reply_rate": rate(sum(r["replied"] for r in applied), len(applied)),
        "bands": bands,
        "days_to_reply": {"median": statistics.median(waits) if waits else None, "n": len(waits)},
        "waiting": waiting[:8], "waiting_count": len(waiting), "wait_days": WAIT_DAYS,
        "missing": missing,
        "approximate": any(r["approx"] for r in applied),
        "min_n": MIN_N,
    }
