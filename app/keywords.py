"""Deterministic keyword match: what the JD asks for, what a CV's text covers.

The score is code, never the model. The model may *name* JD terms (it reads the JD better than a
regex), but a term only counts if it appears in the JD text, and a CV only covers it if its own
text says it. Same inputs, same score, every time.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from .config import KNOWLEDGE
from .jd import sections

SECTION_WEIGHT = {"required": 3.0, "responsibilities": 2.0, "intro": 2.0, "preferred": 1.0, "other": 1.0}
# Soft terms count for half and are never treated as invented: they are judgement, not claims.
SOFT = {"Communication", "Problem solving", "Ownership", "Product thinking", "Clean code"}
BOUND_L, BOUND_R = r"(?<![A-Za-z0-9+#])", r"(?![A-Za-z0-9+#])"


def _alias_regex(alias: str, case_sensitive: bool) -> re.Pattern:
    if alias.startswith("re:"):
        return re.compile(alias[3:])
    body = r"[\s\-]+".join(re.escape(part) for part in re.split(r"[\s\-]+", alias.strip()))
    return re.compile(BOUND_L + body + BOUND_R, 0 if case_sensitive else re.I)


@dataclass
class Term:
    name: str
    patterns: list
    category: str
    custom: bool = False


class Taxonomy:
    def __init__(self):
        raw = yaml.safe_load((KNOWLEDGE / "skills.yaml").read_text("utf-8"))
        cs = set(raw.get("case_sensitive", []))
        self.terms: dict[str, Term] = {}
        for category, entries in raw["categories"].items():
            for canon, aliases in entries.items():
                # A regex alias exists because the bare name is ambiguous (Go, R): don't add it back.
                names = set(aliases) | (set() if any(a.startswith("re:") for a in aliases) else {canon})
                self.terms[canon] = Term(canon, [_alias_regex(a, canon in cs) for a in names], category)

    def find(self, text: str) -> dict[str, int]:
        """canonical -> count, longest match wins where spans overlap (React Native is not React)."""
        spans = []
        for t in self.terms.values():
            for p in t.patterns:
                spans += [(m.start(), m.end(), t.name) for m in p.finditer(text)]
        spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
        taken, counts = [], {}
        for s, e, name in spans:
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            counts[name] = counts.get(name, 0) + 1
        return counts

    def canonical_for(self, phrase: str) -> str | None:
        """If a model-named term is just a taxonomy alias, use the taxonomy entry."""
        phrase = phrase.strip()
        for t in self.terms.values():
            for p in t.patterns:
                m = p.search(phrase)
                if m and (m.end() - m.start()) >= 0.6 * len(phrase):
                    return t.name
        return None


@lru_cache(maxsize=1)
def taxonomy() -> Taxonomy:
    return Taxonomy()


@dataclass
class Keyword:
    name: str
    weight: float
    tier: str            # required | preferred | general
    count: int = 1
    custom: bool = False
    patterns: list = field(default_factory=list)


def jd_keywords(jd_text: str, model_must: list[str] | None = None,
                model_nice: list[str] | None = None) -> dict[str, Keyword]:
    tax = taxonomy()
    kws: dict[str, Keyword] = {}

    def add(name, w, tier, patterns, custom=False):
        k = kws.get(name)
        if not k:
            kws[name] = Keyword(name, w, tier, 1, custom, patterns)
        else:
            k.count += 1
            if w > k.weight:
                k.weight, k.tier = w, tier

    for tag, line in sections(jd_text):
        w = SECTION_WEIGHT[tag]
        tier = "required" if tag == "required" else "preferred" if tag == "preferred" else "general"
        for name, n in tax.find(line).items():
            if name in SOFT:
                continue
            for _ in range(n):
                add(name, w, tier, tax.terms[name].patterns)

    # Model-named terms: a phrase like "GCP, Azure and AWS" or "Bachelor's degree in CS" is several
    # taxonomy skills (or one), never one keyword no CV could match. Only a short phrase the taxonomy
    # doesn't know becomes a custom term, and only if the JD really contains it.
    for terms, w, tier in ((model_must or [], 3.0, "required"), (model_nice or [], 1.0, "preferred")):
        for raw in terms:
            if not raw or len(raw) > 80:
                continue
            found = {c: n for c, n in tax.find(raw).items() if c not in SOFT}
            if found:
                for canon in found:
                    if canon in kws:
                        if w > kws[canon].weight:
                            kws[canon].weight, kws[canon].tier = w, tier
                    elif any(p.search(jd_text) for p in tax.terms[canon].patterns):
                        add(canon, w, tier, tax.terms[canon].patterns)
                continue
            raw = raw.strip()
            if "," in raw or len(raw.split()) > 4:
                continue
            pat = _alias_regex(raw, False)
            if pat.search(jd_text) and raw.lower() not in {k.lower() for k in kws}:
                add(raw, w, tier, [pat], custom=True)

    for k in kws.values():
        k.weight *= 1 + 0.25 * min(k.count - 1, 2)
        if k.name in SOFT:
            k.weight *= 0.5
    return kws


def occurrences(kw: Keyword, text: str) -> int:
    spans = sorted({(m.start(), m.end()) for p in kw.patterns for m in p.finditer(text)})
    n, end = 0, -1
    for s, e in spans:
        if s >= end:
            n += 1
        end = max(end, e)
    return n


def covered(kw: Keyword, text: str) -> bool:
    return any(p.search(text) for p in kw.patterns)


def jd_spelling(kw: Keyword, jd_text: str) -> str:
    """How the JD itself writes a keyword, so the CV can mirror it exactly."""
    for p in kw.patterns:
        m = p.search(jd_text)
        if m:
            return m.group(0)
    return kw.name


def score(kws: dict[str, Keyword], cv_text: str) -> dict:
    rows, total, got = [], 0.0, 0.0
    for k in sorted(kws.values(), key=lambda k: -k.weight):
        hit = covered(k, cv_text)
        total += k.weight
        got += k.weight if hit else 0
        rows.append({"term": k.name, "tier": k.tier, "weight": round(k.weight, 2),
                     "jd_count": k.count, "covered": hit,
                     "cv_count": occurrences(k, cv_text)})
    return {"score": round(100 * got / total) if total else 0, "rows": rows}


def title_match(jd_title: str, headline: str) -> dict:
    norm = lambda s: {w for w in re.split(r"[^a-z0-9+#]+", s.lower().replace("full stack", "full-stack")
                                          .replace("fullstack", "full-stack").replace("-", "")) if w}
    stop = {"and", "the", "of", "for", "a", "an", "to", "in", "at", "with", "ii", "iii", "i"}
    jt, ht = norm(jd_title) - stop, norm(headline) - stop
    if not jt:
        return {"ratio": None, "missing": []}
    alike = {"developer": "engineer", "dev": "engineer", "sde": "engineer", "swe": "engineer", "sr": "senior"}
    jt = {alike.get(w, w) for w in jt}
    ht = {alike.get(w, w) for w in ht}
    return {"ratio": round(len(jt & ht) / len(jt), 2), "missing": sorted(jt - ht)}
