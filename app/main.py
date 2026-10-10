"""Local web app: http://127.0.0.1:8765. Long work (model calls, scans) runs as background jobs the
page polls, so nothing waits on a frozen request."""
import datetime as dt
import threading
import traceback
import uuid

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import areas, batch, discover, extract, letters, tailor, tracker
from .config import OUTPUT, STATIC
from .engines import ENGINES
from .layouts import LAYOUTS
from .profile import experience_years_label, load_profile, save_profile
from .render import in_thread

app = FastAPI(title="CV Tailor")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

JOBS: dict[str, dict] = {}
DEFAULT_PREFS = {"tracks": ["ic_lead", "engineering_manager", "product_manager", "delivery_lead", "staff",
                            "forward_deployed", "solutions_gtm", "ai_engineer"],
                 "india": True, "remote": True, "abroad": True, "days": 14, "concurrency": 2,
                 "include_senior": False, "exclude_skills": ["Java", ".NET", "C#"], "max_years_asked": 11, "offices": "",
                 "regions": ["remote", "bengaluru", "india", "world"],
                 "defaults": {"market": "auto", "track": "auto", "variant": "flowcv", "max_pages": "auto",
                              "engine": "claude:opus"}}


def start_job(kind: str, fn, *args) -> str:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "running", "log": [], "result": None, "error": None,
           "started": dt.datetime.now().isoformat(timespec="seconds")}
    JOBS[jid] = job

    def progress(msg: str):
        job["log"].append({"t": dt.datetime.now().strftime("%H:%M:%S"), "msg": msg})

    def target():
        try:
            job["result"] = fn(*args, progress)
            job["status"] = "done"
        except Exception as e:
            job["status"], job["error"] = "error", str(e)
            job["trace"] = traceback.format_exc()[-2000:]

    threading.Thread(target=target, daemon=True).start()
    return jid


def _prefs(profile: dict) -> dict:
    from .profile import experience_months
    return DEFAULT_PREFS | (profile.get("preferences") or {}) | {"candidate_years": experience_months(profile) // 12}


def _app_dir(app_id: str):
    path = (OUTPUT / app_id).resolve()
    if OUTPUT.resolve() not in path.parents or not (path / "report.json").exists():
        raise HTTPException(404, "No such application")
    return path


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state():
    profile = load_profile()
    m = tailor.markets()
    return {
        "profile": {"basics": profile["basics"], "confirmed_skills": profile.get("confirmed_skills", []),
                    "experience_years": experience_years_label(profile), "preferences": _prefs(profile)},
        "markets": {k: {"label": v["label"], "max_pages": v["max_pages"], "gdpr": v.get("gdpr_clause", False),
                        "header_line": tailor.default_header_line(k, profile)} for k, v in m.items()},
        "layouts": {k: {"label": v["label"], "blurb": v["blurb"]} for k, v in LAYOUTS.items()},
        "tracks": {k: {"label": v["label"], "examples": v["headline_examples"]} for k, v in tailor.tracks().items()},
        "voices": {k: {"label": v["label"], "best_for": v["best_for"]} for k, v in letters.voices().items() if k != "banned_phrases"},
        "engines": [{"id": e.id, "label": e.label, "available": e.available()} for e in ENGINES.values()],
        "statuses": tracker.STATUSES,
        "areas": areas.catalogue(),
    }


@app.post("/api/profile")
def update_profile(body: dict = Body(...)):
    profile = load_profile()
    if "availability" in body:
        profile["basics"]["availability"] = body["availability"].strip()
    if body.get("confirm"):
        have = set(profile.get("confirmed_skills", []))
        profile["confirmed_skills"] = sorted(have | {s.strip() for s in body["confirm"] if s.strip()})
    if body.get("unconfirm"):
        profile["confirmed_skills"] = [s for s in profile.get("confirmed_skills", []) if s not in set(body["unconfirm"])]
    if "preferences" in body:
        prefs = _prefs(profile)
        new = body["preferences"]
        if "defaults" in new:
            new = new | {"defaults": prefs["defaults"] | new["defaults"]}
        profile["preferences"] = prefs | new
        batch.set_concurrency(profile["preferences"].get("concurrency", 2))
    save_profile(profile)
    return state()["profile"]


# ---------- tailoring ----------

@app.post("/api/tailor")
def tailor_start(body: dict = Body(...)):
    if len((body.get("jd") or "").strip()) < 200:
        raise HTTPException(400, "Paste the full job description (at least a few paragraphs).")
    return {"job": start_job("tailor", lambda req, progress: tailor.run(req, progress), body)}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "No such job")
    return job


