"""Where every tailored CV went: one record per application, logos cached locally, Excel export."""
import datetime as dt
import io
import json
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import db
from .config import output_dir

STATUSES = ["Generated", "Applied", "Recruiter call", "Interviewing", "Offer", "Rejected", "No response", "Withdrawn"]
_lock = threading.Lock()

# Hosts that carry other companies' jobs: their logo is not the employer's.
JOB_HOSTS = ("linkedin.com", "naukri.com", "naukrigulf.com", "indeed.com", "glassdoor.com", "glassdoor.co.in",
             "greenhouse.io", "lever.co", "ashbyhq.com", "workday.com", "myworkdayjobs.com", "wellfound.com",
             "angel.co", "instahyre.com", "cutshort.io", "hirist.tech", "iimjobs.com", "foundit.in", "monster.com",
             "smartrecruiters.com", "icims.com", "workable.com", "breezy.hr", "recruitee.com", "bamboohr.com",
             "jobvite.com", "taleo.net", "successfactors.com", "keka.com", "darwinbox.in", "zohorecruit.com",
             "freshteam.com", "ycombinator.com", "workatastartup.com", "google.com", "bayt.com", "gulftalent.com",
             "jobstreet.com", "mycareersfuture.gov.sg", "reed.co.uk", "totaljobs.com", "stepstone.de", "xing.com",
             "otta.com", "welcometothejungle.com", "remoteok.com", "weworkremotely.com", "himalayas.app",
             "turing.com", "uplers.com", "apna.co", "shine.com", "timesjobs.com", "jooble.org")
SECOND_LEVEL = ("co.in", "co.uk", "com.sg", "com.au", "co.jp", "com.br", "ac.in", "org.in", "net.in", "ae", "gov.sg")


def _file() -> Path:
    return output_dir() / "applications.json"


def load() -> list[dict]:
    f = _file()
    if not f.exists():
        return []
    return json.loads(f.read_text("utf-8"))


def _save(rows: list[dict]) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(f)


def domain_from_url(url: str) -> str:
    try:
        host = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower()
    except ValueError:
        return ""
    if not host or "." not in host:
        return ""
    if any(host == h or host.endswith("." + h) for h in JOB_HOSTS):
        return ""
    parts = host.split(".")
    keep = 3 if ".".join(parts[-2:]) in SECOND_LEVEL and len(parts) >= 3 else 2
    return ".".join(parts[-keep:])


def _clean_domain(d: str) -> str:
    d = (d or "").strip().lower()
    return domain_from_url(d) if d else ""


