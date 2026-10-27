"""Tailor many jobs at once. Each item runs the full pipeline on its own thread; a semaphore caps how
many model calls run together (subscription limits), and the state is on disk so a reload, or a
restart, shows what happened. Each profile has its own queue; the semaphore is shared, because the model
calls all go through one subscription."""
import contextvars
import datetime as dt
import json
import re
import threading
import uuid

from . import discover, extract, tailor
from .config import output_dir
from .profiles import current

_lock = threading.Lock()
_queues: dict[str, dict[str, dict]] = {}   # profile id -> item id -> item
_slots = {"n": 2, "sem": threading.Semaphore(2)}


def _qfile():
    return output_dir() / "_queue.json"


def _mine() -> dict[str, dict]:
    return _queues.setdefault(current(), {})


def _save():
    f = _qfile()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(list(_mine().values()), indent=1, ensure_ascii=False), "utf-8")


def _load():
    items, f = _mine(), _qfile()
    if f.exists() and not items:
        for it in json.loads(f.read_text("utf-8")):
            if it["status"] in ("queued", "running"):  # the process that ran them is gone
                it["status"], it["stage"] = "error", "Interrupted by a restart. Retry it."
            items[it["id"]] = it


def set_concurrency(n: int) -> None:
    n = max(1, min(4, int(n)))
    if n != _slots["n"]:
        _slots["n"], _slots["sem"] = n, threading.Semaphore(n)


def items() -> list[dict]:
    with _lock:
        _load()
        return sorted(_mine().values(), key=lambda i: i["created"], reverse=True)


def _update(item_id: str, **kw):
    with _lock:
        _mine()[item_id].update(kw)
        _save()


def _work(item_id: str, req: dict, source: dict):
    sem = _slots["sem"]
    with sem:
        _update(item_id, status="running", stage="Starting")
        progress = lambda m: _update(item_id, stage=m)
        try:
            if source.get("found_id"):
                progress("Reading the posting")
                j = discover.job_detail(source["found_id"])
                req |= {"jd": j.get("description", ""), "company": req.get("company") or j.get("company", ""),
                        "role": req.get("role") or j.get("title", ""), "job_url": j.get("url", ""),
                        "job_id": extract._job_id_from_url(j.get("url", "")),
                        "location": j.get("location", ""), "posted": j.get("posted", ""),
                        "discover_match": j.get("match")}
                if req.get("market") in (None, "", "auto"):
                    req["market"] = extract.market_for(j.get("location", ""), j.get("remote"))
            elif source.get("url"):
                got = extract.extract(source["url"], progress)
                req |= {"jd": got["description"], "company": req.get("company") or got["company"],
                        "role": req.get("role") or got["role"], "job_url": got["url"], "job_id": got["job_id"],
                        "location": got["location"], "posted": got["posted"]}
                if req.get("market") in (None, "", "auto"):
                    req["market"] = got["market"]
            if req.get("market") in (None, "", "auto"):
                req["market"] = tailor.home_market()
            _update(item_id, company=req.get("company") or "", role=req.get("role") or "", market=req["market"])
            report = tailor.run(req, progress)
            _update(item_id, status="done", stage="Tailored", app_id=report["id"], company=report["company"],
                    role=report["role"], score=report["scores"]["after"], ceiling=report["scores"]["ceiling"])
        except tailor.BelowMatch as e:   # stopped before writing; the card offers "Tailor anyway"
            _update(item_id, status="error", stage=str(e), below_min=True, ceiling=e.reach)
        except Exception as e:  # one failed job must not stop the others
            _update(item_id, status="error", stage=str(e)[:400])


def _keys(e: dict) -> set[str]:
    """What identifies the job an entry asks for: its Discover id, its link, or its company and role."""
    norm = lambda x: " ".join(re.findall(r"[a-z0-9]+", (x or "").lower()))
    keys = {f"found:{e['found_id']}"} if e.get("found_id") else set()
    for u in (e.get("url"), e.get("job_url")):
        if u:
            keys.add("url:" + norm(u.split("?")[0].split("#")[0]))
    if norm(e.get("company")) and norm(e.get("role")):
        keys.add(f"job:{norm(e['company'])}|{norm(e['role'])}")
    return keys


def _running(keys: set[str]) -> str | None:
    """The queued or running item for the same job, if there is one: pressing Tailor twice must not write twice."""
    for it in _mine().values():
        # the entry as sent, plus the company and role read from the posting since
        seen = _keys(it.get("entry") or {}) | _keys({"company": it.get("company"), "role": it.get("role")})
        if it["status"] in ("queued", "running") and keys & seen:
            return it["id"]
    return None


def add(entries: list[dict], defaults: dict, min_match: int = 0) -> list[str]:
    """entries: each {found_id} or {url} or {jd, company?, role?}, plus force to skip the match filter;
    defaults: market/track/variant/pages/engine; min_match: the Discover match filter, checked before writing."""
    ids = []
    with _lock:
        _load()
    for e in entries:
        item_id = uuid.uuid4().hex[:10]
        req = {k: v for k, v in defaults.items() if v not in (None, "")} | {k: v for k, v in e.items() if k in (
            "jd", "company", "role", "job_url", "company_site", "market", "track", "variant", "max_pages", "engine",
            "job_id", "location", "posted", "replace_id", "notes", "force")} | {"min_match": min_match}
        source = {k: e[k] for k in ("found_id", "url") if e.get(k)}
        if source.get("found_id"):
            discover.keep(source["found_id"])   # Discover learns from what you act on, too
        with _lock:
            same = _running(_keys(e))   # checked and claimed under one lock, so two quick clicks can't both pass
            if not same:
                _mine()[item_id] = {"id": item_id, "created": dt.datetime.now().isoformat(timespec="seconds"),
                                    "entry": e, "defaults": defaults,
                                    "status": "queued", "stage": "Waiting for a free slot", "company": e.get("company", ""),
                                    "role": e.get("role", ""), "market": req.get("market", ""), "app_id": None,
                                    "source": source | ({"text": True} if e.get("jd") else {})}
                _save()
        if same:
            ids.append(same)
            continue
        ctx = contextvars.copy_context()   # the item belongs to the profile that queued it
        threading.Thread(target=ctx.run, args=(_work, item_id, req, source), daemon=True).start()
        ids.append(item_id)
    return ids


def retry(item_id: str, min_match: int = 0, force: bool = False) -> list[str]:
    with _lock:
        _load()
        it = _mine().pop(item_id, None)
        _save()
    if not it or not it.get("entry"):
        return []
    return add([it["entry"] | ({"force": True} if force else {})], it.get("defaults") or {}, min_match)


def remove(item_id: str) -> None:
    with _lock:
        _load()
        it = _mine().get(item_id)
        if it and it["status"] not in ("queued", "running"):
            _mine().pop(item_id)
            _save()
