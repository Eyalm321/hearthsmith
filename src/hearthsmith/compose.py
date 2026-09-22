"""T2/T3: turn a decision into the blacksmith's line. Ornith on the Mac first, OpenRouter when
the Mac is asleep, a template when both are down. Prose never blocks a nag."""

from __future__ import annotations

import os
import re

import httpx

from hearthsmith.config import ComposeCfg

TEMPLATES = {
    "now": "The forge is cold and '{title}' is overdue. Back to the anvil.",
    "soon": "'{title}' is heating up. Strike while the iron's hot.",
    "ignorable": "The bellows are quiet. '{title}' can wait a little.",
}


def _prompt(cfg: ComposeCfg, state: str, title: str, urgency: str) -> list[dict]:
    return [
        {"role": "system", "content": cfg.persona},
        {"role": "user", "content":
            f"Situation:\n{state}\n\nTask to nag about: {title}\nUrgency: {urgency}\n\n"
            "Write the one thing you'd say to the user right now."},
    ]


def _ollama(cfg: ComposeCfg, messages: list[dict]) -> str | None:
    try:
        r = httpx.post(f"{cfg.ollama_url}/api/chat", timeout=60.0,
                       # think:false — Ornith reasons first by default and a long state paragraph
                       # burns the whole token budget before the actual line
                       json={"model": cfg.ollama_model, "messages": messages, "stream": False,
                             "think": False,
                             "options": {"temperature": 0.8, "num_predict": 300}})
        r.raise_for_status()
        return r.json()["message"]["content"].strip() or None
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _openrouter(cfg: ComposeCfg, messages: list[dict], model: str | None = None,
                max_tokens: int = 300, reasoning: bool = True) -> str | None:
    key = os.environ.get(cfg.fallback_key_env)
    if not key:
        return None
    body: dict = {"model": model or cfg.fallback_model, "messages": messages, "max_tokens": max_tokens}
    if not reasoning:
        body["reasoning"] = {"enabled": False}  # thinking models: 4x slower for a summary
    try:
        r = httpx.post(f"{cfg.fallback_base_url}/chat/completions", timeout=45.0,
                       headers={"Authorization": f"Bearer {key}"}, json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip() or None
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        return None


def _trim(text: str) -> str:
    """Cut a mid-sentence tail if the model hit its token cap."""
    text = text.strip().strip('"')
    if text and text[-1] not in ".!?…" and (m := re.search(r"^(.*[.!?…])[^.!?…]*$", text, re.DOTALL)):
        return m.group(1)
    return text


def compose(cfg: ComposeCfg, state: str, title: str, urgency: str,
            want_llm: bool = True) -> tuple[str, str]:
    """Returns (text, tier) where tier ∈ ornith | openrouter | template."""
    if want_llm:
        msgs = _prompt(cfg, state, title, urgency)
        if text := _ollama(cfg, msgs):
            return _trim(text), "ornith"
        if text := _openrouter(cfg, msgs):
            return _trim(text), "openrouter"
    return TEMPLATES.get(urgency, TEMPLATES["ignorable"]).format(title=title), "template"
