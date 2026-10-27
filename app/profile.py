"""The master profile: the only source a tailored CV may draw facts from."""
import datetime as dt
import json

from .config import data_dir


def profile_path():
    return data_dir() / "base_cv.json"


def original_cv_path():
    """The CV as the candidate had it before any tailoring: the 'before' of every score."""
    return data_dir() / "original_cv.txt"


def load_profile() -> dict:
    return json.loads(profile_path().read_text("utf-8"))


def save_profile(profile: dict) -> None:
    profile_path().write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", "utf-8")


def fmt_month(ym: str | None) -> str:
    """'2021-04' -> '04/2021'; a CV that only gave the year ('2021') keeps just the year."""
    if not ym:
        return "Present"
    if "-" not in str(ym):
        return str(ym)
    year, month = str(ym).split("-")[:2]
    return f"{month}/{year}"


def _ym(value: str, end: bool) -> tuple[int, int]:
    parts = str(value).split("-")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else (12 if end else 1)


def _months(start: str, end: str | None, today: dt.date) -> set[tuple[int, int]]:
    sy, sm = _ym(start, False)
    ey, em = min(_ym(end, True), (today.year, today.month)) if end else (today.year, today.month)
    out, y, m = set(), sy, sm
    while (y, m) <= (ey, em):
        out.add((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def experience_months(profile: dict, today: dt.date | None = None) -> int:
    """Months of paid employment, internships excluded, overlaps counted once."""
    today = today or dt.date.today()
    covered: set[tuple[int, int]] = set()
    for role in profile["experience"]:
        if not role.get("internship"):
            covered |= _months(role["start"], role.get("end"), today)
    return len(covered)


def experience_years_label(profile: dict) -> str:
    months = experience_months(profile)
    years, rest = divmod(months, 12)
    return f"{years}+" if rest else str(years)


def all_skills(profile: dict) -> list[str]:
    items = [s for group in profile["skills"].values() for s in group]
    return items + list(profile.get("confirmed_skills", []))


def memory_notes(profile: dict, role: str) -> list[dict]:
    """Facts from your memory (see memory.py) for one role ("" = general), shaped like bullets: a tailored bullet
    may cite one as its source the way it cites a profile bullet."""
    return [{"id": f"mem-{m['id']}-{i}", "text": f} for m in profile.get("memory", []) if m.get("role", "") == role
            for i, f in enumerate(m.get("facts", []))]


def bullet_index(profile: dict) -> dict[str, tuple[str, str]]:
    """bullet id -> (role id, text); memory facts for a role count as that role's sources too"""
    return {b["id"]: (r["id"], b["text"]) for r in profile["experience"] for b in r["bullets"] + memory_notes(profile, r["id"])}


def profile_text(profile: dict) -> str:
    """Every fact in the profile as one text: the evidence base for the guards."""
    b = profile["basics"]
    parts = [b.get("name"), b.get("headline"), b.get("location"), b.get("phone"), b.get("email"), b.get("linkedin"),
             profile.get("summary", ""), profile.get("interests", "")]
    for r in profile["experience"]:
        parts += [r["title"], r["company"], r.get("client", ""), r.get("context", ""), r.get("location", ""),
                  fmt_month(r["start"]), fmt_month(r.get("end")), r["start"], r.get("end") or "",
                  ", ".join(r.get("stack", []))]
        parts += [x["text"] for x in r["bullets"]]
    for p in profile.get("projects", []):
        parts += [p["name"], p.get("role", ""), p.get("url", ""), ", ".join(p.get("stack", [])), fmt_month(p.get("start")),
                  *(b["text"] for b in p["bullets"])]
    for e in profile["education"]:
        parts += [e["degree"], e["institution"], e.get("location", ""), e.get("year", ""), *e.get("details", [])]
    for c in profile.get("certifications", []):
        parts += [c["name"], c.get("issuer", ""), c.get("detail", "")]
    parts += [l["name"] + " " + l.get("level", "") for l in profile.get("languages", [])]
    parts += all_skills(profile) + list(profile.get("skills", {}))   # group labels are the candidate's own words too
    for m in profile.get("memory", []):   # your own notes about your work, and the facts drawn from them
        parts += [m.get("text", ""), *m.get("facts", [])]
    return "\n".join(p for p in parts if p)


def projects_for(profile: dict, track: str) -> list[dict]:
    """Projects shown for a track: those tagged with it, and untagged ones everywhere."""
    return [p for p in profile.get("projects", []) if not p.get("tracks") or track in p["tracks"]]


def brief_view(profile: dict, track: str = "") -> dict:
    """The slice of the profile the model sees: facts only, no contact details."""
    return {
        "projects (printed exactly as written; cite them in the summary or skills where they fit the JD)": [
            {k: p.get(k, "") for k in ("name", "role", "stack")} | {"dates": f"{fmt_month(p.get('start'))} - {fmt_month(p.get('end'))}",
                                                           "bullets": [b["text"] for b in p["bullets"]]}
            for p in projects_for(profile, track)],
        "summary": profile["summary"],
        "experience": [
            {k: r[k] for k in ("id", "title", "company", "client", "context", "location", "stack",
                                "internship", "optional") if k in r}
            | {"dates": f"{fmt_month(r['start'])} - {fmt_month(r.get('end'))}",
               "bullets": r["bullets"]}
            | ({"notes (the candidate's own words about this role: true, and a valid src for its bullets)": notes}
               if (notes := memory_notes(profile, r["id"])) else {})
            for r in profile["experience"]
        ],
        "notes (the candidate's own words, not tied to one role: true, for the summary and skills)":
            [n["text"] for n in memory_notes(profile, "")],
        "education": profile["education"],
        "certifications": profile.get("certifications", []),
        "languages": profile.get("languages", []),
        "skills": profile["skills"],
        "confirmed_skills": profile.get("confirmed_skills", []),
    }
