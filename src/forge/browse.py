"""Jev drives YOUR Firefox. He opens a tab in the browser you already have open; Firefox lists
what's on the page; Jev picks the next action as a typed decision; Firefox does it. You can
watch, and take over at any point — it's a normal tab. No chat model in the loop; the only LLM
call is for a fill value when the goal doesn't contain it in quotes.

    forge browse "go to google.com and search for 'lw-pla filament'"

Needs Firefox started with --remote-debugging-port 9222 (contrib/firefox.desktop). Without it
he still opens the URL for you (firefox --new-tab) and says so.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from typesafe_sdk import Choice, Noul

from forge import config
from forge.decide import _wire
from forge.firefox import Firefox, open_in_firefox

ACTIONS = {
    "goto": "load a URL (the goal names a site or address)",
    "click": "click one of the listed elements",
    "fill": "type into one of the listed text fields, then press Enter",
    "scroll": "scroll down to reveal more of the page",
    "done": "the goal is already achieved on this page",
    "stuck": "the goal cannot be achieved from here",
}
URL_RE = re.compile(r"\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?)", re.IGNORECASE)
QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{1,120})[\"'“”‘’]")


@dataclass
class Result:
    ok: bool
    steps: list[str] = field(default_factory=list)
    url: str = ""
    title: str = ""
    note: str = ""


def _jev(cfg: config.DecideCfg, state, questions: dict) -> dict:
    r = httpx.post(f"{cfg.adapter_base_url.rstrip('/').removesuffix('/v1')}/alpha/decisions",
                   timeout=15.0,
                   headers={"Authorization": f"Bearer {os.environ[cfg.adapter_key_env]}"},
                   json={"model": cfg.openrouter_slug, "state": state,
                         "questions": {k: _wire(v) for k, v in questions.items()},
                         "session_id": "forge-browse"})
    r.raise_for_status()
    return r.json()["answers"]


def _fill_value(cfg: config.Config, goal: str, field_desc: str) -> str:
    """Prefer text quoted in the goal; else ask the compose model for just the value."""
    if m := QUOTED.search(goal):
        return m.group(1)
    from forge.compose import _ollama, _openrouter
    msgs = [{"role": "system", "content": "Reply with ONLY the exact text to type. No quotes, no prose."},
            {"role": "user", "content": f"Goal: {goal}\nField: {field_desc}\nWhat should be typed?"}]
    out = _ollama(cfg.compose, msgs) or _openrouter(cfg.compose, msgs) or ""
    return out.strip().strip('"').splitlines()[0] if out else goal


SITE_RE = re.compile(r"\b(?:go to|open|navigate to|visit|goto)\s+([a-z0-9][a-z0-9-]{1,40})\b", re.IGNORECASE)


def _goal_url(goal: str) -> str | None:
    if m := URL_RE.search(goal):
        u = m.group(1)
        return u if u.startswith("http") else "https://" + u
    if m := SITE_RE.search(goal):          # "go to google" → https://www.google.com
        return f"https://www.{m.group(1).lower()}.com"
    return None


def browse(goal: str, cfg: config.Config | None = None, max_steps: int = 12) -> Result:
    cfg = cfg or config.load()
    res = Result(ok=False)
    url = _goal_url(goal)
    ff = Firefox()
    if not ff.connect():
        if url and open_in_firefox(url):
            res.steps.append(f"opened {url} in Firefox")
            res.note = "Firefox has no remote port — opened the page, can't click around in it"
            res.url = url
            return res
        res.note = "Firefox isn't reachable (start it with --remote-debugging-port 9222)"
        return res

    try:
        tab = ff.new_tab(url or "about:blank")
        if url:
            res.steps.append(f"goto {url}")
            time.sleep(0.8)
        last_action = None
        for _ in range(max_steps):
            obs = ff.observe(tab, 60)
            cands = obs["candidates"]
            low = (obs["title"] + " " + obs["url"]).lower()
            if any(k in low for k in ("unusual traffic", "/sorry/", "captcha", "verify you are human")):
                res.note, res.url, res.title = "bot check — your turn", obs["url"], obs["title"]
                return res
            state = {"goal": goal, "url": obs["url"], "title": obs["title"],
                     "steps_so_far": res.steps[-6:],
                     "elements": {str(c["i"]): f"{c['role']}: {c['text'] or c['label']}"
                                  + (" [text field]" if c.get("fillable") else "") for c in cands}}
            q = {"done": Noul(instructions="The goal is fully achieved on the current page."),
                 "action": Choice(instructions="Best next action toward the goal.", criteria=ACTIONS)}
            if cands:
                q["target"] = Choice(instructions="Which element to act on, if clicking or filling.",
                                     criteria={str(c["i"]): f"{c['role']}: {c['text'] or c['label']}"
                                               for c in cands})
            a = _jev(cfg.decide, json.dumps(state), q)
            if a["done"]["noul"] > 0.7 or a["action"]["choice"] == "done":
                res.ok, res.url, res.title = True, obs["url"], obs["title"]
                return res
            act = a["action"]["choice"]
            if act == "stuck":
                res.note, res.url, res.title = "stuck", obs["url"], obs["title"]
                return res
            if act == "scroll":
                ff.scroll(tab, 700)
                res.steps.append("scroll")
                time.sleep(0.5)
                continue
            if act == "goto":
                if not url:
                    res.note = "goto chosen but no URL in goal"
                    return res
                ff.navigate(tab, url)
                res.steps.append(f"goto {url}")
                time.sleep(0.8)
                continue
            tgt = next((c for c in cands if str(c["i"]) == a.get("target", {}).get("choice")), None)
            if tgt is None:
                res.note = "no target"
                return res
            desc = f"{tgt['role']}: {tgt['text'] or tgt['label']}"
            if (act, desc) == last_action:
                res.note, res.url, res.title = f"looping on {desc}", obs["url"], obs["title"]
                return res
            last_action = (act, desc)
            if act == "fill" or (act == "click" and tgt.get("fillable")):
                val = _fill_value(cfg, goal, desc)
                ff.fill(tab, tgt["i"], val, enter=True)
                res.steps.append(f"fill '{desc}' = {val!r} + Enter")
                time.sleep(1.2)
            else:
                ff.click_idx(tab, tgt["i"])
                res.steps.append(f"click '{desc}'")
                time.sleep(1.0)
        res.note = "step limit"
        return res
    finally:
        ff.close()
