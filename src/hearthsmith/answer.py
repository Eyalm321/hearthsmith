"""Turn what is on screen into an answer.

Two paths, because two kinds of question:

* one page holds it ("what does this cost", "is it in stock") — read the page and say it, which
  costs a couple of seconds and no extra agent;
* it needs comparing sources ("what do people charge on average") — that is a research job, so
  it goes to an agent with real research tooling and the answer is collected when it lands.

Both end the same way: an answer attached to the task, in his own voice.
"""

from __future__ import annotations

import re

from hearthsmith import config

# A question, not an errand. "find me X" is an errand; "how much is X" wants a number back.
QUESTION = re.compile(r"^\s*(who|what|what's|whats|when|where|why|how|which|is|are|does|do|did|"
                      r"can|should|would|will)\b|\?\s*$", re.IGNORECASE)
# Needs more than one page: comparisons, averages, opinions, surveys of a field.
DEEP = re.compile(r"\b(average|typical|compare|comparison|versus|vs\.?|best|worst|options|"
                  r"alternatives|pros and cons|market|going rate|charge|pricing across|"
                  r"reviews|consensus|research|survey|landscape)\b", re.IGNORECASE)


def is_question(text: str) -> bool:
    return bool(QUESTION.search(text.strip()))


def wants_research(text: str) -> bool:
    """Something one page cannot honestly answer. Not every such request is phrased as a
    question — "compare X and Y" is an instruction and still needs the same work."""
    return bool(DEEP.search(text))


# Facts that live in the world, not in a model's memory: prices, dates, versions, whatever a
# site says today. Asking these of the smith gets you a confident shrug at best.
LOOKUP = re.compile(r"\b(price|cost|how much|charge|fee|rate|release[ds]?|launched|version|"
                    r"latest|current|today|now|according to|on (?:wikipedia|github|amazon|"
                    r"youtube|reddit)|in stock|available|hours|open(?:ing)? time)\b",
                    re.IGNORECASE)


def needs_lookup(text: str) -> bool:
    return is_question(text) and bool(LOOKUP.search(text))


def from_page(cfg: config.Config, question: str, page_text: str, url: str = "") -> str | None:
    """Answer from what is actually on the page — and say so when it isn't there, rather than
    filling the gap from the model's own memory."""
    if not page_text or len(page_text.strip()) < 40:
        return None
    from hearthsmith.compose import _ollama, _openrouter, _trim
    msgs = [
        {"role": "system", "content":
            "Answer the question using ONLY the page text given. Two sentences at most. If the "
            "page does not contain the answer, say exactly what is missing instead of guessing. "
            "You are a gruff blacksmith; keep the voice, keep it short."},
        {"role": "user", "content": f"Question: {question}\nPage: {url}\n\n{page_text[:9000]}"},
    ]
    out = _ollama(cfg.compose, msgs) or _openrouter(cfg.compose, msgs)
    return _trim(out) if out else None
