"""The computer-use loop: AT-SPI observe → Jev decides (typed) → uinput acts. Any app.

    forge do "in Firefox, search for 'lw-pla filament'"
    forge do "open Nautilus and go to Downloads"

One Jev request per cycle carries *speculative heads*: the operation plus a target for each
operation that needs one, each head offering only compatible elements. Whichever operation wins
already has its target — two decisions, one round trip. (Shape borrowed from
browser-use/jev-ultrafast, MIT; the body here is AT-SPI + uinput, so it works in every app, in
the windows you can see, not a hidden browser tab.)

Quiet by default: he activates widgets through AT-SPI (`doAction`) and fills fields through
EditableText, so nothing touches your mouse or keyboard and you can keep working in another
window while he works in his. Only when a widget exposes no action does he need the real
pointer — and then only with `--hands` (or `desktop.hands: true`), which is genuinely exclusive:
you two share one cursor. With `--hands` he also stops the moment the pointer wanders off where
he left it. Either way, if an element moved or vanished since the decision he re-observes
instead of acting blind. `--dry` prints decisions and touches nothing.
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
    "navigate": "open the web address this goal implies, straight in the browser — preferred over "
                "typing an address or clicking through a search box",
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


# "go to google" is a site; "open Downloads" is a folder. Only bare names we actually know
# become URLs — everything else needs a dot or falls through to a search.
KNOWN_SITES = {"google": "google.com", "youtube": "youtube.com", "github": "github.com",
               "reddit": "reddit.com", "wikipedia": "wikipedia.org", "amazon": "amazon.com",
               "openrouter": "openrouter.ai", "huggingface": "huggingface.co", "x": "x.com",
               "twitter": "x.com", "gmail": "mail.google.com", "claude": "claude.ai",
               "openai": "openai.com", "anthropic": "anthropic.com", "hackernews": "news.ycombinator.com"}


def _goal_url(goal: str) -> str | None:
    if m := URL_RE.search(goal):
        u = m.group(1)
        return u if u.startswith("http") else "https://" + u
    if m := SITE_RE.search(goal):
        name = m.group(1).lower()
        if name in KNOWN_SITES and name not in APPS:
            return f"https://{KNOWN_SITES[name]}"
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


SEARCH_RE = re.compile(r"\bsearch(?:\s+\w+)?\s+for\s+(.+)$", re.IGNORECASE)
STOPWORDS = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:show me|find me|find|look up|"
                       r"search for|search|open|go to|navigate to|visit|pull up|bring up)\s+",
                       re.IGNORECASE)
WEBBY = re.compile(r"\b(show me|find|look up|search|open|browse|website|page|docs?|on \w+)\b",
                   re.IGNORECASE)


def _navigate_url(goal: str, cfg: config.Config | None = None) -> str | None:
    """A destination for the goal. Order: an address in the text, a quoted/explicit search, then
    — rather than fall back to typing in the address bar and guessing what submits it — a plain
    web search for what was asked. Getting to a results page is always progress; typing blind
    isn't."""
    from urllib.parse import quote_plus
    if u := _goal_url(goal):
        return u
    if m := (SEARCH_RE.search(goal) or QUOTED.search(goal)):
        q = m.group(1).strip().strip("'\"“”‘’")
        return f"https://www.google.com/search?q={quote_plus(q)}"
    names_local_app = any(re.search(rf"\b{re.escape(n)}\b", goal.lower()) for n in APPS
                          if n not in ("chrome", "firefox"))
    if WEBBY.search(goal) and not names_local_app:
        q = STOPWORDS.sub("", goal).strip(" .?!")
        if q:
            return f"https://www.google.com/search?q={quote_plus(q)}"
    return None


def _launch_target(goal: str) -> str | None:
    """Which executable the goal implies: a named app, else a browser if it names a site."""
    low = goal.lower()
    for name, exe in APPS.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return exe
    return "firefox" if _goal_url(goal) else None


def _pointer_pos() -> tuple[int, int] | None:
    """Only the shell can see the cursor on Wayland; None ⇒ we simply don't check for drift."""
    return atspi.shell_pointer()


ADDRESS_BAR = ("search with google or enter address", "address", "url", "location bar")


def _questions(els, wins, win, nav_url: str | None = None) -> dict:
    """Speculative heads: every target head holds only elements that operation can act on."""
    clickable = [e for e in els if not e.fillable]
    fillable = [e for e in els if e.fillable]
    if nav_url:
        # navigating is strictly better than typing an address: no keyboard, no submit guessing
        fillable = [e for e in fillable if not any(k in e.name.lower() for k in ADDRESS_BAR)]
    others = [w for w in wins if w is not win]
    ops = {k: v for k, v in OPS.items()
           if not (k == "click" and not clickable) and not (k == "type" and not fillable)
           and not (k == "focus" and not others)
           and not (k in ("scroll", "key") and win is None)
           and not (k == "navigate" and not nav_url)}
    q = {"done": Noul(instructions="The goal is fully achieved as things stand."),
         "operation": Choice(instructions="Best next operation toward the goal.", criteria=ops),
         "key": Choice(instructions="If pressing a shortcut, which one.", criteria=KEYS)}
    if clickable:
        q["click_target"] = Choice(instructions="If clicking, which element.",
                                   criteria={str(e.i): e.desc() for e in clickable})
    if fillable:
        q["type_target"] = Choice(instructions="If typing, which field.",
                                  criteria={str(e.i): e.desc() for e in fillable})
    if others:
        q["window"] = Choice(instructions="If switching windows, which one.",
                             criteria={str(i): f"{w.app}: {w.title}" for i, w in enumerate(wins)
                                       if w is not win})
    return q


