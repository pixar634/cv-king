"""The study list: skills, certifications and projects to take up later. Most come from a tailored CV's gaps (the
posting asks for LangGraph, the profile doesn't show it); you can add your own too.

It lives only in the local database (`study_items`, and `study_asks` for the applications that asked), like the
status history: no JSON file, and `db.sync()` never touches it. Nothing here reaches a CV by itself: a finished item
goes into the Career growth log (and, if you say so, your confirmed skills), or into your memory."""
import datetime as dt
from contextlib import closing

from . import career, db, insights, tracker

KINDS = {"skill": "Skill", "certification": "Certification", "project": "Project"}
STATUSES = {"learning": "Learning", "todo": "To study", "done": "Done"}   # in the list's order
LOG_KINDS = {"skill": "skill", "certification": "cert", "project": "did"}   # study kind -> growth-log kind


def _clean(term) -> str:
    return " ".join(str(term or "").split())[:120]


def _key(term: str) -> str:
    return _clean(term).lower()


def items() -> list[dict]:
    """Every item with the applications that asked for it: what you're learning first, then the most asked for."""
    with closing(db.connect()) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM study_items ORDER BY added DESC, id DESC")]
        asks = con.execute("SELECT * FROM study_asks ORDER BY at DESC").fetchall()
    by_item: dict[int, list[dict]] = {}
    for a in asks:
        by_item.setdefault(a["item_id"], []).append({"app_id": a["app_id"], "company": a["company"], "role": a["role"],
                                                     "must_have": bool(a["must_have"]), "note": a["note"], "at": a["at"]})
    order, logged = list(STATUSES), {e["id"] for e in career.log()}
    for r in rows:
        r["asks"] = by_item.get(r["id"], [])
        r["log_id"] = r["log_id"] if r["log_id"] in logged else None   # removed from the growth log since
    return sorted(rows, key=lambda r: (order.index(r["status"]) if r["status"] in order else len(order), -len(r["asks"])))


def add(terms: list, app_id: str = "", kind: str = "skill", asked: bool = False) -> list[dict]:
    """Put terms on the list: gap dicts ({term, must_have, note}) or plain names. A term already there keeps its
    status and gets this application as one more that asked for it. `asked` (a suggestion taken up) links every
    tailored job that required the term while the profile lacked it."""
    app = next((r for r in tracker.load() if r["id"] == app_id), None) if app_id else None
    if app_id and not app:
        raise KeyError(app_id)
    gaps = [t if isinstance(t, dict) else {"term": t} for t in terms or []]
    gaps = [g for g in gaps if _clean(g.get("term"))]
    if not gaps:
        raise ValueError("Name a skill, certification or project to study.")
    now = db._now()
    with closing(db.connect()) as con, con:
        for g in gaps:
            term = _clean(g["term"])
            con.execute("INSERT OR IGNORE INTO study_items (term, key, kind, added, updated) VALUES (?, ?, ?, ?, ?)",
                        (term, _key(term), kind if kind in KINDS else "skill", now, now))
            item_id = con.execute("SELECT id FROM study_items WHERE key = ?", (_key(term),)).fetchone()[0]
            if app:
                con.execute("INSERT OR REPLACE INTO study_asks VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (item_id, app["id"], app.get("company") or "", app.get("role") or "",
                             int(bool(g.get("must_have"))), str(g.get("note") or "")[:300], now))
            elif asked:
                con.executemany("INSERT OR IGNORE INTO study_asks VALUES (?, ?, ?, ?, 1, '', ?)", [
                    (item_id, r["app_id"], r["company"] or "", r["role"] or "", now) for r in con.execute(
                        "SELECT DISTINCT k.app_id, a.company, a.role FROM keywords k JOIN applications a ON a.id = k.app_id "
                        "WHERE k.term = ? AND k.in_profile = 0 AND k.tier = 'required'", (term,))])
    return items()


def suggestions(limit: int = 12) -> list[dict]:
    """Skills the tailored jobs keep requiring that the profile lacks and the list doesn't hold yet: [{term, jobs}]."""
    with closing(db.connect()) as con:
        have = {r[0] for r in con.execute("SELECT key FROM study_items")}
        return [g for g in insights.most_missed(con) if _key(g["term"]) not in have][:limit]


def log(item_id: int, use_on_cv: bool = False) -> dict:
    """A finished item into the Career growth log, which the career map reads. A skill or certification marked for
    CVs also joins the profile's confirmed skills (career.add_log), so tailoring may claim it."""
    item = next((i for i in items() if i["id"] == item_id), None)
    if not item:
        raise KeyError(item_id)
    if item["status"] != "done":
        raise ValueError("Mark it done first.")
    if item["log_id"]:
        raise ValueError("It's in your growth log already.")
    text = item["term"] if item["kind"] != "project" else f"Built a project: {item['term']}"
    entry = career.add_log(LOG_KINDS[item["kind"]], text, item["done_on"] or "", use_on_cv)
    with closing(db.connect()) as con, con:
        con.execute("UPDATE study_items SET log_id = ?, updated = ? WHERE id = ?", (entry["id"], db._now(), item_id))
    return next(i for i in items() if i["id"] == item_id)


def for_brief() -> str:
    """The list as lines for the career map's brief."""
    lines = []
    for i in items():
        bits = [KINDS[i["kind"]].lower(), STATUSES[i["status"]].lower()]
        if i["asks"]:
            bits.append(f"required by {sum(a['must_have'] for a in i['asks'])} of {len(i['asks'])} tailored jobs that named it")
        if i["target_on"]:
            bits.append(f"aiming for {i['target_on']}")
        lines.append(f"- {i['term']} [{'; '.join(bits)}]" + (f" plan: {i['notes'][:200]}" if i["notes"] else ""))
    return "\n".join(lines[:40])


def update(item_id: int, fields: dict) -> dict:
    """Change an item's kind, status, plan or target date. Done stamps today; moving it back clears that."""
    new = {k: fields[k] for k in ("kind", "status", "notes", "target_on") if k in fields}
    if "kind" in new and new["kind"] not in KINDS:
        raise ValueError(f"Kind is one of {', '.join(KINDS)}")
    if "status" in new and new["status"] not in STATUSES:
        raise ValueError(f"Status is one of {', '.join(STATUSES)}")
    if "notes" in new:
        new["notes"] = str(new["notes"] or "")[:2000]
    if "target_on" in new:
        new["target_on"] = str(new["target_on"] or "")[:10] or None
        if new["target_on"]:
            try:
                dt.date.fromisoformat(new["target_on"])
            except ValueError:
                raise ValueError("The target date is YYYY-MM-DD")
    with closing(db.connect()) as con, con:
        row = con.execute("SELECT status FROM study_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        if "status" in new and new["status"] != row["status"]:
            new["done_on"] = dt.date.today().isoformat() if new["status"] == "done" else None
        new["updated"] = db._now()
        con.execute(f"UPDATE study_items SET {', '.join(f'{k} = ?' for k in new)} WHERE id = ?", (*new.values(), item_id))
    return next(i for i in items() if i["id"] == item_id)


def remove(item_id: int) -> None:
    with closing(db.connect()) as con, con:
        con.execute("DELETE FROM study_asks WHERE item_id = ?", (item_id,))
        con.execute("DELETE FROM study_items WHERE id = ?", (item_id,))
