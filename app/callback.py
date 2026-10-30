"""How likely a written CV is to get a callback: a rough guide shown beside the keyword score, so a 100% keyword
match isn't read as "they'll call".

The keyword score says whether an ATS search and a skim will find your skills. Whether anyone calls depends on what
recruiters screen on first: the title you've held, your level and years, where you can work, the must-haves, and
above all how the application reached them. Published averages (Ashby, about 250,000 hires): about 3% of inbound
applications reach an interview, against about 40% of referred ones. The fit label places a job against those
averages; the bands below are a judgement on top of them, not a measurement, and your own numbers replace them once
Insights has ten applications with that label.
"""
import contextlib
import contextvars
import json
import threading
import time

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


def _sig(app_id: str, prefs: dict) -> str:
    """What the guide depends on: the day (a posting ages), the profile, strategy, connections, settings, the report."""
    import datetime as dt
    import hashlib

    from .config import data_dir
    stamp = lambda p: p.stat().st_mtime if p.exists() else 0
    parts = [dt.date.today().isoformat(), SIG_VERSION, stamp(data_dir() / "base_cv.json"), stamp(data_dir() / "strategy.json"),
             stamp(data_dir() / "connections.json"), stamp(output_dir() / app_id / "report.json"),
             json.dumps({k: v for k, v in prefs.items() if not k.startswith("_")}, sort_keys=True, default=str)]
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


SIG_VERSION = 3


def for_app(app_id: str, profile: dict, prefs: dict, own: dict | None = None) -> dict:
    """{"label", "band", "why", "warm", "own", "missing", "source"} for one tailored CV, kept until something it
    depends on changes, so the Pipeline can show it on every card. `own`: your own rates by label, worked out once
    by the caller for a whole list."""
    cache = output_dir() / app_id / "callback.json"
    sig = _sig(app_id, prefs)
    try:
        kept = json.loads(cache.read_text("utf-8"))
        if kept.get("sig") == sig:
            return kept["value"] | {"own": (own if own is not None else own_rates()).get(kept["value"]["label"])}
    except (OSError, ValueError, KeyError):
        pass
    value = _work_out(app_id, profile, prefs)
    try:
        cache.write_text(json.dumps({"sig": sig, "value": value}, ensure_ascii=False), "utf-8")
    except OSError:
        pass
    return value | {"own": (own if own is not None else own_rates()).get(value["label"])}


_busy = threading.Lock()
_serving = 0   # requests the app is answering right now (see serving)
_serving_lock = threading.Lock()


@contextlib.contextmanager
def serving():
    """Mark a request in progress. Working out a guide is pure Python and holds the GIL: run beside a request, it made
    the Pipeline's first visit of the day (every guide out of date) wait 10 seconds for its cards instead of 1."""
    global _serving
    with _serving_lock:
        _serving += 1
    try:
        yield
    finally:
        with _serving_lock:
            _serving -= 1


def _wait_for_requests(most: float = 2.0) -> None:
    """Between two guides, let the requests in progress finish first (for a while: a long one doesn't stall it)."""
    end = time.monotonic() + most
    while _serving and time.monotonic() < end:
        time.sleep(0.05)


def cached(app_id: str, prefs: dict, own: dict) -> dict | None:
    """The guide for one CV if it's already worked out and still current, else None (never slow)."""
    try:
        kept = json.loads((output_dir() / app_id / "callback.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if kept.get("sig") != _sig(app_id, prefs):
        return None
    return kept["value"] | {"own": own.get(kept["value"]["label"])}


def work_out_soon(app_ids: list[str], profile: dict, prefs: dict) -> bool:
    """Work out the guide for these CVs in the background, one batch at a time; True while one is running."""
    if not app_ids:
        return False
    if not _busy.acquire(blocking=False):
        return True

    def run():
        try:
            for app_id in app_ids:
                _wait_for_requests()
                try:
                    for_app(app_id, profile, prefs, {})
                except Exception:  # an old folder without what the guide needs: skip it
                    continue
        finally:
            _busy.release()
    threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True).start()   # keeps the profile
    return True


def _work_out(app_id: str, profile: dict, prefs: dict) -> dict:
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
    label = fit["label"] or "Possible"
    if g["verdict"] == "stop":
        label = "Long shot"
    elif g["verdict"] == "stretch" and label == "Strong":   # a screen it may fail caps it
        label = "Possible"
    why = g["reasons"][:2] if g["verdict"] != "ok" else fit["why"][:4]
    people = warm.who_to_ask(report.get("company", ""), report.get("role", ""), 3)
    return {"label": label, "band": band(label), "warm_band": band(label, True), "why": why,
            "warm": [p["name"] for p in people], "missing": g.get("missing", []), "source": SOURCE}


def own_rates() -> dict:
    """Your own callback rate by fit label, for labels with enough applications: {label: {applied, callbacks, rate}}."""
    from . import tracker
    from .insights import compute
    if sum(1 for r in tracker.load() if tracker.went_out(r)) < OWN_FROM:   # too few for any label: skip the database
        return {}
    try:
        rows = compute()["callbacks"]["by_fit"]
    except Exception:  # Insights needs the local database; the guide stands without it
        return {}
    return {x["group"]: {"applied": x["applied"], "callbacks": x["callbacks"], "rate": x["rate"]}
            for x in rows if x["applied"] >= OWN_FROM}