def run(goal: str, cfg: config.Config | None = None, max_steps: int = 12, dry: bool = False,
        hands: bool | None = None) -> Result:
    cfg = cfg or config.load()
    hands = cfg.desktop.hands if hands is None else hands
    res = Result(ok=False)
    ptr = kb = None
    if not dry and hands:
        from forge.desktop.uinput import Keyboard, Pointer, desktop_size
        dw, dh = desktop_size()
        ptr, kb = Pointer(dw, dh), Keyboard()
    last = None
    expected_ptr = None
    stale_retries = 0
    try:
        for _ in range(max_steps):
            pos = _pointer_pos() if (expected_ptr and not dry) else None
            if pos and (abs(pos[0] - expected_ptr[0]) > 60 or abs(pos[1] - expected_ptr[1]) > 60):
                res.note = "you took the mouse — stopping"
                return res
            wins = atspi.windows()
            on_screen = atspi.shell_windows()
            win = next((w for w in wins if w.active), None) or (wins[0] if wins else None)
            if win is not None and not win.shell_id:
                res.note = ("can't place windows on screen — enable the forge-windows GNOME "
                            "extension (contrib/gnome-extension) and log out/in once")
                return res
            # No accessible window is not the end: the app he needs may simply not be open, or
            # what's on screen may not expose accessibility. Let Jev decide to launch/focus.
            els = atspi.elements(win, limit=70) if win is not None else []
            res.window = f"{win.app}: {win.title}" if win else "(nothing accessible)"
            opaque = [f"{w['app'] or w['wm_class']}: {w['title']}" for w in on_screen
                      if not any(x.title == w["title"] for x in wins)]
            state = {"goal": goal, "active_window": res.window,
                     "other_accessible_windows": [f"{w.app}: {w.title}" for w in wins if w is not win][:12],
                     "windows_on_screen_without_accessibility": opaque[:12],
                     "steps_so_far": res.steps[-6:],
                     "elements": {str(e.i): e.desc() for e in els}}
            nav_url = _navigate_url(goal, cfg)
            state["navigate_would_open"] = nav_url or "(no address in the goal)"
            a = _jev(cfg.decide, json.dumps(state), _questions(els, wins, win, nav_url))
            op = a["operation"]["choice"] if "operation" in a else "stuck"
            if a["done"]["noul"] > 0.7 or op == "done":
                res.ok = True
                return res
            if op == "stuck":
                res.note = "stuck"
                return res

            before = (len(els), tuple((e.role, e.name) for e in els[:20]))
            step = None
            if op in ("launch", "scroll", "key") and (op, a.get("key", {}).get("choice")) == last:
                res.note = f"{op} changed nothing — stopping" if not dry else f"dry: would {op}"
                return res
            if op in ("launch", "scroll", "key"):
                last = (op, a.get("key", {}).get("choice"))

            if op == "navigate":
                u = _navigate_url(goal)
                if not u:
                    res.note = "navigate chosen but no address or search in the goal"
                    return res
                if (op, u) == last:
                    res.note = "already navigated there"
                    return res
                last = (op, u)
                step = f"navigate {u}"
                if not dry:
                    subprocess.Popen(["xdg-open", u], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    for _ in range(25):
                        time.sleep(0.4)
                        w2 = next((x for x in atspi.windows() if x.app == (win.app if win else "")), None)
                        if w2 and w2.title != (win.title if win else ""):
                            break

            elif op == "launch":
                exe = _launch_target(goal)
                if not exe:
                    res.note = "launch chosen but I don't know which app that is"
                    return res
                step = f"launch {exe}"
                if not dry:
                    args = [exe] + ([_goal_url(goal)] if exe == "firefox" and _goal_url(goal) else [])
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    for _ in range(30):              # wait for it to register with AT-SPI
                        time.sleep(0.4)
                        if any(exe.split("-")[0] in w.app.lower() for w in atspi.windows()):
                            break

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
                    if not kb:
                        res.note = f"'{k}' needs the keyboard — rerun with --hands"
                        return res
                    kb.tap(k)
                    atspi.wait_settled(win, before, cap_ms=400)

            elif op == "scroll":
                step = "scroll"
                if not dry:
                    if not ptr:
                        res.note = "scrolling needs the mouse — rerun with --hands"
                        return res
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
                    step = f"type into '{tgt.desc()}': {val!r}"
                    if not dry:
                        if atspi.set_text(tgt, val):                    # quiet: no keyboard
                            if atspi.submit_near(tgt, els):
                                step += " + Search"
                            elif kb:
                                kb.tap("enter")
                                step += " + Enter"
                            else:
                                step += " (typed; nothing to submit it — may need --hands)"
                        elif ptr and kb:
                            ptr.click(tgt.cx, tgt.cy)
                            expected_ptr = (tgt.cx, tgt.cy)
                            time.sleep(0.15)
                            kb.type_text(val, enter=True)
                            step += " + Enter (mouse)"
                        else:
                            res.note = f"'{tgt.desc()}' won't take text quietly — rerun with --hands"
                            return res
                        # a combobox that pops suggestions gets its moment, a fast one costs 50ms
                        atspi.wait_settled(win, before, cap_ms=600)
                else:
                    step = f"click '{tgt.desc()}'"
                    if not dry:
                        if atspi.do_action(tgt):                        # quiet: no pointer
                            pass
                        elif ptr:
                            ptr.click(tgt.cx, tgt.cy)
                            expected_ptr = (tgt.cx, tgt.cy)
                            step += " (mouse)"
                        else:
                            res.note = (f"'{tgt.desc()}' has no accessible action — rerun with "
                                        "--hands to let me use the mouse")
                            return res
                        atspi.wait_settled(win, before, cap_ms=600)

            res.steps.append(step)
        res.note = "step limit"
        return res
    finally:
        for dev in (ptr, kb):
            if dev:
                dev.close()
