"""Local web app: http://127.0.0.1:8765. Long work (model calls, scans) runs as background jobs the
page polls, so nothing waits on a frozen request."""
import contextvars
import datetime as dt
import threading
import traceback
import uuid

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import (apply, areas, batch, discover, extract, letterfile, letters, memory, onboarding, profiles, strategy, tailor,
               tracker)
from .config import STATIC, output_dir
from .engines import ENGINES, EngineError
from .jd import parse_jd
from .layouts import LAYOUTS
from .profile import experience_years_label, load_profile, profile_path, save_profile
from .render import in_thread

app = FastAPI(title="JobMagnet")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

JOBS: dict[str, dict] = {}
ONBOARDING: dict[str, str] = {}   # profile id -> the setup job reading its CV


@app.middleware("http")
async def pick_profile(request: Request, call_next):
    """Whose data this request is about: the profile in the cookie (the last one used when there is none)."""
    pid = request.cookies.get(profiles.COOKIE)
    token = profiles.activate(pid if pid and profiles.get(pid) else profiles.last_used() or None)
    try:
        return await call_next(request)
    finally:
        profiles.reset(token)


def _default_prefs(profile: dict) -> dict:
    from .profile import experience_months
    years = experience_months(profile) // 12
    return {"tracks": list(strategy.tracks()), "india": True, "remote": True, "abroad": True, "days": 14, "concurrency": 2,
            "include_senior": False, "exclude_skills": [], "max_years_asked": years + 4, "offices": "", "min_match": 40,
            "regions": ["remote", "city", "country"], "places": [],
            "defaults": {"market": "auto", "track": "auto", "variant": "flowcv", "max_pages": "auto", "engine": "claude:opus"}}


def start_job(kind: str, fn, *args) -> str:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "running", "log": [], "result": None, "error": None, "profile": profiles.current(),
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

    # The job belongs to the profile that started it, even if the page switches profile while it runs.
    threading.Thread(target=contextvars.copy_context().run, args=(target,), daemon=True).start()
    return jid


def _prefs(profile: dict) -> dict:
    from .profile import experience_months
    base = _default_prefs(profile)
    saved = profile.get("preferences") or {}
    # `defaults` merges key by key: a profile that saved only its writer must still get the design, length and market.
    prefs = base | saved | {"defaults": base["defaults"] | (saved.get("defaults") or {}),
                            "candidate_years": experience_months(profile) // 12}
    if prefs["defaults"].get("variant") not in LAYOUTS:
        prefs["defaults"]["variant"] = base["defaults"]["variant"]
    if prefs["defaults"].get("track") not in strategy.tracks() and prefs["defaults"].get("track") != "auto":
        prefs["defaults"]["track"] = "auto"
    prefs["regions"] = discover.regions(prefs)
    prefs["tracks"] = [t for t in prefs.get("tracks") or [] if t in strategy.tracks()] or base["tracks"]
    return prefs


def _app_dir(app_id: str):
    root = output_dir().resolve()
    path = (root / app_id).resolve()
    if root not in path.parents or not (path / "report.json").exists():
        raise HTTPException(404, "No such application")
    return path


def _key(x: str) -> str:
    """Running-work tables are keyed per profile: two people can tailor for the same company on the same day."""
    return f"{profiles.current()}/{x}"


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


def _profile_list() -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "status": p.get("status", "ready")} for p in profiles.all_profiles()]


def _setup_state(pid: str) -> dict:
    row = profiles.get(pid) or {}
    job = JOBS.get(ONBOARDING.get(pid, ""))
    return {"profiles": _profile_list(), "current": pid, "status": row.get("status", ""), "name": row.get("name", ""),
            "setup_job": job["id"] if job and job["status"] == "running" else None,
            "setup_error": job["error"] if job and job["status"] == "error" else None}


