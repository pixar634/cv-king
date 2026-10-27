"""Cover letters in five voices. Same facts, same guards as the CV; only the register changes."""
import datetime as dt
import itertools
import json
import re
import threading

import yaml

from .config import KNOWLEDGE, output_dir
from .engines import get_engine, parse_json_reply
from .keywords import jd_keywords
from .profile import brief_view, load_profile, profile_text
from .untrusted import fence

HUMAN_RULES = """How a human professional writes (follow all of these):
- Specific beats general: name the system, the stack, the number. Never claim a quality ("detail-oriented",
  "passionate") the facts don't show by themselves.
- Vary sentence length. Some short. Never three sentences in a row that start with "I".
- No template openers or closers, no rhetorical questions, no exclamation marks, no emojis.
- Talk about the employer only through what the JD itself says. Never invent knowledge of the company,
  its products, values or news.
- Every fact and number comes from the PROFILE, copied exactly. Same rule zero as the CV.
- Never mention what the candidate lacks, hasn't used or would need to learn. Gaps are for the
  interview, not the application; a letter that lists weaknesses screens the candidate out.
- Plain text only. Paragraphs separated by one blank line. Sign off with the candidate's name."""


def voices() -> dict:
    return yaml.safe_load((KNOWLEDGE / "voices.yaml").read_text("utf-8"))


def banned_phrases() -> list[str]:
    return voices()["banned_phrases"]


def first_letter_rules() -> str:
    return ("## COVER LETTER\n`cover_letter`: 170 to 240 words, 3 or 4 short paragraphs. Open with "
            "\"Dear Hiring Team,\" unless the JD names the hiring manager. Paragraph 1: the role and the "
            "one-line reason the candidate fits it. Paragraph 2: two concrete proof points from the profile, "
            "with its own numbers, matched to the JD's top needs. Paragraph 3: why this role, using only what "
            "the JD says, plus availability. Close with \"Kind regards,\" and the candidate's name.\n"
            + HUMAN_RULES + "\nNever use these phrases: " + "; ".join(banned_phrases()) + ".")


def phrase_flags(text: str) -> list[dict]:
    lower = text.lower()
    hits = [p for p in banned_phrases() if p.lower() in lower]
    flags = []
    if hits:
        flags.append({"level": "warn", "kind": "tone", "text": "Template-sounding phrases: " + "; ".join(hits)})
    if text.count("!"):
        flags.append({"level": "warn", "kind": "tone", "text": "Exclamation marks read as junior; remove them."})
    starts = [s.strip().split(" ")[0] for s in re.split(r"(?<=[.?])\s+", text) if s.strip()]
    run = max((len(list(g)) for k, g in itertools.groupby(starts) if k == "I"), default=0)
    if run >= 3:
        flags.append({"level": "warn", "kind": "tone", "text": f"{run} sentences in a row start with \"I\"."})
    return flags


def write_letter(app_id: str, voice_id: str, engine_id: str) -> dict:
    from .tailor import _allowed_numbers, _text_flags, markets, soften  # avoid an import cycle
    folder = output_dir() / app_id
    report = json.loads((folder / "report.json").read_text("utf-8"))
    plan = json.loads((folder / "plan.json").read_text("utf-8"))
    jd_text = (folder / "jd.txt").read_text("utf-8")
    profile = load_profile()
    v = voices()[voice_id]
    m = markets()[report["market"]]
    brief = "\n\n".join([
        "You are rewriting a cover letter for one candidate and one job. Return JSON only: "
        '{"cover_letter": "string"}.',
        f"## VOICE: {v['label']}\n{v['brief']}",
        HUMAN_RULES,
        "Never use these phrases: " + "; ".join(banned_phrases()) + ".",
        f"## MARKET: {m['label']}\nSpelling: {m['spelling']}",
        f"## TARGET\nCompany: {report['company']}\nRole: {report['role']}\nCandidate: {profile['basics']['name']}\n"
        f"Availability: {profile['basics'].get('availability', '')}\nStatus line on the CV: {report['options']['header_line']}",
        "## THE TAILORED CV (lead with what it leads with)\n" + json.dumps(plan, indent=1, ensure_ascii=False),
        f"## JOB DESCRIPTION\n<<<\n{fence(jd_text)}\n>>>",
        f"## PROFILE\n```json\n{json.dumps(brief_view(profile), indent=1, ensure_ascii=False)}\n```",
    ])
    engine = get_engine(engine_id)
    text = (parse_json_reply(engine.run(brief, timeout=300)).get("cover_letter") or "").strip()
    kws = jd_keywords(jd_text, report["jd_meta"].get("must_have"), report["jd_meta"].get("nice_to_have"))
    flags = _text_flags(text, profile_text(profile),
                        _allowed_numbers(report["options"]["header_line"], report["experience_years"]),
                        kws, "Cover letter")
    flags = soften(flags) + phrase_flags(text)
    return add_version(app_id, {"voice": voice_id, "label": v["label"], "text": text, "flags": flags})


