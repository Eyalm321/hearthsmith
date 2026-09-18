"""Observe the desktop through AT-SPI2. Needs the a11y bus on:
    gsettings set org.gnome.desktop.interface toolkit-accessibility true
Firefox/Chromium/Electron/GTK/Qt all expose their UI once a client connects. Custom canvases
(games, terminal grids) don't — that's the screenshot fallback's job, not this module's."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pyatspi

ACTIONABLE = {
    "push button", "toggle button", "link", "entry", "password text", "text", "combo box",
    "check box", "radio button", "menu item", "check menu item", "radio menu item", "page tab",
    "list item", "tree item", "table cell", "slider", "spin button", "menu",
    "search box", "autocomplete",
}
# Containers, not controls: clicking the document is a no-op that looks like an action.
NEVER_TARGET = {"document web", "document frame", "panel", "filler", "section"}
FILLABLE = {"entry", "password text", "text", "search box", "autocomplete", "spin button"}
SKIP_APPS = {"gnome-shell", "ibus-extension-gtk3", "xdg-desktop-portal-gtk", "xdg-desktop-portal-gnome",
             "evolution-alarm-notify", "mutter-x11-frames", "renderer.py", "hearthsmith"}


@dataclass
class Element:
    i: int
    role: str
    name: str
    x: int
    y: int
    w: int
    h: int
    fillable: bool
    app: str
    window: str
    acc: object = None       # the live accessible, for freshness checks
    clickable: bool = True

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    def desc(self) -> str:
        return f"{self.role}: {self.name}" + (" [text field]" if self.fillable else "")


@dataclass
class Window:
    app: str
    title: str
    x: int
    y: int
    w: int
    h: int
    active: bool
    acc: object  # pyatspi accessible
    shell_id: int = 0
    minimized: bool = False


def desktop():
    return pyatspi.Registry.getDesktop(0)


def shell_windows() -> list[dict]:
    """Frame rects + focus from the hearthsmith-windows GNOME Shell extension (Wayland hides window
    positions from clients; AT-SPI extents are window-relative). [] if the extension is off."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                              "--object-path", "/org/hearthsmith/Windows", "--method", "org.hearthsmith.Windows.List"],
                             capture_output=True, text=True, timeout=3, check=False).stdout.strip()
        # gdbus prints ('<json>',)
        return json.loads(out[2:-3].encode().decode("unicode_escape")) if out.startswith("('") else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def shell_pointer() -> tuple[int, int] | None:
    """Cursor position from the extension. None when it isn't available — Wayland clients can't
    read the pointer themselves (GDK returns 0,0), so the caller must not guess."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                              "--object-path", "/org/hearthsmith/Windows", "--method", "org.hearthsmith.Windows.Pointer"],
                             capture_output=True, text=True, timeout=3, check=False).stdout.strip()
        if not out.startswith("('"):
            return None
        d = json.loads(out[2:-3].encode().decode("unicode_escape"))
        return int(d["x"]), int(d["y"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def activate_window(win_id: int) -> bool:
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                              "--object-path", "/org/hearthsmith/Windows", "--method", "org.hearthsmith.Windows.Activate",
                              str(win_id)], capture_output=True, text=True, timeout=3, check=False).stdout
        return "true" in out
    except (OSError, subprocess.SubprocessError):
        return False


def _extents(acc):
    try:
        e = acc.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        return e.x, e.y, e.width, e.height
    except Exception:  # noqa: BLE001 — many nodes have no Component
        return None


def _showing(acc) -> bool:
    try:
        st = acc.getState()
        return st.contains(pyatspi.STATE_SHOWING) and st.contains(pyatspi.STATE_VISIBLE)
    except Exception:  # noqa: BLE001
        return False


def _match_shell(sw: list[dict], app_name: str, title: str, pid: int | None) -> dict | None:
    """Pair an AT-SPI frame with a Shell window: pid+title first, then title, then wm_class."""
    t = title.strip()
    for s in sw:
        if pid and s["pid"] == pid and (not t or s["title"] == t):
            return s
    for s in sw:
        if t and s["title"] == t:
            return s
    a = app_name.lower()
    for s in sw:
        if a and (a in s["wm_class"].lower() or a in s["app_id"].lower() or a in s["app"].lower()):
            return s
    return None


def windows() -> list[Window]:
    out = []
    d = desktop()
    sw = shell_windows()
    for i in range(d.childCount):
        app = d.getChildAtIndex(i)
        if app is None or app.name in SKIP_APPS:
            continue
        try:
            pid = app.get_process_id()
        except Exception:  # noqa: BLE001
            pid = None
        for j in range(app.childCount):
            w = app.getChildAtIndex(j)
            if w is None or w.getRoleName() not in ("frame", "window", "dialog"):
                continue
            if not _showing(w):
                continue
            ext = _extents(w)
            if not ext or ext[2] < 50:
                continue
            try:
                active = w.getState().contains(pyatspi.STATE_ACTIVE)
            except Exception:  # noqa: BLE001
                active = False
            m = _match_shell(sw, app.name, w.name or "", pid)
            if m:
                if m["minimized"]:
                    continue
                x, y, ww, hh = m["x"], m["y"], m["w"], m["h"]
                active = active or m["focus"]
                out.append(Window(app.name, w.name or "", x, y, ww, hh, active, w, m["id"], m["minimized"]))
            else:
                out.append(Window(app.name, w.name or "", *ext, active, w))
    return out


def elements(win: Window, limit: int = 80, max_depth: int = 40) -> list[Element]:
    """Actionable, showing descendants with on-screen extents, in tree order."""
    out: list[Element] = []
    stack = [(win.acc, 0)]
    seen = 0
    while stack and len(out) < limit and seen < 6000:
        acc, depth = stack.pop()
        seen += 1
        try:
            n = acc.childCount
        except Exception:  # noqa: BLE001
            continue
        kids = []
        for k in range(min(n, 400)):
            try:
                c = acc.getChildAtIndex(k)
            except Exception:  # noqa: BLE001
                continue
            if c is None:
                continue
            kids.append(c)
        for c in reversed(kids):
            # Prune hidden subtrees. A browser keeps every background tab's document in the
            # tree; descending into them burns the budget before the visible page is reached —
            # which is why a 14-tab window used to report nothing but chrome.
            if depth < max_depth and _showing(c):
                stack.append((c, depth + 1))
        for c in kids:
            try:
                role = c.getRoleName()
            except Exception:  # noqa: BLE001
                continue
            if role not in ACTIONABLE or role in NEVER_TARGET or not _showing(c):
                continue
            ext = _extents(c)
            if not ext or ext[2] < 3 or ext[3] < 3:
                continue
            name = (c.name or "").strip()
            if not name:
                try:
                    name = (c.description or "").strip()
                except Exception:  # noqa: BLE001
                    pass
            if not name and role not in FILLABLE:
                continue
            fillable = role in FILLABLE
            if not fillable:
                try:
                    fillable = c.getState().contains(pyatspi.STATE_EDITABLE)
                except Exception:  # noqa: BLE001
                    pass
            fx, fy = (_extents(win.acc) or (0, 0, 0, 0))[:2]
            ex, ey, ew, eh = ext
            if win.shell_id and (fx, fy) == (0, 0):
                ex, ey = ex + win.x, ey + win.y  # Wayland: window-relative → screen
            out.append(Element(len(out), role, name[:80], ex, ey, ew, eh, fillable,
                               win.app, win.title, c, role not in ("text", "entry", "password text")
                               or not fillable))
            if len(out) >= limit:
                break
    return out


# Preference order when an element offers several actions.
ACTION_PREF = ("click", "activate", "switch", "press", "jump", "open", "toggle", "expand")


def actions(el: Element) -> list[str]:
    try:
        a = el.acc.queryAction()
        return [a.getName(i).lower() for i in range(a.nActions)]
    except Exception:  # noqa: BLE001 — not every widget implements Action
        return []


def do_action(el: Element) -> bool:
    """Activate a widget through AT-SPI — no pointer, no keystrokes, so it works while the user
    is typing somewhere else. False when the widget exposes no usable action."""
    try:
        a = el.acc.queryAction()
        names = [a.getName(i).lower() for i in range(a.nActions)]
    except Exception:  # noqa: BLE001
        return False
    order = [names.index(p) for p in ACTION_PREF if p in names] or ([0] if names else [])
    for i in order:
        try:
            if a.doAction(i):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def set_text(el: Element, value: str) -> bool:
    """Replace a field's contents through AT-SPI EditableText — again, no keyboard."""
    try:
        et = el.acc.queryEditableText()
        try:
            n = el.acc.queryText().characterCount
        except Exception:  # noqa: BLE001
            n = 0
        if n:
            et.deleteText(0, n)
        return bool(et.insertText(0, value, len(value)))
    except Exception:  # noqa: BLE001
        return False


