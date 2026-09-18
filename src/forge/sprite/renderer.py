"""forge-sprite: GTK3 always-on-top, click-through, transparent window that polls sprite.json and
plays the matching state. XWayland path (GDK_BACKEND=x11) because Mutter has no layer-shell; a
GNOME Shell extension can replace this later — the state file contract stays.

Interaction: left click = talk to him, left hold+drag = move, right click = menu (size, corner,
sheet, nag now, hide). Everything outside his body is click-through. Position/size persist in
~/.config/forge/avatar.yaml (scale, x/y or corner, sheet) and hot-reload.

Launch as the user service (forge-sprite start), never from an agent pane (cgroup cap + oomd).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("GDK_BACKEND", "x11")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
import cairo
import yaml
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo

from forge.config import STATE_DIR
from forge.sprite import pack as packmod
from forge.sprite import procedural
from forge.sprite.procedural import AVATAR_CFG

BUBBLE_W = 300
ALIVE_FILE = STATE_DIR / "sprite.alive"


def _rgb(hexs: str) -> tuple[float, float, float]:
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def load_avatar_cfg() -> dict:
    if AVATAR_CFG.exists():
        return yaml.safe_load(AVATAR_CFG.read_text()) or {}
    return {}


def save_avatar_cfg(patch: dict) -> None:
    cfg = load_avatar_cfg()
    for k, v in patch.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    AVATAR_CFG.parent.mkdir(parents=True, exist_ok=True)
    AVATAR_CFG.write_text(yaml.safe_dump(cfg, sort_keys=True))


class Bubble(Gtk.Window):
    """Pixel-art speech balloon (parchment, stepped corners, blocky tail) drawn on a unit grid so
    it stays crisp; VT323 text typed out at ~30 cps. Own click-through popup, tail aimed at him."""

    U = 3                       # screen px per balloon pixel
    PAD = 3                     # units
    TAIL = 4                    # units
    COLS = 100                  # max width in units
    CPS = 32
    C = {"o": (0.23, 0.15, 0.10), "f": (0.91, 0.76, 0.60), "h": (0.97, 0.87, 0.75),
         "s": (0.80, 0.62, 0.45), "t": (0.17, 0.11, 0.07)}

    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        screen = self.get_screen()
        if (vis := screen.get_rgba_visual()) and screen.is_composited():
            self.set_visual(vis)
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.text, self.shown, self.tail_side, self.tail_at = "", 0, "bottom", 0.5
        self.font = Pango.FontDescription("VT323 21px")
        self.connect("realize", lambda *_: self.get_window().input_shape_combine_region(
            cairo.Region(), 0, 0))

    # -- text ----------------------------------------------------------------------------------

    def _layout(self, cr, width_px: int, text: str):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(self.font)
        lay.set_width(width_px * Pango.SCALE)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_text(text, -1)
        return lay

    def _type(self) -> bool:
        if self.shown >= len(self.text):
            return False
        self.shown += 1
        self.area.queue_draw()
        ch = self.text[self.shown - 1]
        delay = 260 if ch in ".!?" else 120 if ch in ",;:—" else int(1000 / self.CPS)
        GLib.timeout_add(delay, self._type)
        return False

    @property
    def done(self) -> bool:
        return self.shown >= len(self.text)

    # -- placement -----------------------------------------------------------------------------

    def show_at(self, text: str, anchor: tuple[int, int, int, int]) -> None:
        """anchor = sprite body rect (x, y, w, h) in root coords."""
        restart = text != self.text
        self.text = text
        if restart:
            self.shown = 0
            GLib.timeout_add(80, self._type)
        U, P, T = self.U, self.PAD, self.TAIL
        sx, sy, sw, sh = anchor
        mon = self.get_screen().get_display().get_primary_monitor().get_workarea()
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surf)
        _, ext = self._layout(cr, (self.COLS - 2 * P - 2) * U, text).get_pixel_extents()
        self.bw = min(self.COLS, -(-ext.width // U) + 2 * P + 2)   # units, incl. outline
        self.bh = -(-ext.height // U) + 2 * P + 2
        bwp, bhp = self.bw * U, self.bh * U
        if sy - bhp - T * U - 8 >= mon.y:
            self.tail_side = "bottom"
            x = sx + sw // 2 - bwp // 2
            y = sy - bhp - T * U - 4
            x = max(mon.x + 4, min(x, mon.x + mon.width - bwp - 4))
            self.tail_at = (sx + sw // 2 - x) // U
            self.resize(bwp, bhp + T * U)
        else:
            right_room = mon.x + mon.width - (sx + sw)
            self.tail_side = "left" if right_room >= bwp + T * U + 8 else "right"
            x = sx + sw + T * U + 4 if self.tail_side == "left" else sx - bwp - T * U - 4
            y = max(mon.y + 4, min(sy + sh // 4 - bhp // 2, mon.y + mon.height - bhp - 4))
            self.tail_at = (sy + sh // 3 - y) // U
            self.resize(bwp + T * U, bhp)
        self.move(int(x), int(y))
        self.show_all()
        self.area.queue_draw()

    # -- raster --------------------------------------------------------------------------------

    def _grid(self) -> tuple[dict[tuple[int, int], str], int, int]:
        """Cells → palette key. Body at (ox, oy); tail hangs off it."""
        bw, bh, T = self.bw, self.bh, self.TAIL
        ox = T if self.tail_side == "left" else 0
        oy = 0
        g: dict[tuple[int, int], str] = {}
        cut = {(0, 0), (1, 0), (0, 1)}  # stepped corner: drop 3 cells, outline the diagonal
        for y in range(bh):
            for x in range(bw):
                cx = min(x, bw - 1 - x)
                cy = min(y, bh - 1 - y)
                if (cx, cy) in cut:
                    continue
                edge = x in (0, bw - 1) or y in (0, bh - 1) or (cx, cy) in {(1, 1), (2, 0), (0, 2)}
                if edge:
                    k = "o"
                elif y == 1 or x == 1:
                    k = "h"
                elif y == bh - 2 or x == bw - 2:
                    k = "s"
                else:
                    k = "f"
                g[(ox + x, oy + y)] = k
        # tail: 45° wedge — right edge straight, left edge one cell per row — so the outline is a
        # continuous diagonal; the body's edge + shade rows open into it with no seam
        base = T + 1
        if self.tail_side == "bottom":
            tc = max(base + 3, min(bw - 3, int(self.tail_at)))   # column just right of the wedge
            for row in (bh - 2, bh - 1):                         # open body shade + outline rows
                for x in range(tc - base, tc):
                    g[(ox + x, oy + row)] = "f"
            for k in range(T):
                y, w = oy + bh + k, base - k
                for x in range(tc - w, tc):
                    g[(ox + x, y)] = "f"
                g[(ox + tc - w - 1, y)] = "o"
                g[(ox + tc, y)] = "o"
            g[(ox + tc - 1, oy + bh + T)] = "o"
            g[(ox + tc, oy + bh + T)] = "o"
        else:
            tr = max(base + 3, min(bh - 3, int(self.tail_at)))
            left = self.tail_side == "left"
            for col in ((ox, ox + 1) if left else (ox + bw - 1, ox + bw - 2)):
                for y in range(tr - base, tr):
                    g[(col, oy + y)] = "f"
            for k in range(T):
                x, w = (ox - 1 - k) if left else (ox + bw + k), base - k
                for y in range(tr - w, tr):
                    g[(x, oy + y)] = "f"
                g[(x, oy + tr - w - 1)] = "o"
                g[(x, oy + tr)] = "o"
            xt = (ox - 1 - T) if left else (ox + bw + T)
            g[(xt, oy + tr - 1)] = "o"
            g[(xt, oy + tr)] = "o"
        return g, ox, oy

    def on_draw(self, _w, cr) -> bool:
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        U = self.U
        g, ox, oy = self._grid()
        for (x, y), k in g.items():
            cr.set_source_rgb(*self.C[k])
            cr.rectangle(x * U, y * U, U, U)
            cr.fill()
        cr.set_antialias(cairo.ANTIALIAS_DEFAULT)
        cr.set_source_rgb(*self.C["t"])
        tx, ty = (ox + self.PAD + 1) * U, (oy + self.PAD + 1) * U
        shown = self.text[:self.shown]
        cr.move_to(tx, ty)
        lay = self._layout(cr, (self.bw - 2 * self.PAD - 2) * U, shown)
        PangoCairo.show_layout(cr, lay)
        if not self.done and int(time.time() * 3) % 2 == 0:  # block cursor
            _, ext = lay.get_pixel_extents()
            lines = lay.get_line_count()
            last = lay.get_line_readonly(lines - 1).get_pixel_extents()[1] if lines else None
            cx = tx + (last.x + last.width if last else 0)
            cy = ty + ext.height - 14 if shown else ty + 2
            cr.rectangle(cx + 2, cy, 8, 14)
            cr.fill()
        return True


class Sprite(Gtk.Window):
    def __init__(self, state_file: Path, pack: packmod.Pack | None, cli_scale: float | None,
                 cli_corner: str | None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.state_file, self.pack = state_file, pack
        self.cli_scale, self.cli_corner = cli_scale, cli_corner
        self._pix: dict[tuple[Path, int], GdkPixbuf.Pixbuf] = {}
        self.state, self.frame, self.text, self.text_until = "idle", 0, "", 0.0
        self._state_mtime = self._cfg_mtime = 0.0
        self._frame_at = 0.0
        self.cfg: dict = {}
        self._suppress_save = 0.0
        self._press: tuple[int, int, int] | None = None  # x_root, y_root, time
        self._dragging = False
        self._grab = (0, 0)

        self.set_title("forge")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_app_paintable(True)
        screen = self.get_screen()
        if (vis := screen.get_rgba_visual()) and screen.is_composited():
            self.set_visual(vis)

        self.bubble = Bubble()
        self._alive_at = 0.0
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_MOTION_MASK
                        | Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.on_scroll)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("configure-event", self.on_configure)
        self.connect("realize", lambda *_: self._apply_cfg(force=True))
        GLib.timeout_add(100, self._tick)

    # -- geometry ------------------------------------------------------------------------------

    @property
    def scale(self) -> float:
        return float(self.cfg.get("scale") or self.cli_scale or (1.0 if self.pack else 4))

    def sprite_size(self) -> tuple[int, int]:
        if self.pack:
            return round(self.pack.cell * self.scale), round(self.pack.cell * self.scale)
        return round(procedural.W * self.scale), round(procedural.H * self.scale)

    def _apply_cfg(self, force: bool = False) -> None:
        try:
            m = AVATAR_CFG.stat().st_mtime if AVATAR_CFG.exists() else 0.0
        except OSError:
            m = 0.0
        if not force and m == self._cfg_mtime:
            return
        self._cfg_mtime = m
        self.cfg = load_avatar_cfg()
        self.palette = {k: _rgb(v) for k, v in {**procedural.DEFAULT_PALETTE,
                                                **self.cfg.get("palette", {})}.items()}
        self._pix.clear()
        sw, sh = self.sprite_size()
        self.bubble_h = 0
        w, h = sw, sh
        self.set_size_request(w, h)
        self.resize(w, h)
        self._place(w, h)
        self._set_input_shape()
        self.area.queue_draw()

    def _place(self, w: int, h: int) -> None:
        self._suppress_save = time.time() + 1.0
        if "x" in self.cfg and "y" in self.cfg:
            self.move(int(self.cfg["x"]), int(self.cfg["y"]))
            return
        corner = self.cfg.get("corner") or self.cli_corner or "bottom-right"
        mon = self.get_screen().get_display().get_primary_monitor().get_workarea()
        x = mon.x + (mon.width - w - 16 if "right" in corner else 16)
        y = mon.y + (mon.height - h - 16 if "bottom" in corner else 16)
        self.move(x, y)

    def _set_input_shape(self) -> None:
        win = self.get_window()
        if not win:
            return
        sw, sh = self.sprite_size()
        # only his body takes input; bubble + margins pass clicks through
        win.input_shape_combine_region(
            cairo.Region(cairo.RectangleInt(0, self.bubble_h, sw, sh)), 0, 0)

    # -- interaction: click = talk, hold+drag = move, right click = menu -------------------------

    DRAG_PX = 6

    def on_press(self, _w, ev) -> bool:
        if ev.button == 1:
            wx, wy = self.get_position()
            self._press = (int(ev.x_root), int(ev.y_root), ev.time)
            self._grab = (int(ev.x_root) - wx, int(ev.y_root) - wy)  # pointer offset in window
            self._dragging = False
        elif ev.button == 3:
            self.open_menu(ev)
        return True

    def on_motion(self, _w, ev) -> bool:
        # manual drag: Mutter won't WM-move a DOCK window (begin_move_drag is ignored), but a
        # dock may position itself, so we follow the pointer with move()
        if self._press and ev.state & Gdk.ModifierType.BUTTON1_MASK:
            x0, y0, _ = self._press
            if not self._dragging and (abs(ev.x_root - x0) > self.DRAG_PX
                                       or abs(ev.y_root - y0) > self.DRAG_PX):
                self._dragging = True
                self.bubble.hide()
            if self._dragging:
                gx, gy = self._grab
                self.move(int(ev.x_root) - gx, int(ev.y_root) - gy)
        return True

    def on_release(self, _w, ev) -> bool:
        if ev.button == 1 and self._press:
            if self._dragging:
                x, y = self.get_position()
                self.cfg["x"], self.cfg["y"] = x, y
                self.cfg.pop("corner", None)
                save_avatar_cfg({"x": x, "y": y, "corner": None})
                self._cfg_mtime = AVATAR_CFG.stat().st_mtime
                self._suppress_save = time.time() + 1.0
            elif self.text:
                self.text = ""          # first click dismisses what he's saying
                self.bubble.hide()
                if self.state in ("forge", "alert"):
                    self.state, self.frame = "idle", 0
            else:
                self.open_prompt()
        self._press = None
        self._dragging = False
        return True

    def on_configure(self, _w, ev) -> bool:
        if self.bubble.get_visible() and not self._dragging:
            x, y = self.get_position()
            sw, sh = self.sprite_size()
            self.bubble.show_at(self.text, (x, y + self.bubble_h, sw, sh))
        return False

    # -- menu ----------------------------------------------------------------------------------

    def open_menu(self, ev) -> None:
        m = Gtk.Menu()

        def item(label, cb, submenu=None):
            it = Gtk.MenuItem(label=label)
            if submenu:
                it.set_submenu(submenu)
            else:
                it.connect("activate", lambda *_: cb())
            m.append(it)
            return it

        size = Gtk.Menu()
        for lab, sc in (("Small", 0.75), ("Normal", 1.0), ("Large", 1.5), ("Huge", 2.0), ("Giant", 3.0)):
            it = Gtk.CheckMenuItem(label=lab)
            it.set_active(abs(self.scale - sc) < 0.05)
            it.connect("activate", lambda _i, sc=sc: save_avatar_cfg({"scale": sc}))
            size.append(it)
        item("Size", None, size)

        corner = Gtk.Menu()
        for c in ("top-left", "top-right", "bottom-left", "bottom-right"):
            it = Gtk.MenuItem(label=c.replace("-", " ").title())
            it.connect("activate", lambda _i, c=c: save_avatar_cfg({"corner": c, "x": None, "y": None}))
            corner.append(it)
        item("Snap to corner", None, corner)

        sheets = Gtk.Menu()
        for p in sorted((Path.home() / "dev/forge/assets/sheets").glob("*.png")):
            it = Gtk.CheckMenuItem(label=p.stem)
            it.set_active(self.cfg.get("sheet") == p.stem)
            it.connect("activate", lambda _i, p=p: self._switch_sheet(p))
            sheets.append(it)
        item("Appearance", None, sheets)

        m.append(Gtk.SeparatorMenuItem())
        item("Talk to him…", self.open_prompt)
        item("Nag me now", lambda: self._systemctl("start", "forged-now.service"))
        mute = Gtk.Menu()
        for lab, mins in (("1 hour", 60), ("4 hours", 240), ("Rest of today", 24 * 60), ("Unmute", 0)):
            it = Gtk.MenuItem(label=lab)
            it.connect("activate", lambda _i, mins=mins: self._forge("unmute" if mins == 0 else "mute", str(mins)))
            mute.append(it)
        item("Mute nagging", None, mute)
        m.append(Gtk.SeparatorMenuItem())
        item("Hide", lambda: self._systemctl("stop", "forge-sprite.service"))
        m.show_all()
        m.popup_at_pointer(ev)

    def _switch_sheet(self, sheet: Path) -> None:
        import subprocess
        out = packmod.DEFAULT_PACK
        subprocess.run([str(Path.home() / "dev/forge/.venv/bin/forge-sprite-slice"), str(sheet), str(out),
                        "--cell", "96"], check=False, capture_output=True, timeout=60)
        self.pack = packmod.load(out)
        self._pix.clear()
        save_avatar_cfg({"sheet": sheet.stem})

    def _forge(self, *args: str) -> None:
        import subprocess
        subprocess.Popen([str(Path.home() / "dev/forge/.venv/bin/forge"), *[a for a in args if a]])

    def _systemctl(self, verb: str, unit: str) -> None:
        import subprocess
        subprocess.Popen(["systemctl", "--user", verb, unit])

    # -- talk to him ---------------------------------------------------------------------------

    def open_prompt(self) -> None:
        if getattr(self, "_prompt", None):
            self._prompt.present()
            return
        w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        w.set_app_paintable(True)
        if (vis := w.get_screen().get_rgba_visual()) and w.get_screen().is_composited():
            w.set_visual(vis)
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            #forge-prompt { background: rgba(20,20,22,0.96); border: 2px solid #ff9a2e;
                            border-radius: 10px; padding: 6px 12px; }
            #forge-prompt entry { background: transparent; border: none; box-shadow: none;
                                  color: #f2f2f2; font-family: monospace; font-size: 15px;
                                  caret-color: #ff9a2e; }
            #forge-prompt label { color: #ff9a2e; font-size: 26px; margin-right: 12px;
                                  margin-left: 2px; }
        """)
        Gtk.StyleContext.add_provider_for_screen(w.get_screen(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_name("forge-prompt")
        box.pack_start(Gtk.Label(label="⚒"), False, False, 0)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Tell the smith…   Enter to send · Esc to close")
        entry.set_width_chars(60)
        box.pack_start(entry, True, True, 0)
        w.add(box)
        mon = w.get_screen().get_display().get_primary_monitor().get_workarea()
        pw = 640
        w.set_default_size(pw, -1)
        w.move(mon.x + (mon.width - pw) // 2, mon.y + int(mon.height * self.cfg.get("prompt_y", 0.22)))
        entry.connect("activate", lambda e: self._submit(e.get_text()))
        w.connect("key-press-event",
                  lambda _w, ev: self._close_prompt() if ev.keyval == Gdk.KEY_Escape else False)
        w.connect("focus-out-event", lambda *_: self._close_prompt())
        w.connect("destroy", lambda *_: setattr(self, "_prompt", None))
        self._prompt = w
        w.show_all()
        w.present()
        entry.grab_focus()

    def _close_prompt(self) -> bool:
        if getattr(self, "_prompt", None):
            self._prompt.destroy()
            self._prompt = None
        return True

    def _submit(self, text: str) -> None:
        text = text.strip()
        self._close_prompt()
        if not text:
            return
        self.state, self.frame = "forge", 0
        self.text, self.text_until = "…", time.time() + 60
        import subprocess
        import threading

        def run():
            try:
                out = subprocess.run([str(Path.home() / "dev/forge/.venv/bin/forge"), "say", "--json", text],
                                     capture_output=True, text=True, timeout=120)
                r = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else \
                    {"intent": "error", "text": (out.stderr or "no reply").strip()[-160:]}
            except Exception as e:  # noqa: BLE001
                r = {"intent": "error", "text": f"{type(e).__name__}: {e}"[:160]}
            GLib.idle_add(self._show_reply, r)
        threading.Thread(target=run, daemon=True).start()

    def _show_reply(self, r: dict) -> bool:
        self.text, self.text_until = r["text"], time.time() + 60
        self.state, self.frame = ("alert" if r["intent"] in ("nag",) else "idle"), 0
        return False

    def on_scroll(self, _w, ev) -> bool:
        step = 0.1 if ev.direction == Gdk.ScrollDirection.UP else -0.1 if \
            ev.direction == Gdk.ScrollDirection.DOWN else 0
        if step:
            save_avatar_cfg({"scale": round(max(0.3, min(6.0, self.scale + step)), 2)})
        return True

    # -- animation -----------------------------------------------------------------------------

    def _tick(self) -> bool:
        self._apply_cfg()
        try:
            m = self.state_file.stat().st_mtime
            if m != self._state_mtime:
                self._state_mtime = m
                d = json.loads(self.state_file.read_text())
                new = d.get("state", "idle")
                if new != self.state:
                    self.state, self.frame = new, 0
                if d.get("text") and d.get("at", 0) > time.time() - 600:
                    self.text, self.text_until = d["text"], time.time() + 90
                elif new in ("forge", "alert"):
                    self.text_until = time.time() + 20  # animation without a line: short burst
        except (OSError, ValueError):
            pass
        fps = (self.pack.fps if self.pack else procedural.FPS).get(self.state, 1)
        now = time.time()
        if now - self._frame_at >= 1.0 / max(fps, 0.1):
            self.frame += 1
            self._frame_at = now
        if self.text and time.time() > self.text_until:
            self.text = ""
            if self.state in ("forge", "alert"):
                self.state, self.frame = "idle", 0  # said his piece; back to the bellows
        elif not self.text and self.state in ("forge", "alert") and time.time() > self.text_until:
            self.state, self.frame = "idle", 0
        if self.text:
            if self.bubble.text != self.text or not self.bubble.get_visible():
                x, y = self.get_position()
                sw, sh = self.sprite_size()
                self.bubble.show_at(self.text, (x, y + self.bubble_h, sw, sh))
        elif self.bubble.get_visible():
            self.bubble.hide()
        if time.time() - self._alive_at > 2:
            self._alive_at = time.time()
            try:
                ALIVE_FILE.touch()
            except OSError:
                pass
        self.area.queue_draw()
        return True

    # -- drawing -------------------------------------------------------------------------------

    def on_draw(self, _w, cr) -> bool:
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        oy = self.bubble_h
        sw, sh = self.sprite_size()
        if self.pack:
            frames = self.pack.frames(self.state)
            if frames:
                self._blit(cr, frames[self.frame % len(frames)], 0, oy, sw)
        else:
            s = self.scale
            for y, row in enumerate(procedural.composite(self.state, self.frame)):
                for x, key in enumerate(row):
                    if key and key in self.palette:
                        cr.set_source_rgb(*self.palette[key])
                        cr.rectangle(x * s, oy + y * s, s + 0.5, s + 0.5)
                        cr.fill()
        return True

    def _blit(self, cr, path: Path, x: int, y: int, size: int) -> None:
        key = (path, size)
        pb = self._pix.get(key)
        if pb is None:
            src = GdkPixbuf.Pixbuf.new_from_file(str(path))
            pb = src if src.get_width() == size else src.scale_simple(
                size, size, GdkPixbuf.InterpType.NEAREST)
            self._pix[key] = pb
        Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
        cr.get_source().set_filter(cairo.FILTER_FAST)
        cr.paint()


def _add_fonts() -> None:
    """Register assets/fonts with fontconfig for this process (no system install needed)."""
    import ctypes
    import ctypes.util
    lib = ctypes.util.find_library("fontconfig")
    if not lib:
        return
    fc = ctypes.CDLL(lib)
    for f in (Path(__file__).resolve().parents[3] / "assets/fonts").glob("*.ttf"):
        fc.FcConfigAppFontAddFile(None, str(f).encode())


def main() -> None:
    _add_fonts()
    ap = argparse.ArgumentParser(prog="forge-sprite")
    ap.add_argument("--scale", type=float, help="default size multiplier (avatar.yaml wins)")
    ap.add_argument("--corner", help="default corner (avatar.yaml wins)")
    ap.add_argument("--state-file", type=Path, default=STATE_DIR / "sprite.json")
    ap.add_argument("--pack", type=Path, help="sprite pack dir (default ~/.config/forge/pack)")
    ap.add_argument("--procedural", action="store_true", help="ignore packs, draw the placeholder")
    a = ap.parse_args()
    pack = None if a.procedural else packmod.load(a.pack)
    win = Sprite(a.state_file, pack, a.scale, a.corner)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