# ---------- every version of an application's letter, kept next to it ----------
# letters.json holds each letter written for the application: the first draft tailoring wrote, every voice tried,
# and the candidate's own edits. Keeping one makes it the letter (cover_letter.txt and the report); the others stay,
# so going back to an earlier one is one click.

_lock = threading.Lock()


def _versions_file(app_id: str):
    return output_dir() / app_id / "letters.json"


def _read(app_id: str) -> list[dict]:
    f = _versions_file(app_id)
    rows = json.loads(f.read_text("utf-8")) if f.exists() else []
    if not any(r["id"] == "first" for r in rows):
        # The first draft is what tailoring wrote. Keeping another voice used to overwrite the report's copy,
        # so read it from the model's own reply, which nothing rewrites.
        folder = output_dir() / app_id
        first = ""
        try:
            first = (json.loads((folder / "model_reply.json").read_text("utf-8")).get("cover_letter") or "").strip()
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        report = json.loads((folder / "report.json").read_text("utf-8"))
        if not first and not report.get("cover_voice"):   # never replaced: the report's copy is still the first draft
            first = (report.get("cover_letter") or "").strip()
        if first:
            rows.insert(0, _row("first", "first", "First draft", first, at=report.get("created", "")))
        kept = (report.get("cover_letter") or "").strip()
        if kept and not any(r["text"].strip() == kept for r in rows):
            voice = report.get("cover_voice") or "edited"
            rows.append(_row(_new_id(voice), voice, _label(voice), kept))
    return rows


def _write(app_id: str, rows: list[dict]) -> None:
    _versions_file(app_id).write_text(json.dumps(rows, indent=1, ensure_ascii=False), "utf-8")


def _label(voice: str) -> str:
    v = voices().get(voice)
    return v["label"] if isinstance(v, dict) else "Your edit" if voice in ("", "edited") else "Draft"


def _new_id(voice: str) -> str:
    return f"{voice}-{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}"


def _row(vid, voice, label, text, flags=None, at="") -> dict:
    text = (text or "").strip()
    return {"id": vid, "voice": voice, "label": label, "text": text, "flags": flags if flags is not None else phrase_flags(text),
            "words": len(text.split()), "at": at or dt.datetime.now().isoformat(timespec="seconds")}


def add_version(app_id: str, d: dict) -> dict:
    """Store one more letter (a voice, an edit, or a draft carried over from the browser); the same text twice is one."""
    with _lock:
        rows = _read(app_id)
        same = next((r for r in rows if r["text"].strip() == (d.get("text") or "").strip()), None)
        if same:
            return same
        row = _row(d.get("id") or _new_id(d.get("voice") or "draft"), d.get("voice") or "draft",
                   d.get("label") or "Draft", d.get("text"), d.get("flags"), d.get("at", ""))
        _write(app_id, rows + [row])
        return row


def list_versions(app_id: str) -> dict:
    """Newest first, with the one that is the application's letter now."""
    with _lock:
        rows = _read(app_id)
        if rows and not _versions_file(app_id).exists():
            _write(app_id, rows)
    kept = (json.loads((output_dir() / app_id / "report.json").read_text("utf-8")).get("cover_letter") or "").strip()
    kept_id = next((r["id"] for r in reversed(rows) if r["text"].strip() == kept), "")
    return {"versions": sorted(rows, key=lambda r: r["at"], reverse=True), "kept": kept_id}


def remove_version(app_id: str, vid: str) -> None:
    """Drop a version you don't want. The first draft and the letter in use stay."""
    with _lock:
        rows = _read(app_id)
        kept = (json.loads((output_dir() / app_id / "report.json").read_text("utf-8")).get("cover_letter") or "").strip()
        _write(app_id, [r for r in rows if r["id"] != vid or r["id"] == "first" or r["text"].strip() == kept])


def keep_letter(app_id: str, text: str, voice_id: str, version_id: str = "") -> None:
    """Make one version the application's letter. A text that isn't a version yet (an edit) becomes one first."""
    if version_id:
        row = next((r for r in _read(app_id) if r["id"] == version_id), None)
        if not row:
            raise KeyError(version_id)
        text, voice_id = row["text"], row["voice"]
    else:
        add_version(app_id, {"voice": voice_id or "edited", "label": _label(voice_id), "text": text})
    folder = output_dir() / app_id
    (folder / "cover_letter.txt").write_text(text.strip() + "\n", "utf-8")
    report = json.loads((folder / "report.json").read_text("utf-8"))
    report["cover_letter"], report["cover_voice"] = text.strip(), voice_id
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
