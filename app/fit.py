"""Is this job at the candidate's level? Read by code from the JD: years of experience asked for,
years leading people, scope (managing managers, org size), and the seniority its title names against the
candidate's own level (from their strategy). A keyword match says nothing about level; a "Head of Engineering,
10+ years, manages managers" JD can share every soft word with a Lead CV.
"""
import re

PM_TITLE = re.compile(r"product (manager|owner|lead)|\bapm\b", re.I)
NUM = r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?"
YEARS = re.compile(NUM + r"(?:years?|yrs?)\b(?:\s+of)?(?P<after>[^.\n;]{0,70})", re.I)
LEADING = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?\s+|yrs?\s+)?(?:of\s+)?(?:experience\s+)?(?:in\s+)?"
                     r"(?:leading|managing|people management|engineering management|management|leadership|"
                     r"as an? (?:engineering )?manager)", re.I)
LEAD_WORDS = re.compile(r"lead|manag|people|direct reports|leadership|supervis", re.I)
EXP_WORDS = re.compile(r"experience|engineering|software|development|industry|professional|building|working|"
                       r"hands-on|programming|coding|in (?:a |the )?(?:tech|it)\b", re.I)
SCOPE = re.compile(r"manag(?:er|ing|e)s? of managers|managing (?:engineering )?managers|manage (?:other )?managers|"
                   r"(?:organi[sz]ation|org|team|department) of (?:\d{2,3})\+? (?:engineers|people)|"
                   r"\((?:ideally |approximately |about )?(?:2[5-9]|[3-9]\d|\d{3})\s*(?:-|–|to)\s*\d{2,3} engineers\)|"
                   r"(?:2[5-9]|[3-9]\d|\d{3})\s*\+?\s*(?:-|–|to)?\s*\d{0,3}\s*(?:engineers|direct reports)\b", re.I)


def requirements(text: str) -> dict:
    """Years of experience the JD asks for (its largest minimum), years leading people, and scope."""
    main, lead, fields, pm = [], [], [], []
    text = text or ""
    for m in YEARS.finditer(text):
        low = int(m.group(1))
        if low > 30:
            continue
        after, before = m.group("after"), text[max(0, m.start() - 30):m.start()]
        clause = re.split(r"[,;(]|\band\b|\d", after)[0]  # "10+ years in engineering, 5+ leading": only "in engineering"
        if re.search(r"product (management|manager)|as an? (senior )?pm\b|\bpm experience", clause, re.I):
            pm.append(low)
        elif LEAD_WORDS.search(clause):
            lead.append(low)
        # A count only means "years asked for" next to experience words: never "founded 12 years ago".
        elif EXP_WORDS.search(after) or re.search(r"experience|minimum|at least|require", before, re.I):
            main.append(low)
            fields.append((low, clause))
    lead += [int(x) for x in LEADING.findall(re.sub(r"product (management|manager)", "product", text or "", flags=re.I)) if int(x) <= 30]
    scope = SCOPE.search(text or "")
    return {"years": max(main) if main else None, "leading_years": max(lead) if lead else None,
            "scope": scope.group(0) if scope else "", "fields": fields, "pm_years": max(pm) if pm else None}


# Whole disciplines: a role whose title is built on one of these, without it on the CV, is a different job.
DISCIPLINES = {"Machine learning", "TensorFlow", "PyTorch", "LLMs", "RAG", "Spark", "Data pipelines", "Flutter",
               "React Native", "Swift", "Kotlin", "Go", "Rust", "C++", "C", "Scala", "Ruby on Rails", "Ruby", "Java",
               ".NET", "C#", "Spring Boot", "Kubernetes", "Terraform", "Elasticsearch", "Kafka"}
GENERIC = {"Communication", "Problem solving", "Ownership", "Product thinking", "Clean code", "Mentoring",
           "Stakeholder management", "Scalability", "Technical documentation", "Agile", "Requirements gathering",
           "Data visualisation", "Enterprise software", "Startup experience", "Technical leadership", "Code review",
           "Performance", "Automation", "Security", "Monitoring", "Full-stack development", "Frontend development",
           "Backend development", "System design", "Fintech", "SaaS", "E-commerce", "Analytics products"}