def submit_near(el: Element, els: list[Element]) -> bool:
    """After filling a field, press its Search/Go/Submit button — the quiet equivalent of Enter."""
    words = ("search", "go", "submit", "find", "apply", "ok", "enter")
    close = sorted((e for e in els if e is not el and not e.fillable),
                   key=lambda e: abs(e.y - el.y) + abs(e.x - el.x))
    for e in close[:12]:
        if any(w in e.name.lower() for w in words) and do_action(e):
            return True
    return False


def read_text(el: Element, limit: int = 120) -> str:
    try:
        tx = el.acc.queryText()
        return tx.getText(0, min(tx.characterCount, limit))
    except Exception:  # noqa: BLE001
        return ""


def fold(s: str) -> str:
    """Compare text the way a person would: 'Zurich' should match 'Zürich, Switzerland'."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower())
                   if not unicodedata.combining(c))


def suggestions(win: Window, typed: str, limit: int = 140) -> list[Element]:
    want = fold(typed.strip())
    return [e for e in elements(win, limit=limit)
            if e.role in ("list item", "menu item", "option") and e.name and want in fold(e.name)]


def pick_suggestion(win: Window, typed: str, cap_ms: int = 1200) -> str | None:
    """After filling an autocomplete, choose the option it offers. Waits briefly for the list to
    appear, then activates the best match — the quiet equivalent of arrow-down + Enter."""
    import time as _t
    deadline = _t.time() + cap_ms / 1000
    while _t.time() < deadline:
        _t.sleep(0.1)
        if (opts := suggestions(win, typed)) and do_action(opts[0]):
            return opts[0].name[:60]
    return None


def fresh(el: Element, win: Window, tol: int = 6) -> Element | None:
    """Re-read the element right before acting: gone, hidden, or moved more than `tol` px ⇒ the
    decision was made against a stale tree, so don't click into whatever is there now.
    (Guard idea from browser-use/jev-ultrafast, MIT.)"""
    if el.acc is None:
        return el
    try:
        if not _showing(el.acc):
            return None
        if el.acc.getState().contains(pyatspi.STATE_DEFUNCT):
            return None
    except Exception:  # noqa: BLE001
        return None
    ext = _extents(el.acc)
    if not ext or ext[2] < 3 or ext[3] < 3:
        return None
    ex, ey, ew, eh = ext
    fx, fy = (_extents(win.acc) or (0, 0, 0, 0))[:2]
    if win.shell_id and (fx, fy) == (0, 0):
        ex, ey = ex + win.x, ey + win.y
    if abs(ex - el.x) > tol or abs(ey - el.y) > tol:
        return None
    el.x, el.y, el.w, el.h = ex, ey, ew, eh
    return el


def signature(win: Window, limit: int = 70) -> tuple[int, tuple]:
    """Cheap fingerprint of what's on screen, to tell whether an action changed anything."""
    els = elements(win, limit=limit)
    return len(els), tuple((e.role, e.name) for e in els[:20])


