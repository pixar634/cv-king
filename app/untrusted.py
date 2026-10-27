"""Text from outside (job postings, web pages, application forms) goes to the model as data, never as instructions.

Every brief puts such text between <<< and >>>, and the system prompt (engines.SYSTEM) says what that means. `fence`
makes sure the text can't end its own block early or hide characters; `signals` finds lines in it that speak to an
AI, so the person sees them too."""
import re

# Zero-width, direction and soft-hyphen characters: invisible on the page, readable by a model.
INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤﻿]")

# Phrases that address an AI rather than a candidate. Each needs the AI to be the one spoken to, so an ordinary
# AI job ad ("As an AI engineer you will...") is never flagged.
AIMED_AT_AI = re.compile("|".join([
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|prompts?|rules|guidelines)\b",
    r"\bignore (all |everything )?(the )?above\b",
    r"\b(system prompt|developer message|jailbreak|prompt injection)\b",
    r"\bif you(?:'re| are)\s+(?:an?\s+)?(ai|llm|language model|chatbot|ai assistant|chatgpt|claude|gemini)\b",
    r"\b(you are|act as|pretend to be)\s+(?:an?\s+)?(ai|llm|large language model|language model|chatbot)\b"
    r"(?!\s+(engineer|researcher|scientist|developer|architect|lead|specialist|expert))",
    r"\bas an ai language model\b",
    r"\b(ai|llm|language model|chatgpt|claude|gemini|gpt)s?\b[^.\n]{0,30}\b(reading|reviewing|processing|screening|parsing)\b"
    r"[^.\n]{0,20}\b(this|these)\b",
    r"\bnote to (the |any )?(ai|llm|model|reviewer|screener)\b",
]), re.I)


def fence(text: str) -> str:
    """The text, safe to put between <<< and >>>: no invisible characters, and no markers of its own."""
    text = INVISIBLE.sub("", text or "")
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")


def signals(text: str, limit: int = 3) -> list[str]:
    """Lines of the text that address an AI, and whether it hides characters: for the person to see."""
    out = []
    for line in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        line = line.strip()
        if AIMED_AT_AI.search(line):
            out.append(line[:200])
            if len(out) == limit:
                break
    hidden = len(INVISIBLE.findall(text or ""))
    if hidden >= 3:
        out.append(f"The text hides {hidden} invisible characters.")
    return out
