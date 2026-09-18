"""The computer-use loop: AT-SPI observe → Jev decides (typed) → uinput acts. Any app.

    hearthsmith do "in Firefox, search for 'lw-pla filament'"
    hearthsmith do "open Nautilus and go to Downloads"

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
from dataclasses import dataclass, field, replace

import httpx
from typesafe_sdk import Choice, Noul

from hearthsmith import config
from hearthsmith.decide import _wire
from hearthsmith.desktop import atspi

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
MAX_RECHECKS = 3         # corrections driven by the verifier before admitting defeat
BROWSERS = ("firefox", "chromium", "google-chrome", "chrome", "brave-browser")
APPS = {"firefox": "firefox", "files": "nautilus", "nautilus": "nautilus", "terminal": "ptyxis",
        "settings": "gnome-control-center", "chrome": "google-chrome"}


@dataclass
class Result:
    ok: bool
    steps: list[str] = field(default_factory=list)
    window: str = ""
    note: str = ""
    seen: str = ""          # what the verifier saw, when it could look


def _jev(cfg: config.DecideCfg, state, questions: dict) -> dict:
    r = httpx.post(f"{cfg.adapter_base_url.rstrip('/').removesuffix('/v1')}/alpha/decisions",
                   timeout=15.0,
                   headers={"Authorization": f"Bearer {os.environ[cfg.adapter_key_env]}"},
                   json={"model": cfg.openrouter_slug, "state": state,
                         "questions": {k: _wire(v) for k, v in questions.items()},
                         "session_id": "hearthsmith-desktop"})
    r.raise_for_status()
    return r.json()["answers"]


# "go to google" is a site; "open Downloads" is a folder. Only bare names we actually know
# become URLs — everything else needs a dot or falls through to a search.
KNOWN_SITES = {"google": "google.com", "youtube": "youtube.com", "github": "github.com",
               "reddit": "reddit.com", "wikipedia": "wikipedia.org", "amazon": "amazon.com",
               "openrouter": "openrouter.ai", "huggingface": "huggingface.co", "x": "x.com",
    "protonmail": "proton.me", "proton": "proton.me", "fastmail": "fastmail.com",
               "twitter": "x.com", "gmail": "mail.google.com", "claude": "claude.ai",
               "openai": "openai.com", "anthropic": "anthropic.com", "hackernews": "news.ycombinator.com"}


# Multi-word destinations, checked first: "google flights" is a place, not a search for the
# word "flights" on google.com.
NAMED_PLACES = {
    "google flights": "https://www.google.com/travel/flights",
    "google maps": "https://www.google.com/maps",
    "google drive": "https://drive.google.com",
    "google calendar": "https://calendar.google.com",
    "google docs": "https://docs.google.com",
    "hacker news": "https://news.ycombinator.com",
    "protonmail signup": "https://account.proton.me/signup",
    "proton signup": "https://account.proton.me/signup",
    "youtube music": "https://music.youtube.com",
}


def _goal_url(goal: str) -> str | None:
    low = goal.lower()
    for name, url in NAMED_PLACES.items():
        if name in low:
            return url
    if m := URL_RE.search(goal):
        u = m.group(1)
        return u if u.startswith("http") else "https://" + u
    if m := SITE_RE.search(goal):
        name = m.group(1).lower()
        if name in KNOWN_SITES and name not in APPS:
            return f"https://{KNOWN_SITES[name]}"
    return None


DATE_FIELD = re.compile(r"\b(departure|depart|return|date|check.?in|check.?out|when)\b", re.IGNORECASE)
DATE_IN_GOAL = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"(?:,?\s+\d{4})?|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:,?\s+\d{4})?)\b", re.IGNORECASE)


def _fill_value(cfg: config.Config, goal: str, field_desc: str, filled: dict | None = None) -> str:
    """Only reached when the operation is `type`. A quoted phrase, an address for the address
    bar, a date for a date field; otherwise a small model writes the value — and it is told what
    has already been entered, or it happily puts the origin city in the departure date."""
    filled = filled or {}
    from hearthsmith import ask as asker
    if kind := asker.sensitive(field_desc):
        answer = asker.ask(field_desc, secret=kind == "secret")
        if answer:
            return answer
        raise PermissionError(f"{field_desc}: needs you")
    low = field_desc.lower()
    if ("address" in low or "url" in low or "location" in low) and (u := _goal_url(goal)):
        return u
    if DATE_FIELD.search(low) and (m := DATE_IN_GOAL.search(goal)):
        return m.group(1)
    if (m := QUOTED.search(goal)) and m.group(1) not in filled.values():
        return m.group(1)
    from hearthsmith.compose import _ollama, _openrouter
    done = ("Already entered: " + "; ".join(f"{k} = {v}" for k, v in filled.items())
            if filled else "Nothing entered yet.")
    msgs = [{"role": "system", "content": "Reply with ONLY the exact text to type in the named "
             "field. No quotes, no prose, never repeat a value already entered elsewhere."},
            {"role": "user", "content": f"Goal: {goal}\n{done}\nField to fill: {field_desc}\n"
                                        "What should be typed?"}]
    out = _ollama(cfg.compose, msgs) or _openrouter(cfg.compose, msgs) or ""
    return out.strip().strip('"').splitlines()[0] if out else goal


SEARCH_RE = re.compile(r"\bsearch(?:\s+\w+)?\s+for\s+(.+)$", re.IGNORECASE)
STOPWORDS = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:show me|find me|find|look up|"
                       r"search for|search|open|go to|navigate to|visit|pull up|bring up)\s+",
                       re.IGNORECASE)
WEBBY = re.compile(r"\b(show me|find|look up|search|open|browse|website|page|docs?|on \w+)\b",
                   re.IGNORECASE)


ENGINES = {"google.com", "bing.com", "duckduckgo.com", "search.brave.com"}
SITE_SEARCH = {"github.com": "https://github.com/search?q={q}&type=repositories",
               "youtube.com": "https://www.youtube.com/results?search_query={q}",
               "reddit.com": "https://www.reddit.com/search/?q={q}",
               "openrouter.ai": "https://openrouter.ai/models?q={q}",
               "huggingface.co": "https://huggingface.co/search/full-text?q={q}"}
NOISE = re.compile(r"\b(?:go to|open|show me|find|look up|visit|navigate to|the|a|an|on|in|please|"
                   r"repo|repository|page|site|website)\b", re.IGNORECASE)


SIGNUP = {"proton.me": "https://account.proton.me/signup",
          "fastmail.com": "https://www.fastmail.com/signup/",
          "github.com": "https://github.com/signup"}
SIGNUP_RE = re.compile(
    # people type "adress", and "make me an account" is the same request as "register"
    r"\b(register|sign ?up|signup|"
    r"(?:create|make|open|set ?up|get) (?:me )?(?:a |an )?(?:new )?(?:email |e-?mail )?"
    r"(?:acc?ount|add?ress|inbox|mailbox)|"
    r"new (?:email |e-?mail )?(?:add?ress|acc?ount))\b", re.IGNORECASE)


def _leftover(goal: str, domain: str) -> str:
    """What the goal asks for beyond the site itself — 'github.com openshorts repo' → 'openshorts'.
    The site's own names count as the site, not as something to search for."""
    rest = goal.replace(domain, " ")
    for name, host in KNOWN_SITES.items():
        if host == domain:
            rest = re.sub(rf"\b{re.escape(name)}\b", " ", rest, flags=re.IGNORECASE)
    rest = NOISE.sub(" ", rest)
    return " ".join(rest.split()).strip(" .?!,")