def fetch_logo(app_id: str, domain: str) -> bool:
    """Download the site's icon once into the application folder. Google's favicon service first
    (it returns 404 for unknown sites), DuckDuckGo's as a fallback; anything tiny is rejected."""
    if not domain:
        return False
    out = output_dir() / app_id / "logo.png"
    for url in (f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
                f"https://icons.duckduckgo.com/ip3/{domain}.ico"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 jobmagnet"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status != 200:
                    continue
                data = resp.read()
        except Exception:
            continue
        if len(data) < 300:
            continue
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            if img.width < 32:
                continue
            img.convert("RGBA").save(out, "PNG")
        except Exception:
            continue
        return True
    return False


def add_from_report(report: dict, req: dict) -> None:
    domain = _clean_domain(req.get("company_site") or "") or domain_from_url(req.get("job_url") or "") \
        or _clean_domain((report.get("jd_meta") or {}).get("company_domain") or "")
    row = {
        "id": report["id"], "created": report["created"], "company": report["company"], "role": report["role"],
        "market": report["market_label"], "layout": report["options"]["variant"],
        "pages": report["options"]["max_pages"], "engine": report["engine"],
        "score_before": report["scores"]["before"], "score_after": report["scores"]["after"],
        "score_ceiling": report["scores"]["ceiling"], "status": "Generated", "applied_on": "",
        "job_url": req.get("job_url") or "", "domain": domain, "logo": False, "notes": "",
        "pdf": report["pdf"], "job_id": req.get("job_id") or "", "location": req.get("location") or "",
        "posted": req.get("posted") or "",
    }
    row["logo"] = fetch_logo(report["id"], domain)
    replaced = req.get("replace_id")
    with _lock:
        db.ensure_seeded()
        old = next((r for r in load() if r["id"] == replaced), None)
        if old:  # a re-tailor after confirming skills keeps the application's status and notes
            row.update({k: old[k] for k in ("status", "applied_on", "notes", "job_url") if old.get(k)})
            row["domain"] = row["domain"] or old.get("domain", "")
        rows = [r for r in load() if r["id"] not in (row["id"], replaced)]
        _save([row] + rows)
        if old:
            db.moved(old["id"], row["id"])
            db.record_status(row["id"], row["company"], row["role"], old["status"], row["status"], "tailor",
                             f"re-tailored, replacing {old['id']}")
        else:
            db.record_status(row["id"], row["company"], row["role"], None, "Generated", "tailor")
    db.sync_soon()


def update(app_id: str, fields: dict, source: str = "app") -> dict:
    """source: who changed it (app or extension), kept with the status history."""
    allowed = {"status", "applied_on", "job_url", "notes", "domain", "company", "role"}
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"Unknown status {fields['status']!r}: use one of {', '.join(STATUSES)}")
    with _lock:
        db.ensure_seeded()
        rows = load()
        for r in rows:
            if r["id"] != app_id:
                continue
            was = r["status"]
            for k, v in fields.items():
                if k in allowed:
                    r[k] = v
            if fields.get("status") not in (None, "Generated") and not r.get("applied_on"):   # any later stage means it went out
                r["applied_on"] = dt.date.today().isoformat()
            if "domain" in fields:
                r["domain"] = _clean_domain(fields["domain"])
                r["logo"] = fetch_logo(app_id, r["domain"])
            _save(rows)
            if r["status"] != was:
                db.record_status(app_id, r["company"], r["role"], was, r["status"], source)
            db.sync_soon()
            return r
    raise KeyError(app_id)


def sync_scores(report: dict) -> None:
    """A re-render or a hand edit changes the score and the file; keep the tracker in step."""
    with _lock:
        rows = load()
        for r in rows:
            if r["id"] == report["id"]:
                r.update({"score_after": report["scores"]["after"], "score_before": report["scores"]["before"],
                          "score_ceiling": report["scores"]["ceiling"], "layout": report["options"]["variant"],
                          "pages": report["options"]["max_pages"], "pdf": report["pdf"]})
        _save(rows)


def export_xlsx() -> Path:
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from PIL import Image

    rows = load()
    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    head = ["", "Company", "Role", "Status", "Applied on", "Generated", "Market", "Layout", "Pages",
            "Keyword match (before)", "Keyword match (after)", "Profile ceiling", "Job link", "Notes", "CV file"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1D4E5C")
        c.alignment = Alignment(vertical="center")
    for i, r in enumerate(rows, start=2):
        ws.append(["", r["company"], r["role"], r["status"], r.get("applied_on", ""), r["created"][:10],
                   r["market"], r["layout"], r["pages"], r["score_before"] / 100, r["score_after"] / 100,
                   r["score_ceiling"] / 100, r.get("job_url", ""), r.get("notes", ""),
                   str(output_dir() / r["id"] / r["pdf"])])
        ws.row_dimensions[i].height = 24
        for col in (10, 11, 12):
            ws.cell(i, col).number_format = "0%"
        if r.get("job_url"):
            ws.cell(i, 13).hyperlink = r["job_url"]
            ws.cell(i, 13).font = Font(color="1D4E5C", underline="single")
        logo = output_dir() / r["id"] / "logo.png"
        if r.get("logo") and logo.exists():
            thumb = output_dir() / r["id"] / "logo_xl.png"
            Image.open(logo).convert("RGBA").resize((28, 28)).save(thumb)
            img = XLImage(str(thumb))
            img.width = img.height = 28
            ws.add_image(img, f"A{i}")
    widths = [5, 22, 34, 14, 12, 12, 16, 10, 7, 12, 12, 12, 40, 30, 60]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(head))}{max(1, len(rows) + 1)}"
    out = output_dir() / f"applications_{dt.date.today().isoformat()}.xlsx"
    wb.save(out)
    return out
