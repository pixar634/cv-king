"""Read the finished PDF back the way an ATS parser does (pdfminer text extraction) and check
that what the recruiter's software sees is what we meant to send."""
import re
from pathlib import Path

from pdfminer.high_level import extract_text
from pypdf import PdfReader

HEADINGS = ["PROFESSIONAL SUMMARY", "SKILLS", "WORK EXPERIENCE", "EDUCATION"]
BAD_GLYPHS = re.compile("[ﬀ-ﬆ-�­]")


def pdf_text(pdf: Path) -> str:
    return extract_text(str(pdf))


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _plain(s: str) -> str:
    return _flat(s.replace("**", ""))


def check(pdf: Path, doc: dict, profile: dict, max_pages: int) -> dict:
    text = pdf_text(pdf)
    flat = _flat(text)
    reader = PdfReader(str(pdf))
    checks = []

    def add(cid, label, ok, detail="", level="error"):
        checks.append({"id": cid, "label": label, "ok": bool(ok), "detail": detail, "level": level})

    add("text", "Real, selectable text layer", len(flat) > 1500, f"{len(flat)} characters extracted")
    bad = sorted({f"U+{ord(c):04X}" for c in BAD_GLYPHS.findall(text)})
    add("glyphs", "No ligature or private-use glyphs (they break keyword search)", not bad, ", ".join(bad))

    b = profile["basics"]
    if b.get("email"):
        add("email", "Email parses", b["email"] in flat, b["email"])
    digits = re.sub(r"\D", "", b.get("phone") or "")
    if digits:
        phone_re = re.compile(r"\+?" + r"[\s\-]?".join(digits))
        add("phone", "Phone parses as one number", bool(phone_re.search(flat)), b["phone"])
    if b.get("linkedin"):
        add("linkedin", "LinkedIn URL parses", b["linkedin"] in flat, b["linkedin"])
    add("name", "Name is the first text on the page", flat.startswith(b["name"]), flat[:40])

    pos, last, order_ok = [], -1, True
    for h in HEADINGS:
        i = flat.find(h, last + 1)
        pos.append(f"{h}@{i}")
        if i < 0 or i < last:
            order_ok = False
        last = max(last, i)
    add("headings", "Standard section headings, in reading order", order_ok, " · ".join(pos))

    # Compare with whitespace removed: a line that wraps after a hyphen ("schema-" / "driven")
    # extracts with a space the source never had, and that is layout, not lost order.
    sq = lambda s: re.sub(r"\s+", "", s)
    flat_sq = sq(flat)
    cursor, roles_ok, detail = flat_sq.find("WORKEXPERIENCE"), True, []
    for r in doc["experience"]:
        first = sq(_plain(r["bullets"][0]))[:24] if r["bullets"] else ""
        t = flat_sq.find(sq(r["title"]), cursor)
        c = flat_sq.find(sq(r["company"]), t) if t >= 0 else -1
        f = flat_sq.find(first, c) if c >= 0 and first else c
        nxt_ok = t >= 0 and c >= 0 and f >= 0
        if not nxt_ok:
            roles_ok = False
            detail.append(f"lost order at {r['title']} / {r['company']}")
        cursor = max(cursor, f)
    add("order", "Each role reads title, company, then its bullets", roles_ok, "; ".join(detail))

    add("pages", f"At most {max_pages} pages", len(reader.pages) <= max_pages, f"{len(reader.pages)} pages")

    unembedded, images = set(), 0
    for page in reader.pages:
        res = page.get("/Resources") or {}
        res = res.get_object() if hasattr(res, "get_object") else res
        for _, ref in (res.get("/Font") or {}).items():
            f = ref.get_object()
            desc = f.get("/FontDescriptor")
            if desc is None and "/DescendantFonts" in f:
                desc = f["/DescendantFonts"][0].get_object().get("/FontDescriptor")
            desc = desc.get_object() if desc is not None else None
            if not desc or not any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                unembedded.add(str(f.get("/BaseFont")))
        for _, ref in (res.get("/XObject") or {}).items():
            if ref.get_object().get("/Subtype") == "/Image":
                images += 1
    add("fonts", "All fonts embedded", not unembedded, ", ".join(sorted(unembedded)))
    add("images", "No images (all content is text)", images == 0, f"{images} images", level="warn")

    meta = reader.metadata or {}
    add("meta", "PDF title and author set", bool(meta.get("/Title")) and bool(meta.get("/Author")),
        f"{meta.get('/Title', '')} / {meta.get('/Author', '')}", level="warn")
    size_kb = pdf.stat().st_size // 1024
    add("size", "File under 1 MB", size_kb < 1024, f"{size_kb} KB")

    return {"text": text, "checks": checks, "pages": len(reader.pages)}