def _navigate_url(goal: str, cfg: config.Config | None = None, in_browser: bool = False) -> str | None:
    """A destination for the goal. Order: an address in the text, a quoted/explicit search, then
    — rather than fall back to typing in the address bar and guessing what submits it — a plain
    web search for what was asked. Getting to a results page is always progress; typing blind
    isn't."""
    from urllib.parse import quote_plus

    def search_on(host: str | None, q: str) -> str:
        if host in ENGINES:            # searching "on google" is just searching
            host = None
        if host and (tmpl := SITE_SEARCH.get(host)):
            return tmpl.format(q=quote_plus(q))
        if host:
            return f"https://www.google.com/search?q={quote_plus(q)}+site:{host}"
        return f"https://www.google.com/search?q={quote_plus(q)}"

    u = _goal_url(goal)
    if u and any(n in goal.lower() for n in NAMED_PLACES):
        return u
    host = u.split("//", 1)[-1].split("/")[0].removeprefix("www.") if u else None
    if host is None:                   # "search youtube for X" names a site without an address
        host = next((d for n, d in KNOWN_SITES.items()
                     if re.search(rf"\b{re.escape(n)}\b", goal.lower())), None)
    # A signup errand has a page of its own — searching a site for the word "register" lands
    # nowhere useful, and this is the one destination worth being sure about.
    if host and SIGNUP_RE.search(goal) and (page := SIGNUP.get(host)):
        return page
    deep = bool(u and "/" in u.split("//", 1)[-1])      # a full path — open it as given

    # 1. an explicit query ("search for X", or quoted text) wins, scoped to the named site
    if not deep and (m := (SEARCH_RE.search(goal) or QUOTED.search(goal))):
        q = m.group(1).strip().strip("'\"“”‘’")
        if host and host in q.lower():
            q = re.sub(re.escape(host), "", q, flags=re.IGNORECASE).strip()
        if q:
            return search_on(host, q)
    if u:
        # 2. a domain plus other words means "find this on that site", not "open the homepage" —
        #    otherwise every cycle reopens the front page in a new tab
        rest = "" if deep else _leftover(goal, host or "")
        return search_on(host, rest) if rest else u
    # 3. nothing addressable, but clearly a web errand → just search for what was asked
    names_local_app = any(re.search(rf"\b{re.escape(n)}\b", goal.lower()) for n in APPS
                          if n not in ("chrome", "firefox"))
    if (WEBBY.search(goal) or in_browser) and not names_local_app:
        q = STOPWORDS.sub("", goal).strip(" .?!")
        if host:                       # "... on openrouter" → search openrouter, not the web
            q = re.sub(r"\bon\s+\w+\s*$", "", q, flags=re.IGNORECASE).strip()
            q = next((re.sub(rf"\b{re.escape(n)}\b", "", q, flags=re.IGNORECASE).strip()
                      for n, d in KNOWN_SITES.items() if d == host), q)
        if q:
            return search_on(host, q)
    return None