@app.get("/api/apps")
def apps():
    return tracker.load()


@app.get("/api/apps/{app_id}")
def app_report(app_id: str):
    folder = _app_dir(app_id)
    jd = folder / "jd.txt"
    row = next((r for r in tracker.load() if r["id"] == app_id), {})
    return tailor.load_report(app_id) | {"jd_text": jd.read_text("utf-8") if jd.exists() else "",
                                         "job_url": row.get("job_url", ""), "domain": row.get("domain", "")}


@app.post("/api/apps/{app_id}/render")
def app_render(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    return in_thread(tailor.rerender, app_id, body)


@app.get("/api/apps/{app_id}/plan")
def app_plan(app_id: str):
    _app_dir(app_id)
    return tailor.get_plan(app_id)


@app.put("/api/apps/{app_id}/plan")
def app_plan_save(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    return in_thread(tailor.save_plan, app_id, body)


@app.post("/api/apps/{app_id}/edited")
def app_edited(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    return in_thread(tailor.apply_edits, app_id, body["html"])


@app.get("/apps/{app_id}/cv.html", response_class=HTMLResponse)
def app_html(app_id: str):
    return HTMLResponse((_app_dir(app_id) / "cv.html").read_text("utf-8"))


@app.get("/api/apps/{app_id}/changes")
def app_changes(app_id: str):
    _app_dir(app_id)
    return tailor.changes(app_id)


@app.get("/apps/{app_id}/changes.html", response_class=HTMLResponse)
def app_changes_html(app_id: str):
    _app_dir(app_id)
    return HTMLResponse(tailor.changes_html(app_id))


@app.get("/apps/{app_id}/pdf")
def app_pdf(app_id: str, download: int = 0):
    folder = _app_dir(app_id)
    name = tailor.load_report(app_id)["pdf"]
    return FileResponse(folder / name, media_type="application/pdf", filename=name,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "no-store"})


@app.get("/apps/{app_id}/logo")
def app_logo(app_id: str):
    p = _app_dir(app_id) / "logo.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.post("/api/apps/{app_id}/letter")
def letter_start(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    voice, engine = body.get("voice", "plainspoken"), body.get("engine", "claude:opus")
    return {"job": start_job("letter", lambda a, v, e, progress: (progress(f"Writing the {v} voice"), letters.write_letter(a, v, e))[1],
                             app_id, voice, engine)}


@app.post("/api/apps/{app_id}/letter/keep")
def letter_keep(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    letters.keep_letter(app_id, body["text"], body.get("voice", ""))
    return {"ok": True}


@app.post("/api/apps/{app_id}/track")
def app_track(app_id: str, body: dict = Body(...)):
    try:
        return tracker.update(app_id, body)
    except KeyError:
        raise HTTPException(404, "Not in the tracker")


@app.get("/api/export.xlsx")
def export():
    path = tracker.export_xlsx()
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/preview.pdf")
def preview(variant: str = "flowcv", max_pages: str = "auto", market: str = "india", track: str = "ic_lead"):
    pdf = in_thread(tailor.preview_base, {"variant": variant, "max_pages": max_pages, "market": market})
    return FileResponse(pdf, media_type="application/pdf", headers={"Cache-Control": "no-store"})


# ---------- career map ----------

@app.get("/api/career")
def career_get():
    from . import career
    return {"map": career.load(), "log": career.log(), "kinds": career.LOG_KINDS,
            "history": career.history(load_profile()), "goal": career.goal(), "salaries": career.salaries(),
            "projection": career.projection()}


@app.post("/api/career/goal")
def career_goal(body: dict = Body(...)):
    from . import career
    return career.set_goal(body)


@app.post("/api/career/salary")
def career_salary(body: dict = Body(...)):
    from . import career
    try:
        return career.add_salary(body.get("year"), body.get("amount"), body.get("unit", "LPA"), body.get("note", ""))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e) or "Give a year and a yearly amount.")


@app.delete("/api/career/salary/{row_id}")
def career_salary_remove(row_id: str):
    from . import career
    career.remove_salary(row_id)
    return {"ok": True}


@app.post("/api/career/generate")
def career_generate(body: dict = Body(default={})):
    from . import career
    engine = body.get("engine") or _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    return {"job": start_job("career", lambda e, progress: career.generate(e, progress), engine)}


@app.post("/api/career/log")
def career_log_add(body: dict = Body(...)):
    from . import career
    try:
        return career.add_log(body.get("kind", "did"), body.get("text", ""), body.get("date", ""), body.get("use_on_cv", False))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/career/log/{entry_id}")
def career_log_remove(entry_id: str):
    from . import career
    career.remove_log(entry_id)
    return {"ok": True}


@app.post("/api/career/jobs")
def career_jobs(body: dict = Body(...)):
    from . import career
    return career.matching_jobs(body.get("titles") or [], int(body.get("days") or 30), _prefs(load_profile()))


@app.post("/api/career/search")
def career_search(body: dict = Body(...)):
    from . import career
    prefs = _prefs(load_profile())
    return {"job": start_job("career-search", lambda t, progress: career.search_web(t, prefs, progress), body.get("titles") or [])}


# ---------- links and batches ----------

@app.post("/api/extract")
def extract_start(body: dict = Body(...)):
    return {"job": start_job("extract", lambda v, progress: extract.extract(v, progress), body.get("url", ""))}


@app.get("/api/batch")
def batch_list():
    return batch.items()


@app.post("/api/batch")
def batch_add(body: dict = Body(...)):
    prefs = _prefs(load_profile())
    batch.set_concurrency(prefs.get("concurrency", 2))
    return {"ids": batch.add(body.get("items") or [], prefs["defaults"] | (body.get("defaults") or {}))}


@app.post("/api/batch/{item_id}/retry")
def batch_retry(item_id: str):
    return {"ids": batch.retry(item_id)}


@app.delete("/api/batch/{item_id}")
def batch_remove(item_id: str):
    batch.remove(item_id)
    return {"ok": True}


# ---------- discovery ----------

@app.get("/api/sources")
def sources():
    return discover.load_sources()


@app.post("/api/sources")
def source_add(body: dict = Body(...)):
    try:
        return discover.add_source(body["value"], body.get("kind", "url"), body.get("label", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/sources/{sid}")
def source_update(sid: str, body: dict = Body(...)):
    try:
        return discover.update_source(sid, body.get("value"), body.get("label"))
    except KeyError:
        raise HTTPException(404)


@app.delete("/api/sources/{sid}")
def source_delete(sid: str):
    discover.remove_source(sid)
    return {"ok": True}


@app.post("/api/scan")
def scan_start(body: dict = Body(...)):
    prefs = _prefs(load_profile())
    days = int(body.get("days") or prefs["days"])
    return {"job": start_job("scan", lambda ids, d, p, progress: discover.scan(ids, d, p, progress=progress),
                             body.get("source_ids"), days, prefs)}


@app.get("/api/found")
def found(days: int = 14):
    return discover.saved(days, _prefs(load_profile()))


@app.get("/api/scans")
def scan_history():
    return discover.scans()


@app.post("/api/found/{job_id}/dismiss")
def found_dismiss(job_id: str):
    discover.dismiss(job_id)
    return {"ok": True}


@app.get("/api/found/{job_id}/detail")
def found_detail(job_id: str):
    try:
        return in_thread(discover.job_detail, job_id)
    except KeyError:
        raise HTTPException(404)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