@app.get("/api/state")
def state():
    pid = profiles.current()
    row = profiles.get(pid)
    if not row or row.get("status", "ready") in ("setting_up", "reading") or not profile_path().exists():
        return {"setup": _setup_state(pid) | {"mode": "new"}, "engines": [{"id": e.id, "label": e.label, "available": e.available()} for e in ENGINES.values()]}
    profile = load_profile()
    if row.get("status") == "review":
        return {"setup": _setup_state(pid) | {"mode": "review"}, **_review(profile),
                "engines": [{"id": e.id, "label": e.label, "available": e.available()} for e in ENGINES.values()]}
    m = tailor.markets()
    home = strategy.home()
    zones = next((z for city, z in areas.catalogue().items() if city.lower() in home["city_names"]), [])
    return {
        "setup": None, "profiles": _profile_list(), "current": pid, "home": home, "strategy": strategy.summary(),
        "home_market": tailor.home_market(), "legacy": profiles.is_legacy(pid),
        "profile": {"basics": profile["basics"], "confirmed_skills": profile.get("confirmed_skills", []),
                    "experience_years": experience_years_label(profile), "preferences": _prefs(profile)},
        "markets": {k: {"label": v["label"], "max_pages": v["max_pages"], "gdpr": v.get("gdpr_clause", False),
                        "header_line": tailor.default_header_line(k, profile)} for k, v in m.items()},
        "layouts": {k: {"label": v["label"], "blurb": v["blurb"]} for k, v in LAYOUTS.items()},
        "tracks": {k: {"label": v["label"], "examples": v.get("headline_examples") or []} for k, v in tailor.tracks().items()},
        "voices": {k: {"label": v["label"], "best_for": v["best_for"]} for k, v in letters.voices().items() if k != "banned_phrases"},
        "engines": [{"id": e.id, "label": e.label, "available": e.available()} for e in ENGINES.values()],
        "statuses": tracker.STATUSES,
        "areas": {home["city"]: zones} if zones else {},
    }


def _review(profile: dict) -> dict:
    """What the review screen shows after a CV was read: the profile as read, the checks, the strategy."""
    cv = ""
    from .profile import original_cv_path
    if original_cv_path().exists():
        cv = original_cv_path().read_text("utf-8")
    return {"review": {"profile": {k: profile.get(k) for k in ("basics", "summary", "experience", "education", "certifications", "skills", "projects")},
                       "flags": onboarding.check_against_cv(profile, cv) if cv else [],
                       "strategy": strategy.summary(), "tracks": {k: {"label": v["label"], "brief": v.get("brief", ""), "titles": v.get("titles", [])}
                                                                    for k, v in strategy.tracks().items()}}}


# ---------- profiles: several people, each starting from a CV ----------

def _cookie(resp: JSONResponse, pid: str) -> JSONResponse:
    resp.set_cookie(profiles.COOKIE, pid, max_age=60 * 60 * 24 * 365, samesite="lax")
    return resp


@app.post("/api/profiles/switch")
def profile_switch(body: dict = Body(...)):
    if not profiles.get(body.get("id", "")):
        raise HTTPException(404, "No such profile")
    profiles.remember(body["id"])
    return _cookie(JSONResponse({"ok": True}), body["id"])


@app.post("/api/profiles/new")
def profile_new(body: dict = Body(default={})):
    """Start a profile: an empty one, ready for its CV."""
    row = profiles.create((body.get("name") or "New profile").strip())
    profiles.remember(row["id"])
    return _cookie(JSONResponse({"id": row["id"]}), row["id"])


@app.post("/api/profiles/{pid}/cv")
def profile_cv(pid: str, body: dict = Body(...)):
    """Read the CV (an uploaded file or pasted text) and build the profile and its strategy, as a background job."""
    row = profiles.get(pid)
    if not row or row.get("legacy"):
        raise HTTPException(404, "No such profile")
    job = JOBS.get(ONBOARDING.get(pid, ""))
    if job and job["status"] == "running":
        return {"job": job["id"]}
    try:
        text = onboarding.read_cv(body.get("filename", ""), body["data"]) if body.get("data") else (body.get("text") or "").strip()
    except ValueError as e:
        raise HTTPException(400, str(e))
    if len(text) < 300:
        raise HTTPException(400, "Upload your CV (PDF or Word), or paste its full text.")
    engine = body.get("engine") if body.get("engine") in ENGINES else "claude:opus"
    profiles.update(pid, status="setting_up")
    token = profiles.activate(pid)
    try:
        jid = start_job("onboarding", lambda t, n, w, e, progress: onboarding.run(pid, t, n, w, e, progress),
                        text, row["name"], body.get("wants") or "", engine)
    finally:
        profiles.reset(token)
    ONBOARDING[pid] = jid
    return {"job": jid}


