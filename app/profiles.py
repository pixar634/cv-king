"""Several people on one install. Each profile has its own CV, strategy, sources, applications and career map.

The first profile is whoever set the app up by hand: it keeps living in data/ and output/ exactly as before.
Profiles made from the UI live in profiles/<id>/data and profiles/<id>/output. Which profile a request is for
comes from a cookie; background work carries the profile it started under (a context variable copied into
its thread), so switching profiles mid-run never mixes two people's files.
"""
import contextvars
import datetime as dt
import json
import re
import shutil
import threading
from pathlib import Path

from .config import DATA, OUTPUT, ROOT

PROFILES = ROOT / "profiles"
INDEX = PROFILES / "index.json"
COOKIE = "cv_profile"
LEGACY_ID = "me"

_active: contextvars.ContextVar[str | None] = contextvars.ContextVar("profile", default=None)
_lock = threading.Lock()


def _read_index() -> dict:
    if INDEX.exists():
        return json.loads(INDEX.read_text("utf-8"))
    rows = []
    if (DATA / "base_cv.json").exists():
        try:
            name = json.loads((DATA / "base_cv.json").read_text("utf-8"))["basics"]["name"]
        except (KeyError, ValueError):
            name = "Me"
        rows.append({"id": LEGACY_ID, "name": name, "legacy": True, "status": "ready", "created": ""})
    return {"profiles": rows, "last": rows[0]["id"] if rows else ""}


def _write_index(idx: dict) -> None:
    PROFILES.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False), "utf-8")


def all_profiles() -> list[dict]:
    return _read_index()["profiles"]


def get(pid: str) -> dict | None:
    return next((p for p in all_profiles() if p["id"] == pid), None)


def last_used() -> str:
    """Where a browser with no profile cookie lands: the first profile, never someone else's last session."""
    ids = [p["id"] for p in _read_index()["profiles"]]
    return ids[0] if ids else ""


def current() -> str:
    """The profile this request or background job belongs to; the last one used when nothing says."""
    pid = _active.get()
    return pid if pid and get(pid) else last_used()


def activate(pid: str | None) -> contextvars.Token:
    return _active.set(pid)


def reset(token: contextvars.Token) -> None:
    _active.reset(token)


def run_as(pid: str, fn, *args):
    """Call fn with `pid` as the current profile (for threads that didn't inherit a context)."""
    token = _active.set(pid)
    try:
        return fn(*args)
    finally:
        _active.reset(token)


def remember(pid: str) -> None:
    with _lock:
        idx = _read_index()
        idx["last"] = pid
        _write_index(idx)


def data_dir(pid: str | None = None) -> Path:
    pid = pid or current()
    p = get(pid) if pid else None
    if not p or p.get("legacy"):
        return DATA
    return PROFILES / pid / "data"


def output_dir(pid: str | None = None) -> Path:
    pid = pid or current()
    p = get(pid) if pid else None
    if not p or p.get("legacy"):
        return OUTPUT
    return PROFILES / pid / "output"


def is_legacy(pid: str | None = None) -> bool:
    p = get(pid or current())
    return bool(p and p.get("legacy"))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:24] or "profile"


def create(name: str) -> dict:
    """A new, empty profile (the onboarding job fills it)."""
    with _lock:
        idx = _read_index()
        ids = {p["id"] for p in idx["profiles"]}
        base, n = _slug(name), 2
        pid = base
        while pid in ids or pid == LEGACY_ID:
            pid, n = f"{base}-{n}", n + 1
        row = {"id": pid, "name": name.strip() or "New profile", "legacy": False, "status": "setting_up",
               "created": dt.datetime.now().isoformat(timespec="seconds")}
        idx["profiles"].append(row)
        _write_index(idx)
    (PROFILES / pid / "data").mkdir(parents=True, exist_ok=True)
    (PROFILES / pid / "output").mkdir(parents=True, exist_ok=True)
    return row


def update(pid: str, **fields) -> dict:
    with _lock:
        idx = _read_index()
        for p in idx["profiles"]:
            if p["id"] == pid:
                p.update({k: v for k, v in fields.items() if k in ("name", "status")})
                _write_index(idx)
                return p
    raise KeyError(pid)


def delete(pid: str) -> None:
    """Remove a profile made from the UI, with all its files. The hand-made first profile can't be deleted here."""
    p = get(pid)
    if not p:
        raise KeyError(pid)
    if p.get("legacy"):
        raise ValueError("This profile lives in data/ and output/; it can't be deleted from the app.")
    with _lock:
        idx = _read_index()
        idx["profiles"] = [x for x in idx["profiles"] if x["id"] != pid]
        if idx.get("last") == pid:
            idx["last"] = idx["profiles"][0]["id"] if idx["profiles"] else ""
        _write_index(idx)
    folder = (PROFILES / pid).resolve()
    if PROFILES.resolve() in folder.parents:
        shutil.rmtree(folder, ignore_errors=True)
