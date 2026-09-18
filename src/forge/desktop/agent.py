"""The computer-use loop: AT-SPI observe → Jev decides (typed) → uinput acts. Any app.

    forge do "in Firefox, search for 'lw-pla filament'"
    forge do "open Nautilus and go to Downloads"

Safety: he moves the real mouse. If the pointer moves by itself between steps (you grabbed
it) he stops. Every action is logged; --dry prints decisions without touching anything."""

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

ACTIONS = {
    "click": "click one of the listed elements",
    "fill": "type into one of the listed text fields, then press Enter",
    "key": "press a keyboard shortcut (ctrl+l for the address bar, ctrl+t new tab, escape, enter)",
    "scroll": "scroll down inside the window",
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


def _fill_value(cfg: config.Config, goal: str, field_desc: str) -> str:
    if m := QUOTED.search(goal):
        return m.group(1)
    if "address" in field_desc.lower() or "url" in field_desc.lower():
        if u := _goal_url(goal):
            return u
    from forge.compose import _ollama, _openrouter
    msgs = [{"role": "system", "content": "Reply with ONLY the exact text to type. No quotes, no prose."},
            {"role": "user", "content": f"Goal: {goal}\nField: {field_desc}\nWhat should be typed?"}]
    out = _ollama(cfg.compose, msgs) or _openrouter(cfg.compose, msgs) or ""
    return out.strip().strip('"').splitlines()[0] if out else goal


def _goal_url(goal: str) -> str | None:
    if m := URL_RE.search(goal):
        u = m.group(1)
        return u if u.startswith("http") else "https://" + u
    if m := SITE_RE.search(goal):
        name = m.group(1).lower()
        if name not in APPS:
            return f"https://www.{name}.com"
    return None


def _pointer_pos() -> tuple[int, int]:
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    _, x, y = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
    return x, y


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
            q = {"done": Noul(instructions="The goal is fully achieved as things stand."),
                 "action": Choice(instructions="Best next action toward the goal.", criteria=ACTIONS),
                 "key": Choice(instructions="If pressing a shortcut, which one.", criteria=KEYS)}
            if els:
                q["target"] = Choice(instructions="Which element to act on, if clicking or filling.",
                                     criteria={str(e.i): e.desc() for e in els})
            if len(wins) > 1:
                q["window"] = Choice(instructions="If switching windows, which one.",
                                     criteria={str(i): f"{w.app}: {w.title}" for i, w in enumerate(wins)})
            a = _jev(cfg.decide, json.dumps(state), q)
            if a["done"]["noul"] > 0.7 or a["action"]["choice"] == "done":
                res.ok = True
                return res
            act = a["action"]["choice"]
            if act == "stuck":
                res.note = "stuck"
                return res
            step = None
            if act == "launch":
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
            elif act == "focus" and "window" in a:
                w = wins[int(a["window"]["choice"])]
                step = f"focus {w.app}: {w.title}"
                if not dry:
                    if not (w.shell_id and atspi.activate_window(w.shell_id)):
                        ptr.click(w.x + w.w // 2, w.y + 8)  # fallback: titlebar click
                        expected_ptr = (w.x + w.w // 2, w.y + 8)
                    time.sleep(0.6)
            elif act == "key":
                k = a["key"]["choice"]
                step = f"key {k}"
                if not dry:
                    kb.tap(k)
                    time.sleep(0.5)
            elif act == "scroll":
                step = "scroll"
                if not dry:
                    ptr.move(win.x + win.w // 2, win.y + win.h // 2)
                    ptr.wheel(5)
                    expected_ptr = (win.x + win.w // 2, win.y + win.h // 2)
                    time.sleep(0.5)
            else:
                tgt = next((e for e in els if str(e.i) == a.get("target", {}).get("choice")), None)
                if tgt is None:
                    res.note = "no target"
                    return res
                if (act, tgt.desc()) == last:
                    res.note = f"looping on {tgt.desc()}"
                    return res
                last = (act, tgt.desc())
                if act == "fill" or (act == "click" and tgt.fillable):
                    val = _fill_value(cfg, goal, tgt.desc())
                    step = f"fill '{tgt.desc()}' = {val!r} + Enter"
                    if not dry:
                        ptr.click(tgt.cx, tgt.cy)
                        time.sleep(0.25)
                        kb.type_text(val, enter=True)
                        expected_ptr = (tgt.cx, tgt.cy)
                        time.sleep(1.2)
                else:
                    step = f"click '{tgt.desc()}'"
                    if not dry:
                        ptr.click(tgt.cx, tgt.cy)
                        expected_ptr = (tgt.cx, tgt.cy)
                        time.sleep(0.9)
            res.steps.append(step)
        res.note = "step limit"
        return res
    finally:
        for dev in (ptr, kb):
            if dev:
                dev.close()
