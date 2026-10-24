"""Turn a pasted job description (plain text, HTML, or any job-board JSON) into text plus sections."""
import html
import json
import re

TITLE_KEYS = ("title", "jobTitle", "job_title", "position", "positionName", "jobPostingTitle", "role")
COMPANY_KEYS = ("company", "companyName", "company_name", "hiringOrganization", "organization",
                "employer", "employerName", "organizationName")
DESC_KEYS = ("description", "jobDescription", "job_description", "descriptionHtml", "descriptionText",
             "descriptionPlain", "content", "text", "details", "body", "jobDetails")
LOC_KEYS = ("location", "jobLocation", "formattedLocation", "locationName", "city", "workplace")
LIST_KEYS = ("requirements", "qualifications", "skills", "responsibilities", "mustHave",
             "niceToHave", "preferredQualifications", "basicQualifications", "lists")

REQUIRED = re.compile(r"requirement|qualification|must[\s-]*have|what you('ll)?\s+(need|bring)|"
                      r"what we('re| are)?\s+looking for|you (have|bring|are|will have)|skills (required|needed)|"
                      r"key skills|who you are|about you|experience required|desired (profile|skills)|"
                      r"candidate profile|minimum|basic qualifications|technical skills|tech stack|"
                      r"what it takes|ideal candidate", re.I)
PREFERRED = re.compile(r"nice[\s-]*to[\s-]*have|preferred|bonus|good[\s-]*to[\s-]*have|desirable|"
                       r"\ba plus\b|is a plus|are a plus|extra credit|would be great|added advantage|"
                       r"brownie points|not required but", re.I)
RESPONSIBILITY = re.compile(r"responsibilit|what you('ll| will)\s+do|the role|your role|day[\s-]to[\s-]day|"
                            r"job description|key duties|what you'll work on|impact you", re.I)
OTHER = re.compile(r"about (us|the company|the team|[A-Z])|benefits|perks|what we offer|why join|"
                   r"compensation|our culture|equal opportunit|life at|how to apply|salary", re.I)


def strip_html(s: str) -> str:
    if re.search(r"<[a-zA-Z/][^>]*>", s):
        s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div|/ul|/ol|/tr)\s*/?>", "\n", s)
        s = re.sub(r"(?i)<li[^>]*>", "\n• ", s)
        s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def _bfs(obj, keys):
    """Shallowest value under any of `keys`, so a top-level title beats a nested one."""
    queue = [obj]
    while queue:
        nxt = []
        for node in queue:
            if isinstance(node, dict):
                for k in keys:
                    if k in node and node[k]:
                        return node[k]
                nxt += [v for v in node.values() if isinstance(v, (dict, list))]
            elif isinstance(node, list):
                nxt += [v for v in node if isinstance(v, (dict, list))]
        queue = nxt
    return None


def _as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("name", "text", "title", "value", "displayName"):
            if isinstance(v.get(k), str):
                return v[k]
        return " ".join(_as_text(x) for x in v.values())
    if isinstance(v, list):
        return "\n".join("• " + _as_text(x) if isinstance(x, str) else _as_text(x) for x in v)
    return str(v)


def _all_strings(obj, out):
    if isinstance(obj, str):
        if len(obj) > 40:
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _all_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_strings(v, out)
    return out


def parse_jd(raw: str) -> dict:
    raw = raw.strip()
    data = None
    if raw[:1] in "{[":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
    if data is None:
        return {"title": "", "company": "", "location": "", "text": strip_html(raw), "source": "text"}

    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict)), {})
    desc = _as_text(_bfs(data, DESC_KEYS))
    extra = []
    for k in LIST_KEYS:
        v = _bfs(data, (k,))
        if v and _as_text(v) not in desc:
            extra.append(f"{k}:\n{_as_text(v)}")
    text = "\n\n".join([desc, *extra]) if desc else "\n\n".join(_all_strings(data, []))
    return {
        "title": strip_html(_as_text(_bfs(data, TITLE_KEYS)))[:120],
        "company": strip_html(_as_text(_bfs(data, COMPANY_KEYS)))[:120],
        "location": strip_html(_as_text(_bfs(data, LOC_KEYS)))[:120],
        "text": strip_html(text),
        "source": "json",
    }


def _is_header(line: str) -> bool:
    s = line.strip().strip("*#:•-– ").strip()
    if not s or len(s) > 70:
        return False
    return line.rstrip().endswith(":") or (not s.endswith(".") and len(s.split()) <= 8)


def sections(text: str) -> list[tuple[str, str]]:
    """Each line with the section it sits in: required | preferred | responsibilities | other | intro."""
    out, current = [], "intro"
    for line in text.splitlines():
        if not line.strip():
            continue
        if _is_header(line):
            if PREFERRED.search(line):
                current = "preferred"
            elif REQUIRED.search(line):
                current = "required"
            elif RESPONSIBILITY.search(line):
                current = "responsibilities"
            elif OTHER.search(line):
                current = "other"
        tag = current
        if tag != "other" and PREFERRED.search(line):
            tag = "preferred"
        out.append((tag, line))
    return out
