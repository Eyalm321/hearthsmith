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
    "list item", "tree item", "table cell", "slider", "spin button", "menu", "document web",
    "search box", "autocomplete",
}
FILLABLE = {"entry", "password text", "text", "search box", "autocomplete", "spin button"}
SKIP_APPS = {"gnome-shell", "ibus-extension-gtk3", "xdg-desktop-portal-gtk", "xdg-desktop-portal-gnome",
             "evolution-alarm-notify", "mutter-x11-frames", "renderer.py", "forge"}


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
    """Frame rects + focus from the forge-windows GNOME Shell extension (Wayland hides window
    positions from clients; AT-SPI extents are window-relative). [] if the extension is off."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                              "--object-path", "/org/forge/Windows", "--method", "org.forge.Windows.List"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        # gdbus prints ('<json>',)
        return json.loads(out[2:-3].encode().decode("unicode_escape")) if out.startswith("('") else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def activate_window(win_id: int) -> bool:
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                              "--object-path", "/org/forge/Windows", "--method", "org.forge.Windows.Activate",
                              str(win_id)], capture_output=True, text=True, timeout=3).stdout
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
            if depth < max_depth:
                stack.append((c, depth + 1))
        for c in kids:
            try:
                role = c.getRoleName()
            except Exception:  # noqa: BLE001
                continue
            if role not in ACTIONABLE or not _showing(c):
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
            out.append(Element(len(out), role, name[:80], ex, ey, ew, eh, fillable, win.app, win.title))
            if len(out) >= limit:
                break
    return out


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
