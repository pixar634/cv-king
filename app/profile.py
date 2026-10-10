"""The master profile: the only source a tailored CV may draw facts from."""
import datetime as dt
import json

from .config import BASE_CV


def load_profile() -> dict:
    return json.loads(BASE_CV.read_text("utf-8"))


def save_profile(profile: dict) -> None:
    BASE_CV.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", "utf-8")


def fmt_month(ym: str | None) -> str:
    if not ym:
        return "Present"
    year, month = ym.split("-")
    return f"{month}/{year}"


def _months(start: str, end: str | None, today: dt.date) -> set[tuple[int, int]]:
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-")) if end else (today.year, today.month)
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


def bullet_index(profile: dict) -> dict[str, tuple[str, str]]:
    """bullet id -> (role id, text)"""
    return {b["id"]: (r["id"], b["text"]) for r in profile["experience"] for b in r["bullets"]}


def profile_text(profile: dict) -> str:
    """Every fact in the profile as one text: the evidence base for the guards."""
    b = profile["basics"]
    parts = [b["name"], b["headline"], b["location"], b["phone"], b["email"], b["linkedin"],
             profile.get("summary", ""), profile.get("interests", "")]
    for r in profile["experience"]:
        parts += [r["title"], r["company"], r.get("client", ""), r.get("context", ""), r["location"],
                  fmt_month(r["start"]), fmt_month(r.get("end")), r["start"], r.get("end") or "",
                  ", ".join(r.get("stack", []))]
        parts += [x["text"] for x in r["bullets"]]
    for p in profile.get("projects", []):
        parts += [p["name"], p["role"], p.get("url", ""), ", ".join(p.get("stack", [])), fmt_month(p["start"]),
                  *(b["text"] for b in p["bullets"])]
    for e in profile["education"]:
        parts += [e["degree"], e["institution"], e["location"], e["year"], *e.get("details", [])]
    for c in profile["certifications"]:
        parts += [c["name"], c["issuer"], c.get("detail", "")]
    parts += [l["name"] + " " + l.get("level", "") for l in profile["languages"]]
    parts += all_skills(profile)
    return "\n".join(p for p in parts if p)


def projects_for(profile: dict, track: str) -> list[dict]:
    return [p for p in profile.get("projects", []) if track in p.get("tracks", [])]


def brief_view(profile: dict, track: str = "") -> dict:
    """The slice of the profile the model sees: facts only, no contact details."""
    return {
        "projects (printed exactly as written; cite them in the summary or skills where they fit the JD)": [
            {k: p[k] for k in ("name", "role", "stack")} | {"dates": f"{fmt_month(p['start'])} - {fmt_month(p.get('end'))}",
                                                           "bullets": [b["text"] for b in p["bullets"]]}
            for p in projects_for(profile, track)],
        "summary": profile["summary"],
        "experience": [
            {k: r[k] for k in ("id", "title", "company", "client", "context", "location", "stack",
                                "internship", "optional") if k in r}
            | {"dates": f"{fmt_month(r['start'])} - {fmt_month(r.get('end'))}",
               "bullets": r["bullets"]}
            for r in profile["experience"]
        ],
        "education": profile["education"],
        "certifications": profile["certifications"],
        "languages": profile["languages"],
        "skills": profile["skills"],
        "confirmed_skills": profile.get("confirmed_skills", []),
    }