@app.post("/api/profile/basics")
def profile_basics(body: dict = Body(...)):
    """Corrections from the review screen (and Settings): contact details and the summary."""
    profile = load_profile()
    for k in ("name", "headline", "email", "phone", "location", "linkedin", "nationality", "availability"):
        if k in body:
            profile["basics"][k] = str(body[k]).strip()
    if "summary" in body:
        profile["summary"] = str(body["summary"]).strip()
    save_profile(profile)
    pid = profiles.current()
    if profile["basics"]["name"] and not profiles.is_legacy(pid):
        profiles.update(pid, name=profile["basics"]["name"])
    return {"ok": True}


@app.post("/api/profile/bullet")
def profile_bullet(body: dict = Body(...)):
    """Fix, add or remove one bullet of a role (review screen). {role, id?, text} ; text '' removes."""
    profile = load_profile()
    role = next((r for r in profile["experience"] if r["id"] == body.get("role")), None)
    if not role:
        raise HTTPException(404, "No such role")
    text = str(body.get("text") or "").strip()
    row = next((b for b in role["bullets"] if b["id"] == body.get("id")), None)
    if row and not text:
        role["bullets"].remove(row)
    elif row:
        row["text"] = text
    elif text:
        n = 1 + max([int(b["id"].rsplit("-", 1)[-1]) for b in role["bullets"] if b["id"].rsplit("-", 1)[-1].isdigit()] or [0])
        role["bullets"].append({"id": f"{role['id']}-{n}", "text": text})
    save_profile(profile)
    return {"ok": True}


@app.post("/api/profiles/ready")
def profile_ready():
    """The person has checked what was read: the app opens."""
    profiles.update(profiles.current(), status="ready")
    return {"ok": True}


@app.delete("/api/profiles/{pid}")
def profile_delete(pid: str):
    try:
        profiles.delete(pid)
    except KeyError:
        raise HTTPException(404, "No such profile")
    except ValueError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    return _cookie(resp, profiles.last_used())


@app.post("/api/strategy/refresh")
def strategy_refresh(body: dict = Body(default={})):
    """Read the profile again and redo the strategy: after the CV changes, or the goal does."""
    engine = body.get("engine") if body.get("engine") in ENGINES else _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    wants = body.get("wants")
    if wants is None:
        wants = strategy.load().get("career_wants", "")

    def work(progress):
        onboarding.analyse(engine, wants, progress)
        progress("Setting up Discover with employers and searches for you")
        return {"seeded": onboarding.seed_sources(progress), "strategy": strategy.summary()}
    return {"job": start_job("strategy", work)}