# Controls that mean "your edit is not applied yet". A decider looking at a filled-in form
# happily calls it finished while the date picker is still open over it.
COMMIT = re.compile(r"^(done|ok|apply|update|save|search|search flights|go|submit|confirm)$",
                    re.IGNORECASE)


def _pending_commit(els) -> object | None:
    return next((e for e in els if not e.fillable and e.name and COMMIT.match(e.name.strip())), None)


LOOKUP = re.compile(r"\b(look (?:for|up)|show me|find|search|what(?:'s| is)|how much|price of)\b",
                    re.IGNORECASE)


def _answered_by(goal: str, title: str) -> bool:
    """A 'look for flights ORF to NYC' errand is done when the page is showing that — the
    decider otherwise keeps hunting for one more click on a page that already answers it."""
    if not LOOKUP.search(goal):
        return False
    words = {w for w in re.findall(r"[a-z0-9]{3,}", STOPWORDS.sub("", goal).lower())
             if w not in ("for", "the", "and", "from")}
    if not words:
        return False
    low = title.lower()
    hit = sum(1 for w in words if w in low)
    return hit >= max(2, len(words) // 2)


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
KEYBOARD_NOTE = ("uinput types by pasting, so a page that listens for real keystrokes "
                 "(most autocompletes) still won't see them")


def _questions(els, wins, win, nav_url: str | None = None, hands: bool = False,
               spent: dict | None = None) -> dict:
    """Speculative heads: every target head holds only elements that operation can act on."""
    clickable = [e for e in els if not e.fillable]
    fillable = [e for e in els if e.fillable]
    # The address bar is never the right target: `navigate` opens URLs without typing, and a
    # search box on the page is a better target for a query.
    fillable = [e for e in fillable if not any(k in e.name.lower() for k in ADDRESS_BAR)]
    others = [w for w in wins if w is not win]
    ops = {k: v for k, v in OPS.items()
           if not (k == "click" and not clickable) and not (k == "type" and not fillable)
           and not (k == "focus" and not others)
           and not (k in ("scroll", "key") and win is None)
           # quiet mode has no keyboard or mouse wheel; offering them only leads to a dead end
           and not (k in ("key", "scroll") and not hands)
           and not (k == "navigate" and not nav_url)}
    q = {"done": Noul(instructions="The goal is fully achieved as things stand."),
         "operation": Choice(instructions="Best next operation toward the goal.", criteria=ops)}
    if hands:
        q["key"] = Choice(instructions="If pressing a shortcut, which one.", criteria=KEYS)
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
    from hearthsmith.progress import Narrator
    say = Narrator(cfg)
    res = Result(ok=False)
    ptr = kb = None
    if not dry and hands:
        from hearthsmith.desktop.uinput import Keyboard, Pointer, desktop_size
        dw, dh = desktop_size()
        ptr, kb = Pointer(dw, dh), Keyboard()
    last = None
    expected_ptr = None
    stale_retries = 0
    visited: list[str] = []
    filled: dict[str, str] = {}
    commits = 0
    verified = 0
    spent: dict[str, int] = {}
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
                res.note = ("can't place windows on screen — enable the hearthsmith-windows GNOME "
                            "extension (contrib/gnome-extension) and log out/in once")
                return res
            # No accessible window is not the end: the app he needs may simply not be open, or
            # what's on screen may not expose accessibility. Let Jev decide to launch/focus.
            els = atspi.elements(win, limit=70) if win is not None else []
            if win is not None:
                # the tab you're already on is not a destination — clicking it does nothing and
                # looks like progress to a decider comparing titles
                els = [e for e in els if not (e.role == "page tab" and e.name
                                              and win.title.startswith(e.name))]
                els = [replace(e, i=i) for i, e in enumerate(els)]
            res.window = f"{win.app}: {win.title}" if win else "(nothing accessible)"
            opaque = [f"{w['app'] or w['wm_class']}: {w['title']}" for w in on_screen
                      if not any(x.title == w["title"] for x in wins)]
            state = {"goal": goal, "active_window": res.window,
                     "other_accessible_windows": [f"{w.app}: {w.title}" for w in wins if w is not win][:12],
                     "windows_on_screen_without_accessibility": opaque[:12],
                     "steps_so_far": res.steps[-6:],
                     "elements": {str(e.i): e.desc() for e in els}}
            in_browser = bool(win and win.app.lower() in BROWSERS)
            # A web errand belongs in the browser. Asking the decider to "focus" its way there
            # wastes a cycle and invites wandering into whatever else is open.
            browser = next((w for w in wins if w.app.lower() in BROWSERS), None)
            if (not in_browser and browser and _navigate_url(goal, cfg, True)
                and not _launch_target(goal)):
                if not dry and browser.shell_id:
                    atspi.activate_window(browser.shell_id)
                    time.sleep(0.4)
                res.steps.append(f"focus {browser.app}")
                win, in_browser = browser, True
                els = atspi.elements(win, limit=70)
                res.window = f"{win.app}: {win.title}"
            nav_url = _navigate_url(goal, cfg, in_browser)
            if nav_url in visited or len(visited) >= 2:
                nav_url = None          # already went there; work with the page you have
            state["navigate_would_open"] = nav_url or "(nothing new to open — use the page)"
            if visited:
                state["already_opened"] = visited
            state["uncommitted_control"] = (
                pend.desc() if (pend := _pending_commit(els)) else "(none)")
            state["already_done"] = sorted(spent)[:12]
            a = _jev(cfg.decide, json.dumps(state), _questions(els, wins, win, nav_url, hands, spent))
            op = a["operation"]["choice"] if "operation" in a else "stuck"
            if a["done"]["noul"] > 0.7 or op == "done":
                # a visible Done/Search button means the work is staged, not finished
                if pend and commits < 3:
                    commits += 1
                    step = f"commit '{pend.name}'"
                    if not dry:
                        if not atspi.do_action(pend) and ptr:
                            ptr.click(pend.cx, pend.cy)
                            expected_ptr = (pend.cx, pend.cy)
                        atspi.wait_settled(win, (len(els), tuple((e.role, e.name) for e in els[:20])),
                                           cap_ms=1500)
                    res.steps.append(step)
                    continue
                if cfg.desktop.verify and not dry:
                    from hearthsmith.desktop import vision
                    rect = (win.x, win.y, win.w, win.h) if win else None
                    ok_seen, why = vision.verify(cfg.compose, goal, rect)
                    res.seen = why
                    if ok_seen is False:
                        # the screen disagrees with the decider — keep going rather than
                        # reporting a success nobody can see
                        verified += 1
                        if verified > MAX_RECHECKS:
                            res.note = "the screen still doesn't show it"
                            return res          # out of corrections: say so, don't claim done
                        res.steps.append(f"looked: {why[:80]}")
                        continue
                res.ok = True
                say.done(f"Done — {res.window}." if res.window else "Done.")
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
                u = nav_url        # already resolved above, with the browser context
                if not u:
                    res.note = "navigate chosen but no address or search in the goal"
                    return res
                if u in visited:
                    res.note = "already opened that"
                    return res
                visited.append(u)
                last = (op, u)
                step = f"navigate {u}"
                if not dry:
                    subprocess.Popen(["xdg-open", u], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    for _ in range(25):
                        time.sleep(0.4)
                        w2 = next((x for x in atspi.windows() if x.app == (win.app if win else "")), None)
                        if w2 and w2.title != (win.title if win else ""):
                            if _answered_by(goal, w2.title):
                                res.steps.append(step)
                                res.ok, res.window = True, f"{w2.app}: {w2.title}"
                                return res
                            break

            elif op == "launch":
                exe = _launch_target(goal)
                if not exe:
                    res.note = "launch chosen but I don't know which app that is"
                    return res
                step = f"launch {exe}"
                if not dry:
                    args = [exe] + ([_goal_url(goal)] if exe == "firefox" and _goal_url(goal) else [])
                    # Scope the app to the graphical session. A bare Popen outlives a logout:
                    # the window dies with the compositor but the process keeps the profile
                    # lock, and every later launch silently hands off to a corpse.
                    subprocess.Popen(["systemd-run", "--user", "--quiet", "--collect", "--scope",
                                      "--slice=app-graphical.slice", *args],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    for _ in range(30):              # wait for it to register with AT-SPI
                        time.sleep(0.4)
                        if any(exe.split("-")[0] in w.app.lower() for w in atspi.windows()):
                            break

            elif op == "focus" and "window" in a:
                w = wins[int(a["window"]["choice"])]
                if (op, w.title) == last:
                    res.note = f"already focused {w.app}"
                    return res
                last = (op, w.title)
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
                spent[tgt.desc()] = spent.get(tgt.desc(), 0) + 1

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
                    try:
                        val = _fill_value(cfg, goal, tgt.desc(), filled)
                    except PermissionError as e:
                        res.note = str(e)
                        return res
                    secret = bool(__import__("hearthsmith.ask", fromlist=["x"]).sensitive(tgt.desc()))
                    filled[tgt.name or tgt.role] = "(from you)" if secret else val
                    step = (f"type into '{tgt.desc()}': (from you)" if secret
                            else f"type into '{tgt.desc()}': {val!r}")
                    if not dry:
                        # A combobox/autocomplete on a modern page discards programmatic text:
                        # AT-SPI reports success, the value looks right for a moment, then the
                        # page's own handlers reset it. Type those for real when allowed.
                        spa = tgt.role in ("combo box", "autocomplete")
                        quiet_ok = False if (spa and ptr and kb) else atspi.set_text(tgt, val)
                        if quiet_ok:
                            time.sleep(0.25)           # let the page's handlers have their say
                            got = atspi.read_text(tgt).strip()
                            quiet_ok = bool(atspi.suggestions(win, val)) or (
                                bool(got) and atspi.fold(val)[:10] in atspi.fold(got))
                        if quiet_ok:
                            # an autocomplete wants its suggestion picked, not a button pressed
                            if picked := atspi.pick_suggestion(win, val):
                                step += f" → {picked}"
                            elif atspi.submit_near(tgt, els):
                                step += " + Search"
                            elif kb:
                                kb.tap("enter")
                                step += " + Enter"
                            else:
                                step += " (typed; nothing to submit it — may need --hands)"
                        elif ptr and kb:
                            ptr.click(tgt.cx, tgt.cy)
                            expected_ptr = (tgt.cx, tgt.cy)
                            time.sleep(0.2)
                            kb.type_text(val)          # real keystrokes, so autocompletes fire
                            time.sleep(0.2)
                            # A combobox blanks its own accessible value while the dropdown is
                            # open, so "did it take?" is answered by the suggestions, not the
                            # field: matching options mean the page saw every keystroke.
                            if picked := atspi.pick_suggestion(win, val):
                                step += f" → {picked}"
                            else:
                                # a date field commits on blur, so its value can lag the
                                # keystrokes — give it a beat before calling it a failure
                                got = ""
                                for _ in range(6):
                                    got = atspi.read_text(tgt).strip()
                                    if got:
                                        break
                                    time.sleep(0.1)
                                kb.tap("enter")
                                step += " + Enter" if got else " + Enter (field read back empty)"
                        else:
                            res.note = (f"'{tgt.desc()}' ignored the quiet fill (the page wants "
                                        "real typing) — rerun with --hands")
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
            say.step(step)
        res.note = "step limit"
        return res
    finally:
        for dev in (ptr, kb):
            if dev:
                dev.close()
