"""The computer-use loop: AT-SPI observe → Jev decides (typed) → uinput acts. Any app.

    forge do "in Firefox, search for 'lw-pla filament'"
    forge do "open Nautilus and go to Downloads"

One Jev request per cycle carries *speculative heads*: the operation plus a target for each
operation that needs one, each head offering only compatible elements. Whichever operation wins
already has its target — two decisions, one round trip. (Shape borrowed from
browser-use/jev-ultrafast, MIT; the body here is AT-SPI + uinput, so it works in every app, in
the windows you can see, not a hidden browser tab.)

Safety: he moves the real mouse. If the pointer wanders between steps (you grabbed it) he stops;
if the element moved or vanished since the decision, he re-observes instead of clicking blind.
`--dry` prints decisions and touches nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field

import httpx
from typesafe_sdk import Choice, Noul

from forge import config
from forge.decide import _wire
from forge.desktop import atspi

OPS = {
    "click": "click one of the clickable elements",
    "type": "type into one of the text fields, then press Enter",
    "key": "press a keyboard shortcut (address bar, new tab, escape, enter, find)",
    "scroll": "scroll down inside the window to reveal more",
    "focus": "switch to a different window",
    "launch": "the app needed isn't open — launch it",
    "done": "the goal is already achieved",
    "stuck": "cannot make progress from here",
}
KEYS = {"ctrl+l": "focus the address/search bar", "ctrl+t": "new browser tab", "escape": "dismiss",
        "enter": "confirm", "ctrl+f": "find in page", "f5": "reload", "ctrl+w": "close tab"}
URL_RE = re.compile(r"\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?)", re.IGNORECASE)
SITE_RE = re.compile(r"\b(?:go to|open|navigate to|visit)\s+([a-z0-9][a-z0-9-]{1,40})\b", re.IGNORECASE)
QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{1,120})[\"'“”‘’]")
APPS = {"firefox": "firefox", "files": "nautilus", "nautilus": "nautilus", "terminal": "ptyxis",
        "settings": "gnome-control-center", "chrome": "google-chrome"}


@dataclass
class Result:
    ok: bool
    steps: list[str] = field(default_factory=list)
    window: str = ""
    note: str = ""


def _jev(cfg: config.DecideCfg, state, questions: dict) -> dict:
    r = httpx.post(f"{cfg.adapter_base_url.rstrip('/').removesuffix('/v1')}/alpha/decisions",
                   timeout=15.0,
                   headers={"Authorization": f"Bearer {os.environ[cfg.adapter_key_env]}"},
                   json={"model": cfg.openrouter_slug, "state": state,
                         "questions": {k: _wire(v) for k, v in questions.items()},
                         "session_id": "forge-desktop"})
    r.raise_for_status()
    return r.json()["answers"]


def _goal_url(goal: str) -> str | None:
    if m := URL_RE.search(goal):
        u = m.group(1)
        return u if u.startswith("http") else "https://" + u
    if m := SITE_RE.search(goal):
        name = m.group(1).lower()
        if name not in APPS:
            return f"https://www.{name}.com"
    return None


def _fill_value(cfg: config.Config, goal: str, field_desc: str) -> str:
    """Only reached when the operation is `type`. Quoted text in the goal wins; an address bar
    gets the URL; otherwise a small model writes the value and nothing else."""
    if m := QUOTED.search(goal):
        return m.group(1)
    low = field_desc.lower()
    if ("address" in low or "url" in low or "location" in low) and (u := _goal_url(goal)):
        return u
    from forge.compose import _ollama, _openrouter
    msgs = [{"role": "system", "content": "Reply with ONLY the exact text to type. No quotes, no prose."},
            {"role": "user", "content": f"Goal: {goal}\nField: {field_desc}\nWhat should be typed?"}]
    out = _ollama(cfg.compose, msgs) or _openrouter(cfg.compose, msgs) or ""
    return out.strip().strip('"').splitlines()[0] if out else goal


def _pointer_pos() -> tuple[int, int]:
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    _, x, y = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
    return x, y


def _questions(els, wins, win) -> dict:
    """Speculative heads: every target head holds only elements that operation can act on."""
    clickable = [e for e in els if not e.fillable]
    fillable = [e for e in els if e.fillable]
    ops = {k: v for k, v in OPS.items()
           if not (k == "click" and not clickable) and not (k == "type" and not fillable)
           and not (k == "focus" and len(wins) < 2)}
    q = {"done": Noul(instructions="The goal is fully achieved as things stand."),
         "operation": Choice(instructions="Best next operation toward the goal.", criteria=ops),
         "key": Choice(instructions="If pressing a shortcut, which one.", criteria=KEYS)}
    if clickable:
        q["click_target"] = Choice(instructions="If clicking, which element.",
                                   criteria={str(e.i): e.desc() for e in clickable})
    if fillable:
        q["type_target"] = Choice(instructions="If typing, which field.",
                                  criteria={str(e.i): e.desc() for e in fillable})
    if len(wins) > 1:
        q["window"] = Choice(instructions="If switching windows, which one.",
                             criteria={str(i): f"{w.app}: {w.title}" for i, w in enumerate(wins)
                                       if w is not win})
    return q


def run(goal: str, cfg: config.Config | None = None, max_steps: int = 12, dry: bool = False) -> Result:
    from forge.desktop.uinput import Keyboard, Pointer, desktop_size

    cfg = cfg or config.load()
    res = Result(ok=False)
    ptr = kb = None
    if not dry:
        dw, dh = desktop_size()
        ptr, kb = Pointer(dw, dh), Keyboard()
    last = None
    expected_ptr = None
    stale_retries = 0
    try:
        for _ in range(max_steps):
            if expected_ptr and not dry:
                px, py = _pointer_pos()
                if abs(px - expected_ptr[0]) > 40 or abs(py - expected_ptr[1]) > 40:
                    res.note = "you took the mouse — stopping"
                    return res
            wins = atspi.windows()
            win = next((w for w in wins if w.active), None) or (wins[0] if wins else None)
            if win is None:
                res.note = "no windows visible to AT-SPI (is toolkit-accessibility on? app relaunched?)"
                return res
            if not win.shell_id:
                res.note = ("can't place windows on screen — enable the forge-windows GNOME "
                            "extension (contrib/gnome-extension) and log out/in once")
                return res
            els = atspi.elements(win, limit=70)
            res.window = f"{win.app}: {win.title}"
            state = {"goal": goal, "active_window": res.window,
                     "other_windows": [f"{w.app}: {w.title}" for w in wins if w is not win][:12],
                     "steps_so_far": res.steps[-6:],
                     "elements": {str(e.i): e.desc() for e in els}}
            a = _jev(cfg.decide, json.dumps(state), _questions(els, wins, win))
            op = a["operation"]["choice"] if "operation" in a else "stuck"
            if a["done"]["noul"] > 0.7 or op == "done":
                res.ok = True
                return res
            if op == "stuck":
                res.note = "stuck"
                return res

            before = (len(els), tuple((e.role, e.name) for e in els[:20]))
            step = None

            if op == "launch":
                m = SITE_RE.search(goal)
                exe = APPS.get((m.group(1).lower() if m else ""), None) or ("firefox" if _goal_url(goal) else None)
                if not exe:
                    res.note = "launch chosen but no known app in goal"
                    return res
                step = f"launch {exe}"
                if not dry:
                    args = [exe] + ([_goal_url(goal)] if exe == "firefox" and _goal_url(goal) else [])
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(3.0)

            elif op == "focus" and "window" in a:
                w = wins[int(a["window"]["choice"])]
                step = f"focus {w.app}: {w.title}"
                if not dry:
                    if not (w.shell_id and atspi.activate_window(w.shell_id)):
                        ptr.click(w.x + w.w // 2, w.y + 8)
                        expected_ptr = (w.x + w.w // 2, w.y + 8)
                    time.sleep(0.5)

            elif op == "key":
                k = a["key"]["choice"]
                step = f"key {k}"
                if not dry:
                    kb.tap(k)
                    atspi.wait_settled(win, before, cap_ms=400)

            elif op == "scroll":
                step = "scroll"
                if not dry:
                    ptr.move(win.x + win.w // 2, win.y + win.h // 2)
                    ptr.wheel(5)
                    expected_ptr = (win.x + win.w // 2, win.y + win.h // 2)
                    atspi.wait_settled(win, before, cap_ms=400)

            else:  # click / type — take the matching speculative head
                head = "type_target" if op == "type" else "click_target"
                if head not in a:
                    res.note = f"{op} chosen but no {head} offered"
                    return res
                tgt = next((e for e in els if str(e.i) == a[head]["choice"]), None)
                if tgt is None:
                    res.note = "no target"
                    return res
                if (op, tgt.desc()) == last:
                    res.note = f"looping on {tgt.desc()}"
                    return res
                last = (op, tgt.desc())

                if not dry:
                    tgt = atspi.fresh(tgt, win)
                    if tgt is None:                      # page moved under the decision
                        stale_retries += 1
                        if stale_retries > 2:
                            res.note = "page keeps changing under me"
                            return res
                        res.steps.append("re-observe (target moved)")
                        continue
                    stale_retries = 0

                if op == "type":
                    val = _fill_value(cfg, goal, tgt.desc())
                    step = f"type into '{tgt.desc()}': {val!r} + Enter"
                    if not dry:
                        ptr.click(tgt.cx, tgt.cy)
                        expected_ptr = (tgt.cx, tgt.cy)
                        time.sleep(0.15)
                        kb.type_text(val, enter=True)
                        # a combobox that pops suggestions gets its moment, a fast one costs 50ms
                        atspi.wait_settled(win, before, cap_ms=600)
                else:
                    step = f"click '{tgt.desc()}'"
                    if not dry:
                        ptr.click(tgt.cx, tgt.cy)
                        expected_ptr = (tgt.cx, tgt.cy)
                        atspi.wait_settled(win, before, cap_ms=600)

            res.steps.append(step)
        res.note = "step limit"
        return res
    finally:
        for dev in (ptr, kb):
            if dev:
                dev.close()
