"""How likely a written CV is to get a callback: a rough guide shown beside the keyword score, so a 100% keyword
match isn't read as "they'll call".

The keyword score says whether an ATS search and a skim will find your skills. Whether anyone calls depends on what
recruiters screen on first: the title you've held, your level and years, where you can work, the must-haves, and
above all how the application reached them. Published averages (Ashby, about 250,000 hires): about 3% of inbound
applications reach an interview, against about 40% of referred ones. The fit label places a job against those
averages; the bands below are a judgement on top of them, not a measurement, and your own numbers replace them once
Insights has ten applications with that label.
"""
import json

from .config import output_dir

SOURCE = "Ashby talent trends: about 3% of inbound applications and about 40% of referrals reach an interview"
BANDS = {   # (cold application, with a referral), in per cent
    "Strong": ((6, 12), (35, 50)),
    "Possible": ((2, 6), (20, 35)),
    "Long shot": ((0, 2), (5, 15)),
}
OWN_FROM = 10


def band(label: str, warm: bool = False) -> str:
    lo, hi = BANDS.get(label, BANDS["Possible"])[1 if warm else 0]
    return f"under {hi}%" if lo == 0 else f"about {lo} to {hi}%"


def for_app(app_id: str, profile: dict, prefs: dict) -> dict:
    """{"label", "band", "why", "warm", "own", "source"} for one tailored CV."""
    from . import gate, warm
    from .discover import label_for
    folder = output_dir() / app_id
    report = json.loads((folder / "report.json").read_text("utf-8"))
    jd = (folder / "jd.txt").read_text("utf-8") if (folder / "jd.txt").exists() else ""
    job = report.get("job") or {}
    # Worked out afresh: the profile, the settings and the posting's age may have moved on since the CV was written.
    fit = label_for({"title": report.get("role", ""), "company": report.get("company", ""),
                     "location": job.get("location") or "", "description": jd, "posted": job.get("posted") or "",
                     "method": ""}, profile, prefs)
    g = gate.check(jd, report.get("role", ""), job.get("location") or "", report.get("market", ""), None, profile, prefs,
                   company=report.get("company", ""))
    label = "Long shot" if g["verdict"] == "stop" else fit["label"] or "Possible"
    why = g["reasons"][:2] if g["verdict"] != "ok" else fit["why"][:4]
    people = warm.who_to_ask(report.get("company", ""), report.get("role", ""), 3)
    return {"label": label, "band": band(label), "warm_band": band(label, True), "why": why,
            "warm": [p["name"] for p in people], "own": own_rate(label), "source": SOURCE}


def own_rate(label: str) -> dict | None:
    """Your own callback rate for jobs with this label, once there are enough of them."""
    from . import tracker
    from .insights import compute
    if sum(1 for r in tracker.load() if tracker.went_out(r)) < OWN_FROM:   # too few for any label: skip the database
        return None
    try:
        row = next((x for x in compute()["callbacks"]["by_fit"] if x["group"] == label), None)
    except Exception:  # Insights needs the local database; the guide stands without it
        return None
    if not row or row["applied"] < OWN_FROM:
        return None
    return {"applied": row["applied"], "callbacks": row["callbacks"], "rate": row["rate"]}