def strong_skills(profile: dict) -> set[str]:
    """What the candidate actually lists as skills (profile skills, role stacks, confirmed skills).
    A word inside a bullet's description of a product ("bridging data science, machine learning...") is not a skill."""
    from .keywords import taxonomy
    from .profile import all_skills
    stacks = [s for r in profile["experience"] + profile.get("projects", []) for s in r.get("stack", [])]
    return set(taxonomy().find("\n".join(all_skills(profile) + stacks)))


def level_check(title: str, text: str, candidate_years: int, strong: set[str] | None = None,
                max_years: int | None = None, profile: dict | None = None, company: str = "") -> dict:
    """verdict: 'ok', 'stretch' (rank lower, say why) or 'above' (hide from Discover, warn when tailoring)."""
    from . import strategy
    from .keywords import taxonomy
    req = requirements(text)
    lv = strategy.level()
    held = [r.get("title", "") for r in (profile or {}).get("experience", [])]
    reasons, verdict = [], "ok"
    # A verdict only ever goes up: a later "stretch" must never soften an earlier "above".
    raise_to = lambda v: "above" if "above" in (v, verdict) else "stretch" if "stretch" in (v, verdict) else "ok"
    if strong is not None:
        missing_title = sorted(set(taxonomy().find(title or "")) - strong - GENERIC)
        core = [t for t in missing_title if t in DISCIPLINES]
        if core:
            verdict = "above"
            reasons.append(f"the role is built on {', '.join(core)}, which isn't on your CV")
        elif missing_title:
            verdict = raise_to("stretch")
            reasons.append(f"title asks for {', '.join(missing_title)}, not on your CV")
        for years, clause in req["fields"]:
            need = sorted(set(taxonomy().find(clause)) - strong - GENERIC)
            if need and years >= 2:
                verdict = raise_to("above" if any(n in DISCIPLINES for n in need) and years >= 3 else "stretch")
                reasons.append(f"asks for {years}+ years in {', '.join(need)} (not on your CV)")
    ceiling = max_years if max_years is not None else candidate_years + 2
    if req["years"] is not None and req["years"] > ceiling:
        verdict = "above"
        reasons.append(f"asks for {req['years']}+ years (you have {candidate_years}+; you reach up to {ceiling})")
    elif req["years"] is not None and req["years"] > candidate_years:
        verdict = raise_to("stretch")
        reasons.append(f"asks for {req['years']}+ years (you have {candidate_years}+)")
    if req["scope"]:
        verdict = "above"
        reasons.append(f"manages managers or a large org (\"{req['scope'][:50]}\")")
    if req["leading_years"] is not None and req["leading_years"] >= 2 and not lv.get("people_manager"):
        # For a manager's title the people are the job, not a plus: no direct reports screens you out at once.
        from .outcomes import kind
        manager = kind(title or "") == "people manager"
        verdict = raise_to("above" if req["leading_years"] >= 5 or manager else "stretch")
        reasons.append(f"asks for {req['leading_years']}+ years managing people (your CV shows no direct reports)")
    if req["pm_years"] is not None and req["pm_years"] >= 3 and not any(PM_TITLE.search(t) for t in held):
        verdict = raise_to("above" if req["pm_years"] >= 8 else "stretch")
        reasons.append(f"asks for {req['pm_years']}+ years as a product manager (your CV shows no PM title)")
    # The seniority the title names, against the candidate's own level: two steps up is a stretch, three is above.
    named = strategy.title_level(title or "", company=company)
    if named is not None and named - lv["rank"] >= 3:
        verdict = "above"
        reasons.append(f"title is three or more levels above yours ({lv['label']})")
    elif named is not None and named - lv["rank"] >= 2:
        verdict = raise_to("stretch")
        reasons.append(f"title is two levels above yours ({lv['label']})")
    return {"verdict": verdict, "reasons": reasons, **{k: v for k, v in req.items() if k != "fields"}}
