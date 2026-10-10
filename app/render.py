"""HTML -> PDF through the installed Chrome (no browser download), fitted to the page limit."""
import copy
import re
import threading
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from pypdf import PdfReader, PdfWriter

from .config import FONTS, TEMPLATES
from .layouts import DENSITY, LAYOUTS
from .profile import fmt_month

PREVIEW_FONTS = "/static/fonts/"
PRINT_FONTS = FONTS.as_uri() + "/"


def _md(text: str) -> Markup:
    """Escape, then allow **bold** only."""
    safe = str(escape(text or ""))
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe))


_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
_env.filters["md"] = _md


def build_doc(profile: dict, plan: dict, options: dict) -> dict:
    b = profile["basics"]
    roles = {r["id"]: r for r in profile["experience"]}
    experience = []
    for rid, bullets in plan["experience"].items():
        r = roles[rid]
        experience.append({
            "id": rid, "title": r["title"], "company": r["company"], "client": r.get("client", ""),
            "dates": f"{fmt_month(r['start'])} – {fmt_month(r.get('end'))}", "location": r["location"],
            "internship": bool(r.get("internship")), "bullets": list(bullets),
        })
    education = [
        {"year": e["year"], "location": e["location"], "degree": e["degree"],
         "institution": e["institution"], "details": list(e.get("details", []))}
        for e in profile["education"] if not e.get("optional") or options.get("include_school")
    ]
    market_id = options["market"]
    languages = [l for l in profile["languages"] if not l.get("markets") or market_id in l["markets"]]
    from .profile import projects_for
    projects = [{"name": p["name"], "role": p["role"], "url": p.get("url", ""),
                 "dates": f"{fmt_month(p['start'])} – {fmt_month(p.get('end'))}",
                 "bullets": [b["text"] for b in p["bullets"]]}
                for p in projects_for(profile, options.get("track", ""))]
    variant = options.get("variant") if options.get("variant") in LAYOUTS else "flowcv"
    one_page = int(options.get("max_pages") or 2) == 1
    layout = LAYOUTS[variant]
    density = "compact" if one_page else "normal"
    return {
        "name": b["name"], "headline": plan["headline"], "email": b["email"], "phone": b["phone"],
        "location": b["location"], "linkedin": b["linkedin"],
        "header_line": (options.get("header_line") or "").strip(),
        "summary": plan["summary"], "skills": copy.deepcopy(plan["skills"]), "experience": experience,
        "projects": projects,
        "education": education, "certifications": profile["certifications"], "languages": languages,
        "interests": profile.get("interests") if options.get("include_interests", True) and not one_page else None,
        "gdpr": bool(options.get("gdpr")),
        "variant": variant, "header_style": layout["header"], "density": density,
        "geom": dict(DENSITY[density]),
        "font_pt": layout["font_pt"] - (0.4 if one_page else 0),
        "min_pt": layout["min_pt"] - (0.4 if one_page else 0),
        "meta_title": f"{b['name']} - {plan['headline']} - CV",
    }


def render_html(doc: dict, font_base: str = PREVIEW_FONTS) -> str:
    return _env.get_template("cv.html.j2").render(doc=doc, font_base=font_base)


def for_print(html: str) -> str:
    return html.replace(f'url("{PREVIEW_FONTS}', f'url("{PRINT_FONTS}')


def _floors(max_pages: int) -> dict:
    """Fewest bullets a role keeps while fitting. The newest three roles carry the story."""
    return {"internship": 0, "old": 0, "recent": 2} if max_pages == 1 else {"internship": 0, "old": 2, "recent": 3}


def _trim_once(doc: dict, max_pages: int) -> str | None:
    floors = _floors(max_pages)
    if doc["interests"]:
        doc["interests"] = None
        return "dropped Interests"
    exp = doc["experience"]
    for i in range(len(exp) - 1, -1, -1):
        if exp[i]["internship"]:
            r = exp.pop(i)
            return f"dropped {r['title']}, {r['company']}"
    for e in doc["education"]:
        if e["details"]:
            e["details"] = []
            return "dropped education details"
    for p in doc.get("projects", []):
        if len(p["bullets"]) > 2:
            p["bullets"].pop()
            return f"dropped a bullet from {p['name']}"
    # Older roles (4th onward) give way first. Then the recent three are kept balanced: cut from
    # whichever has the most bullets, so one role never keeps five while the strongest story has two.
    floor_of = lambda i: floors["recent"] if i < 3 else floors["old"]
    for i in range(len(exp) - 1, 2, -1):
        if len(exp[i]["bullets"]) > floor_of(i):
            exp[i]["bullets"].pop()
            return f"dropped a bullet from {exp[i]['title']}, {exp[i]['company']}"
    over = [i for i in range(min(3, len(exp))) if len(exp[i]["bullets"]) > floor_of(i)]
    if over:
        i = max(over, key=lambda i: (len(exp[i]["bullets"]), i))
        exp[i]["bullets"].pop()
        return f"dropped a bullet from {exp[i]['title']}, {exp[i]['company']}"
    if doc["font_pt"] > doc["min_pt"]:
        doc["font_pt"] = round(max(doc["min_pt"], doc["font_pt"] - 0.3), 2)
        return f"reduced body size to {doc['font_pt']}pt"
    if len(doc["skills"]) > 4:
        doc["skills"].pop()
        return "dropped the last skills group"
    return None


class Renderer:
    """One headless Chrome for a whole fit loop."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chrome", headless=True)
        self._page = self._browser.new_page()
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def pdf(self, html: str, out: Path) -> int:
        src = out.with_suffix(".print.html")
        src.write_text(for_print(html), "utf-8")
        self._page.goto(src.as_uri(), wait_until="load")
        self._page.evaluate("document.fonts.ready.then(() => true)")
        self._page.pdf(path=str(out), prefer_css_page_size=True, print_background=True)
        src.unlink(missing_ok=True)
        return len(PdfReader(str(out)).pages)


def set_metadata(pdf: Path, title: str, author: str, subject: str) -> None:
    writer = PdfWriter(clone_from=str(pdf))
    writer.add_metadata({"/Title": title, "/Author": author, "/Subject": subject, "/Creator": "cv-tailor",
                         "/Producer": "cv-tailor"})
    with open(pdf, "wb") as f:
        writer.write(f)


def fit_and_print(doc: dict, out: Path, max_pages: int, log=lambda m: None) -> tuple[dict, str, int, list[str]]:
    """Print, and while it overflows, cut the least important thing and print again.
    Bullets arrive ordered by relevance, so popping from the end of a role drops the weakest."""
    doc = copy.deepcopy(doc)
    trims = []
    with Renderer() as r:
        html = render_html(doc)
        pages = r.pdf(html, out)
        while pages > max_pages:
            what = _trim_once(doc, max_pages)
            if not what:
                break
            trims.append(what)
            log(f"Fitting to {max_pages} page{'s' if max_pages > 1 else ''}: {what}")
            html = render_html(doc)
            pages = r.pdf(html, out)
    return doc, html, pages, trims


def print_html(html: str, out: Path) -> int:
    with Renderer() as r:
        return r.pdf(html, out)


def in_thread(fn, *args):
    """Playwright's sync API refuses to run on a thread with an event loop; give it its own."""
    box = {}

    def target():
        try:
            box["value"] = fn(*args)
        except BaseException as e:  # re-raised on the caller's thread
            box["error"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]
