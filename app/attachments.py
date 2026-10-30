"""Files read to text: the CV at setup, and what the candidate adds to an interview prep (the recruiter's mail saved
as a PDF, the interview schedule, prep notes from a friend).

Only the text is kept, never the file itself. An interview's files sit in the application's folder and go to the
writer as the candidate's own material, fenced like a job description: facts about the process, never instructions.
"""
import base64
import datetime as dt
import html
import io
import json
import re
import urllib.parse
import urllib.request
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
    return _keep(app_id, (filename or "file").strip()[:120], text)


def _keep(app_id: str, name: str, text: str) -> list[dict]:
    rows = [r for r in _index(app_id) if r["name"] != name]
    if len(rows) >= MAX_FILES:
        raise ValueError(f"That's {MAX_FILES} files already. Remove one first.")
    _folder(app_id).mkdir(parents=True, exist_ok=True)
    (_folder(app_id) / (_slug(name) + ".txt")).write_text(text[:KEEP_CHARS], "utf-8")
    rows.append({"name": name, "chars": min(len(text), KEEP_CHARS), "added": dt.datetime.now().isoformat(timespec="seconds")})
    _save_index(app_id, rows)
    return rows


# ---------- links: a YouTube video or a web page ----------

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
YOUTUBE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")
PRIVATE = re.compile(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|\[?::1\]?$)")


def _get(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return r.read(3_000_000).decode("utf-8", "replace")


def _youtube(video: str, url: str) -> tuple[str, str]:
    """Title, channel, length and description. What is said in the video isn't open to apps any more (YouTube serves
    empty captions to anything but its own player), so the text says how to add it."""
    page = _get(f"https://www.youtube.com/watch?v={video}&hl=en")
    m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\});\s*(?:var\s|</script>)", page, re.S)
    try:
        details = json.loads(m.group(1))["videoDetails"] if m else {}
    except (ValueError, KeyError):
        details = {}
    if not details.get("title"):
        raise ValueError("Couldn't read that video (private, removed, or YouTube asked for a sign-in).")
    secs = int(details.get("lengthSeconds") or 0)
    text = (f"YouTube video: {details['title']}\nChannel: {details.get('author', '')}\nLength: {secs // 60}:{secs % 60:02d}\n"
            f"Link: {url}\n\nDescription:\n{details.get('shortDescription', '').strip() or '(none)'}\n\n"
            "(What is said in the video isn't included: YouTube doesn't let apps read transcripts.)")
    return f"YouTube: {details['title'][:90]}", text


def _page(url: str) -> tuple[str, str]:
    raw = _get(url)
    found = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    title = html.unescape(re.sub(r"\s+", " ", found.group(1))).strip() if found else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", raw)
    body = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)\b[^>]*>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    if len(text) < 200:
        raise ValueError("That page shows its text only in a browser. Open it, save it as PDF (Ctrl+P), and attach the PDF.")
    return (title[:100] or url), f"Web page: {title}\nLink: {url}\n\n{text}"


def add_link(app_id: str, url: str) -> list[dict]:
    """A link the candidate was sent: a YouTube video's details, or a web page's text, kept like a file."""
    url = (url or "").strip()
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not re.match(r"https?://", url, re.I) or not host or PRIVATE.match(host):
        raise ValueError("Paste a full web link, starting with https://")
    video = YOUTUBE.search(url)
    try:
        name, text = _youtube(video.group(1), url) if video else _page(url)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("Couldn't open that link. Check it opens in your browser, or save the page as PDF and attach it.") from e
    return _keep(app_id, name, text)


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