@app.post("/api/strategy/company")
def strategy_company(body: dict = Body(...)):
    """Add one of the suggested employers to the favourites."""
    try:
        row = discover.add_favourite(body.get("name", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    s = strategy.load()
    for c in s.get("companies") or []:
        if c["name"].lower() == body.get("name", "").lower():
            c["added"] = True
    if not s.get("fallback"):
        strategy.save(s)
    return row


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
    body = {"min_match": _prefs(load_profile())["min_match"]} | body
    return {"job": start_job("tailor", lambda req, progress: tailor.run(req, progress), body)}


@app.post("/api/tailor/prompt")
def tailor_prompt(body: dict = Body(...)):
    """The tailoring brief as plain text for another AI chat, for when Claude's usage is used up."""
    try:
        return {"prompt": tailor.manual_prompt(body)}
    except EngineError as e:
        raise HTTPException(400, str(e))


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
    """Make one version the application's letter: by its id, or by its text (an edit)."""
    _app_dir(app_id)
    try:
        letters.keep_letter(app_id, body.get("text", ""), body.get("voice", ""), body.get("id", ""))
    except KeyError:
        raise HTTPException(404, "That version of the letter is gone")
    return letters.list_versions(app_id)


@app.get("/api/apps/{app_id}/letters")
def letter_versions(app_id: str):
    _app_dir(app_id)
    return letters.list_versions(app_id)


@app.post("/api/apps/{app_id}/letters/import")
def letter_import(app_id: str, body: dict = Body(...)):
    """Drafts this browser held before letters were kept on the server."""
    _app_dir(app_id)
    for d in body.get("drafts") or []:
        if (d.get("text") or "").strip():
            letters.add_version(app_id, {k: d.get(k) for k in ("voice", "label", "text", "flags")})
    return letters.list_versions(app_id)


@app.post("/api/apps/{app_id}/letters/remove")
def letter_remove(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    letters.remove_version(app_id, body.get("id", ""))
    return letters.list_versions(app_id)


@app.post("/api/apps/{app_id}/letter/export")
def letter_export(app_id: str, body: dict = Body(...)):
    _app_dir(app_id)
    fmt, text = body.get("fmt", "pdf"), (body.get("text") or "").strip()
    if fmt not in letterfile.FORMATS:
        raise HTTPException(400, "Pick pdf, docx or doc")
    if not text:
        raise HTTPException(400, "There's no letter to export")
    import tempfile
    from pathlib import Path
    report, profile = tailor.load_report(app_id), load_profile()
    name = f"{letterfile.file_stem(profile, report['company'], report['role'])}.{fmt}"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / name
        letterfile.build(fmt, text, profile["basics"], out)
        data = out.read_bytes()
    return Response(data, media_type=letterfile.FORMATS[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.post("/api/apps/{app_id}/track")
def app_track(app_id: str, body: dict = Body(...)):
    try:
        return tracker.update(app_id, body, body.get("source") or "app")
    except KeyError:
        raise HTTPException(404, "Not in the tracker")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps/{app_id}/history")
def app_history(app_id: str):
    """Every status change of one application, oldest first, from the local database."""
    from . import db
    return db.history(app_id)


@app.get("/api/export.xlsx")
def export():
    path = tracker.export_xlsx()
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/preview.pdf")
def preview(variant: str = "flowcv", max_pages: str = "auto", market: str = "", track: str = ""):
    pdf = in_thread(tailor.preview_base, {"variant": variant, "max_pages": max_pages, "market": market or tailor.home_market()})
    return FileResponse(pdf, media_type="application/pdf", headers={"Cache-Control": "no-store"})


# ---------- interview prep ----------
INTERVIEW_JOBS: dict[str, str] = {}   # profile/app id -> the prep job running for it


def _prep_job(app_id: str) -> dict | None:
    job = JOBS.get(INTERVIEW_JOBS.get(_key(app_id), ""))
    return job if job and job["status"] == "running" else None


@app.get("/api/interview")
def interview_list():
    from . import interview
    rows = []
    for r in tracker.load():
        folder = output_dir() / r["id"]
        if not (folder / "report.json").exists():
            continue
        d = interview.load(r["id"])
        rows.append({k: r.get(k, "") for k in ("id", "company", "role", "status", "logo", "created")}
                    | {"prepared_at": d["generated_at"] if d else "", "questions": sum(len(x["questions"]) for x in d["rounds"]) if d else 0,
                       "preparing": bool(_prep_job(r["id"]))})
    return {"apps": rows}


@app.get("/api/apps/{app_id}/interview")
def interview_get(app_id: str):
    from . import interview
    _app_dir(app_id)
    job = _prep_job(app_id)
    return {"prep": interview.load(app_id), "job": job["id"] if job else None}


@app.post("/api/apps/{app_id}/interview")
def interview_start(app_id: str, body: dict = Body(default={})):
    from . import interview
    _app_dir(app_id)
    if _prep_job(app_id):
        return {"job": INTERVIEW_JOBS[_key(app_id)]}
    engine = _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    jid = start_job("interview", lambda a, o, e, progress: interview.generate(a, o, e, progress), app_id, body, engine)
    INTERVIEW_JOBS[_key(app_id)] = jid
    return {"job": jid}


@app.post("/api/apps/{app_id}/interview/say")
def interview_say(app_id: str, body: dict = Body(...)):
    """Spoken answers for one round's talking questions."""
    from . import interview
    _app_dir(app_id)
    key = _key(f"{app_id}:say:{body.get('round', '')}")
    job = JOBS.get(INTERVIEW_JOBS.get(key, ""))
    if job and job["status"] == "running":
        return {"job": job["id"]}
    engine = _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    jid = start_job("interview-say", lambda a, r, e, progress: interview.write_say(a, r, e, progress), app_id, body.get("round", ""), engine)
    INTERVIEW_JOBS[key] = jid
    return {"job": jid}


CODE_JOBS: dict[str, str] = {}   # app id -> the coding-test job running for it


def _code_job(app_id: str) -> dict | None:
    job = JOBS.get(CODE_JOBS.get(_key(app_id), ""))
    return job if job and job["status"] == "running" else None


@app.get("/api/apps/{app_id}/codetest")
def codetest_get(app_id: str):
    from . import interview
    _app_dir(app_id)
    job = _code_job(app_id)
    return {"test": interview.load_codetest(app_id), "job": job["id"] if job else None}


@app.post("/api/apps/{app_id}/codetest")
def codetest_start(app_id: str, body: dict = Body(default={})):
    """Write the test, or with {"level": n} two more problems at that level."""
    from . import interview
    _app_dir(app_id)
    if _code_job(app_id):
        return {"job": CODE_JOBS[_key(app_id)]}
    engine = _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    level = int(body.get("level") or 0)
    if 1 <= level <= 5:
        jid = start_job("codetest-more", lambda a, l, e, progress: interview.more_problems(a, l, e, progress), app_id, level, engine)
    else:
        jid = start_job("codetest", lambda a, e, progress: interview.generate_codetest(a, e, progress), app_id, engine)
    CODE_JOBS[_key(app_id)] = jid
    return {"job": jid}


@app.get("/apps/{app_id}/interview.md")
def interview_md(app_id: str):
    p = _app_dir(app_id) / "interview.md"
    if not p.exists():
        raise HTTPException(404, "No interview notes yet")
    return FileResponse(p, media_type="text/markdown", filename=f"{app_id}_interview.md")


# ---------- contacts: who to write to about an application ----------
CONTACT_JOBS: dict[str, str] = {}   # app id -> the contact search running for it


@app.get("/api/contacts")
def contacts_all():
    from . import contacts
    mine = _key("")
    running = {a[len(mine):]: j for a, j in CONTACT_JOBS.items() if a.startswith(mine) and JOBS.get(j, {}).get("status") == "running"}
    return {"contacts": contacts.load_all(), "running": running}


@app.post("/api/apps/{app_id}/contacts")
def contacts_find(app_id: str):
    from . import contacts
    _app_dir(app_id)
    job = JOBS.get(CONTACT_JOBS.get(_key(app_id), ""))
    if job and job["status"] == "running":
        return {"job": job["id"]}
    jid = start_job("contacts", lambda a, progress: contacts.find(a, progress), app_id)
    CONTACT_JOBS[_key(app_id)] = jid
    return {"job": jid}


# ---------- cold mail ----------

@app.get("/api/coldmail")
def coldmail_list():
    from . import coldmail
    rows = []
    for r in tracker.load():
        if not (output_dir() / r["id"] / "report.json").exists():
            continue
        mails = coldmail.load(r["id"])["mails"]
        rows.append({k: r.get(k, "") for k in ("id", "company", "role", "status", "logo", "created")}
                    | {"mails": len(mails), "sent": sum(1 for x in mails if x.get("sent_at"))})
    from .letters import voices
    return {"apps": rows, "roles": coldmail.ROLES, "tones": {k: v["label"] for k, v in voices().items() if k in coldmail.TONES}}


# Emails to people you found yourself: the same calls, owned by "custom" instead of an application.
@app.get("/api/coldmail/custom")
def coldmail_custom_get():
    from . import coldmail
    return coldmail.load(coldmail.CUSTOM)


@app.post("/api/coldmail/custom")
def coldmail_custom_write(body: dict = Body(...)):
    from . import coldmail
    if not coldmail.EMAIL_RE.match(str(body.get("to_email") or "").strip()):
        raise HTTPException(400, "Give the recipient's email address.")
    if not str(body.get("company") or "").strip():
        raise HTTPException(400, "Give the company's name.")
    engine = _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    return {"job": start_job("coldmail", lambda b, e, progress: coldmail.write(coldmail.CUSTOM, b, e, progress), body, engine)}


@app.put("/api/coldmail/custom/{mail_id}")
def coldmail_custom_update(mail_id: str, body: dict = Body(...)):
    from . import coldmail
    try:
        return coldmail.update(coldmail.CUSTOM, mail_id, body)
    except KeyError:
        raise HTTPException(404, "No such email")


@app.delete("/api/coldmail/custom/{mail_id}")
def coldmail_custom_delete(mail_id: str):
    from . import coldmail
    coldmail.remove(coldmail.CUSTOM, mail_id)
    return {"ok": True}


@app.post("/api/coldmail/custom/{mail_id}/draft")
def coldmail_custom_draft(mail_id: str, body: dict = Body(default={})):
    from . import coldmail
    which = "follow_up" if body.get("which") == "follow_up" else "first"
    return {"job": start_job("coldmail-draft", lambda m, w, progress: coldmail.save_draft(coldmail.CUSTOM, m, w, progress), mail_id, which)}


@app.get("/api/apps/{app_id}/coldmail")
def coldmail_get(app_id: str):
    from . import coldmail
    folder = _app_dir(app_id)
    report = tailor.load_report(app_id)
    return coldmail.load(app_id) | {"pdf": report.get("pdf", ""), "has_research": (folder / "interview.json").exists()}


@app.post("/api/apps/{app_id}/coldmail")
def coldmail_write(app_id: str, body: dict = Body(...)):
    from . import coldmail
    _app_dir(app_id)
    if not coldmail.EMAIL_RE.match(str(body.get("to_email") or "").strip()):
        raise HTTPException(400, "Give the recipient's email address.")
    engine = _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    return {"job": start_job("coldmail", lambda a, b, e, progress: coldmail.write(a, b, e, progress), app_id, body, engine)}


@app.put("/api/apps/{app_id}/coldmail/{mail_id}")
def coldmail_update(app_id: str, mail_id: str, body: dict = Body(...)):
    from . import coldmail
    _app_dir(app_id)
    try:
        return coldmail.update(app_id, mail_id, body)
    except KeyError:
        raise HTTPException(404, "No such email")


@app.delete("/api/apps/{app_id}/coldmail/{mail_id}")
def coldmail_delete(app_id: str, mail_id: str):
    from . import coldmail
    _app_dir(app_id)
    coldmail.remove(app_id, mail_id)
    return {"ok": True}


@app.post("/api/apps/{app_id}/coldmail/{mail_id}/draft")
def coldmail_draft(app_id: str, mail_id: str, body: dict = Body(default={})):
    """Save to Gmail drafts. Never sends."""
    from . import coldmail
    _app_dir(app_id)
    which = "follow_up" if body.get("which") == "follow_up" else "first"
    return {"job": start_job("coldmail-draft", lambda a, m, w, progress: coldmail.save_draft(a, m, w, progress), app_id, mail_id, which)}


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


# ---------- memory: what the CV leaves out ----------

@app.get("/api/memory")
def memory_list():
    profile = load_profile()
    return {"entries": memory.entries(profile), "roles": memory.roles(profile)}


@app.post("/api/memory")
def memory_add(body: dict = Body(...)):
    """A note about your work -> tidy facts, kept against a role. Tidying uses the lighter Claude model."""
    engine = _prefs(load_profile())["defaults"].get("engine", "claude:opus")
    engine = "claude:sonnet" if engine.startswith("claude:") else engine
    text, role = (body.get("text") or "").strip(), body.get("role") or ""
    if len(text) < 10:
        raise HTTPException(400, "Write a sentence or two about what you did.")
    return {"job": start_job("memory", lambda progress: memory.add(role, text, engine, progress))}


@app.post("/api/memory/{entry_id}")
def memory_edit(entry_id: str, body: dict = Body(...)):
    try:
        return memory.edit(entry_id, body.get("facts") or [])
    except KeyError:
        raise HTTPException(404)


@app.delete("/api/memory/{entry_id}")
def memory_remove(entry_id: str):
    memory.remove(entry_id)
    return {"ok": True}


@app.post("/api/fit")
def fit_preview(body: dict = Body(...)):
    """A job's match before tailoring, in well under a second: no model call."""
    profile = load_profile()
    return tailor.fit(parse_jd(body.get("jd") or "")["text"], profile, _prefs(profile)["min_match"])


@app.post("/api/clip")
def clip_start(body: dict = Body(...)):
    """The page open in the browser extension, read into a job without fetching it again."""
    return {"job": start_job("clip", lambda c, progress: extract.from_clip(c, progress), body)}


@app.post("/api/apps/{app_id}/fill")
def fill_start(app_id: str, body: dict = Body(...)):
    """A scanned application form -> what to put in each field. The extension fills it; you submit."""
    _app_dir(app_id)
    return {"job": start_job("fill", lambda f, u, progress: apply.answer(app_id, f, u, progress),
                             body.get("fields") or [], body.get("url", ""))}


@app.get("/api/answers")
def answers_list():
    return apply.saved()


@app.post("/api/answers")
def answers_add(body: dict = Body(...)):
    """Your answer to a form question, kept for every later form."""
    return apply.remember(body.get("q", ""), body.get("a"))


@app.post("/api/answers/forget")
def answers_forget(body: dict = Body(...)):
    return apply.forget(body.get("q", ""))


@app.get("/api/apps/{app_id}/letter.pdf")
def letter_pdf(app_id: str):
    """The kept cover letter as a PDF, for the extension to attach to a form."""
    text = (tailor.load_report(app_id).get("cover_letter") or "").strip()
    return letter_export(app_id, {"fmt": "pdf", "text": text})


@app.get("/api/batch")
def batch_list():
    return batch.items()


@app.post("/api/batch")
def batch_add(body: dict = Body(...)):
    prefs = _prefs(load_profile())
    batch.set_concurrency(prefs.get("concurrency", 2))
    return {"ids": batch.add(body.get("items") or [], prefs["defaults"] | (body.get("defaults") or {}), prefs["min_match"])}


@app.post("/api/batch/{item_id}/retry")
def batch_retry(item_id: str, body: dict = Body(default={})):
    """Try a failed item again; force=true tailors it even below the match filter."""
    return {"ids": batch.retry(item_id, _prefs(load_profile())["min_match"], bool(body.get("force")))}


@app.delete("/api/batch/{item_id}")
def batch_remove(item_id: str):
    batch.remove(item_id)
    return {"ok": True}


# ---------- discovery ----------

@app.get("/api/sources")
def sources():
    return discover.load_sources()


@app.get("/api/boards")
def boards():
    on = {r["value"] for r in discover.load_sources() if r["kind"] == "board"}
    return [{"id": b, "label": label, "on": b in on} for b, label in discover.BOARDS.items()]


@app.post("/api/boards")
def boards_set(body: dict = Body(...)):
    """Switch the public remote and Europe job boards on or off."""
    discover.set_boards(body.get("on") or [])
    return boards()


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


@app.post("/api/favourites")
def favourite_add(body: dict = Body(...)):
    try:
        return discover.add_favourite(body.get("value", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/scan")
def scan_start(body: dict = Body(...)):
    prefs = _prefs(load_profile())
    days = int(body.get("days") or prefs["days"])
    if body.get("jobspy"):
        return {"job": start_job("scan", lambda d, p, progress: discover.scan_jobspy(d, p, progress), days, prefs)}
    ids = body.get("source_ids")
    if body.get("indeed"):
        ids = [s["id"] for s in discover.ensure_indeed_sources(prefs)]
    if body.get("favourites"):
        ids = [s["id"] for s in discover.load_sources() if s.get("favourite")]
        if not ids:
            raise HTTPException(400, "Add a favourite company in Settings first.")
    return {"job": start_job("scan", lambda ids, d, p, progress: discover.scan(ids, d, p, progress=progress),
                             ids, days, prefs)}


@app.get("/api/discover/prompt")
def discover_prompt(days: int = 14):
    """The prompt for Claude in Chrome, and the page to open it on."""
    prefs = _prefs(load_profile())
    return {"prompt": discover.chrome_prompt(prefs, days), "url": discover.chrome_start_url(prefs, days)}


@app.post("/api/discover/import")
def discover_import(body: dict = Body(...)):
    """Jobs pasted from Claude in Chrome: ranked and stored like a scan."""
    prefs = _prefs(load_profile())
    try:
        return in_thread(discover.import_jobs, body.get("text", ""), int(body.get("days") or prefs["days"]), prefs)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/found")
def found(days: int = 14):
    return discover.saved(days, _prefs(load_profile()))


@app.get("/api/scans")
def scan_history():
    return discover.scans()


@app.post("/api/found/{job_id}/dismiss")
def found_dismiss(job_id: str, body: dict = Body(default={})):
    discover.dismiss(job_id, (body or {}).get("reason", ""))
    return {"ok": True}


@app.post("/api/found/{job_id}/restore")
def found_restore(job_id: str):
    discover.restore(job_id)
    return {"ok": True}


@app.get("/api/taste")
def taste_get():
    return discover.taste_summary()


@app.post("/api/taste/rule")
def taste_rule(body: dict = Body(...)):
    discover.taste_rule(body.get("key", ""), bool(body.get("on")))
    return discover.taste_summary()


@app.get("/api/found/{job_id}/detail")
def found_detail(job_id: str):
    try:
        return in_thread(discover.job_detail, job_id, _prefs(load_profile()))
    except KeyError:
        raise HTTPException(404)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