def wait_settled(win: Window, before: tuple[int, tuple], cap_ms: int = 300,
                 poll_ms: int = 50) -> bool:
    """Wait until the tree actually changes, capped. Beats a fixed sleep: a combobox that pops
    suggestions in 40ms doesn't cost a second, and a slow one still gets its window."""
    import time as _t
    deadline = _t.time() + cap_ms / 1000
    while _t.time() < deadline:
        _t.sleep(poll_ms / 1000)
        try:
            if signature(win) != before:
                return True
        except Exception:  # noqa: BLE001
            return False
    return False


def active_window() -> Window | None:
    ws = windows()
    return next((w for w in ws if w.active), ws[0] if ws else None)


def find_window(app_hint: str) -> Window | None:
    h = app_hint.lower()
    for w in windows():
        if h in w.app.lower() or h in w.title.lower():
            return w
    return None


def page_text(win: Window, max_chars: int = 4000) -> str:
    """Visible text of a window (document text + labels), for read/verify steps."""
    parts: list[str] = []
    stack = [win.acc]
    n = 0
    while stack and n < 4000 and sum(map(len, parts)) < max_chars:
        acc = stack.pop()
        n += 1
        try:
            if acc.getRoleName() in ("text", "paragraph", "heading", "label", "static", "link", "section"):
                try:
                    t = acc.queryText()
                    s = t.getText(0, min(t.characterCount, 500)).strip()
                    if s:
                        parts.append(s)
                except Exception:  # noqa: BLE001
                    if acc.name:
                        parts.append(acc.name)
            for k in range(min(acc.childCount, 300)):
                c = acc.getChildAtIndex(k)
                if c is not None and _showing(c):
                    stack.append(c)
        except Exception:  # noqa: BLE001
            continue
    return " ".join(parts)[:max_chars]
