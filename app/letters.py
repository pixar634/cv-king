"""Cover letters in five voices. Same facts, same guards as the CV; only the register changes."""
import itertools
import json
import re

import yaml

from .config import KNOWLEDGE, OUTPUT
from .engines import get_engine, parse_json_reply
from .keywords import jd_keywords
from .profile import brief_view, load_profile, profile_text

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
    folder = OUTPUT / app_id
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
        f"## JOB DESCRIPTION\n<<<\n{jd_text}\n>>>",
        f"## PROFILE\n```json\n{json.dumps(brief_view(profile), indent=1, ensure_ascii=False)}\n```",
    ])
    engine = get_engine(engine_id)
    text = (parse_json_reply(engine.run(brief, timeout=300)).get("cover_letter") or "").strip()
    kws = jd_keywords(jd_text, report["jd_meta"].get("must_have"), report["jd_meta"].get("nice_to_have"))
    flags = _text_flags(text, profile_text(profile),
                        _allowed_numbers(report["options"]["header_line"], report["experience_years"]),
                        kws, "Cover letter")
    flags = soften(flags) + phrase_flags(text)
    return {"voice": voice_id, "label": v["label"], "text": text, "flags": flags,
            "words": len(text.split())}


def keep_letter(app_id: str, text: str, voice_id: str) -> None:
    folder = OUTPUT / app_id
    (folder / "cover_letter.txt").write_text(text.strip() + "\n", "utf-8")
    report = json.loads((folder / "report.json").read_text("utf-8"))
    report["cover_letter"], report["cover_voice"] = text.strip(), voice_id
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
