"""Files read to text: the CV at setup, and what the candidate adds to an interview prep (the recruiter's mail saved
as a PDF, the interview schedule, prep notes from a friend).

Only the text is kept, never the file itself. An interview's files sit in the application's folder and go to the
writer as the candidate's own material, fenced like a job description: facts about the process, never instructions.
"""
import base64
import datetime as dt
import io
import json
import re
import zipfile

from .config import output_dir

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 8
KEEP_CHARS = 20000          # per file
BRIEF_CHARS = 24000         # all files together, in the interview brief
KINDS = ".pdf,.docx,.txt,.md"


def extract_text(filename: str, raw: bytes, what: str = "file") -> str:
    """The text of a PDF, a Word .docx or a plain-text file. ValueError, worded for the person, when it can't be read."""
    if len(raw) > MAX_BYTES:
        raise ValueError(f"That {what} is over 8 MB. Export it again as a PDF.")
    name = (filename or "").lower()
    if name.endswith(".pdf") or raw[:5] == b"%PDF-":
        from pypdf import PdfReader
        try:
            text = "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(raw)).pages)
        except Exception as e:
            raise ValueError(f"Couldn't read that PDF. If it's a scan, paste the {what} text instead.") from e
    elif name.endswith(".docx") or raw[:2] == b"PK":
        try:
            xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode("utf-8", "replace")
        except Exception as e:
            raise ValueError("Couldn't read that Word file. Save it as .docx or PDF, or paste the text.") from e
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        text = re.sub(r"&amp;", "&", re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", text)))
    elif name.endswith((".doc", ".rtf", ".odt", ".pages")):
        raise ValueError(f"Save the {what} as PDF or .docx first (File > Save As), or paste its text.")
    else:
        text = raw.decode("utf-8", "replace")
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r", ""))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------- an interview's files ----------

def _folder(app_id: str):
    return output_dir() / app_id / "interview_files"


def _index(app_id: str) -> list[dict]:
    f = _folder(app_id) / "index.json"
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def _save_index(app_id: str, rows: list[dict]):
    _folder(app_id).mkdir(parents=True, exist_ok=True)
    (_folder(app_id) / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), "utf-8")


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "file"


def listing(app_id: str) -> list[dict]:
    """[{name, chars, added}] in the order they were added."""
    return _index(app_id)


def add(app_id: str, filename: str, data_b64: str) -> list[dict]:
    try:
        raw = base64.b64decode(data_b64 or "", validate=False)
    except Exception as e:
        raise ValueError("The file didn't upload properly. Try again.") from e
    text = extract_text(filename, raw)
    if len(text) < 20:
        raise ValueError("There's almost no text in that file. If it's a scan or a picture, paste what it says instead.")
    name = (filename or "file").strip()[:120]
    rows = [r for r in _index(app_id) if r["name"] != name]
    if len(rows) >= MAX_FILES:
        raise ValueError(f"That's {MAX_FILES} files already. Remove one first.")
    _folder(app_id).mkdir(parents=True, exist_ok=True)
    (_folder(app_id) / (_slug(name) + ".txt")).write_text(text[:KEEP_CHARS], "utf-8")
    rows.append({"name": name, "chars": min(len(text), KEEP_CHARS), "added": dt.datetime.now().isoformat(timespec="seconds")})
    _save_index(app_id, rows)
    return rows


def remove(app_id: str, name: str) -> list[dict]:
    rows = _index(app_id)
    (_folder(app_id) / (_slug(name) + ".txt")).unlink(missing_ok=True)
    rows = [r for r in rows if r["name"] != name]
    _save_index(app_id, rows)
    return rows


def brief_text(app_id: str, limit: int = BRIEF_CHARS) -> str:
    """Every file's text under its name, for the interview brief; "" when there are none."""
    parts, left = [], limit
    for r in _index(app_id):
        f = _folder(app_id) / (_slug(r["name"]) + ".txt")
        if left <= 0 or not f.exists():
            continue
        text = f.read_text("utf-8")[:left]
        parts.append(f"--- {r['name']} ---\n{text}")
        left -= len(text)
    return "\n\n".join(parts)
