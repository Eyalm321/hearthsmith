"""forge-sprite: GTK3 always-on-top, click-through, transparent window that polls sprite.json and
plays the matching state. XWayland path (GDK_BACKEND=x11) because Mutter has no layer-shell; a
GNOME Shell extension can replace this later — the state file contract stays.

Position/size live in ~/.config/forge/avatar.yaml and hot-reload:
  scale: 1.5          # multiplier on the pack cell (or px-per-pixel for the procedural sprite)
  x: 1700  y: 900     # absolute; or  corner: bottom-right
  locked: true        # true = click-through. false = draggable, scroll wheel resizes,
                      # right-click locks again. Toggle with `forge-sprite unlock|lock`.

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
import cairo
import yaml
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from forge.config import STATE_DIR
from forge.sprite import pack as packmod
from forge.sprite import procedural
from forge.sprite.procedural import AVATAR_CFG

BUBBLE_W = 220


def _rgb(hexs: str) -> tuple[float, float, float]:
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def load_avatar_cfg() -> dict:
    if AVATAR_CFG.exists():
        return yaml.safe_load(AVATAR_CFG.read_text()) or {}
    return {}


def save_avatar_cfg(patch: dict) -> None:
    cfg = load_avatar_cfg()
    cfg.update(patch)
    AVATAR_CFG.parent.mkdir(parents=True, exist_ok=True)
    AVATAR_CFG.write_text(yaml.safe_dump(cfg, sort_keys=True))


class Sprite(Gtk.Window):
    def __init__(self, state_file: Path, pack: packmod.Pack | None, cli_scale: float | None,
                 cli_corner: str | None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.state_file, self.pack = state_file, pack
        self.cli_scale, self.cli_corner = cli_scale, cli_corner
        self._pix: dict[tuple[Path, int], GdkPixbuf.Pixbuf] = {}
        self.state, self.frame, self.text, self.text_until = "idle", 0, "", 0.0
        self._state_mtime = self._cfg_mtime = 0.0
        self.cfg: dict = {}
        self.locked = True
        self._suppress_save = 0.0

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

        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK)
        self.connect("button-press-event", self.on_button)
        self.connect("scroll-event", self.on_scroll)
        self.connect("configure-event", self.on_configure)
        self.connect("realize", lambda *_: self._apply_cfg(force=True))
        GLib.timeout_add(250, self._tick)

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
        self.bubble_h = 36
        w, h = sw + BUBBLE_W, sh + self.bubble_h
        self.set_size_request(w, h)
        self.resize(w, h)
        self._place(w, h)
        self._set_locked(bool(self.cfg.get("locked", True)))
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

    def _set_locked(self, locked: bool) -> None:
        self.locked = locked
        win = self.get_window()
        if not win:
            return
        if locked:
            win.input_shape_combine_region(cairo.Region(), 0, 0)  # click-through
        else:
            sw, sh = self.sprite_size()
            win.input_shape_combine_region(
                cairo.Region(cairo.RectangleInt(0, self.bubble_h, sw, sh)), 0, 0)

    # -- interaction (unlocked only) -----------------------------------------------------------

    def on_button(self, _w, ev) -> bool:
        if self.locked:
            return False
        if ev.button == 1:
            self.begin_move_drag(ev.button, int(ev.x_root), int(ev.y_root), ev.time)
        elif ev.button == 3:
            save_avatar_cfg({"locked": True})
        return True

    def on_scroll(self, _w, ev) -> bool:
        if self.locked:
            return False
        step = 0.1 if ev.direction == Gdk.ScrollDirection.UP else -0.1 if \
            ev.direction == Gdk.ScrollDirection.DOWN else 0
        if step:
            save_avatar_cfg({"scale": round(max(0.3, min(6.0, self.scale + step)), 2)})
        return True

    def on_configure(self, _w, ev) -> bool:
        # persist a drag; ignore programmatic moves right after we placed ourselves
        if not self.locked and time.time() > self._suppress_save:
            x, y = self.get_position()
            if (x, y) != (self.cfg.get("x"), self.cfg.get("y")):
                self.cfg["x"], self.cfg["y"] = x, y
                save_avatar_cfg({"x": x, "y": y})
                self._cfg_mtime = AVATAR_CFG.stat().st_mtime
        return False

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
        except (OSError, ValueError):
            pass
        fps = (self.pack.fps if self.pack else procedural.FPS).get(self.state, 1)
        if time.time() * fps - int(time.time() * fps) < 0.25:
            self.frame += 1
        if self.text and time.time() > self.text_until:
            self.text = ""
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
        if not self.locked:
            cr.set_source_rgba(1, 0.6, 0.18, 0.9)
            cr.set_line_width(2)
            cr.set_dash([6, 4])
            cr.rectangle(1, oy + 1, sw - 2, sh - 2)
            cr.stroke()
            cr.set_dash([])
            self._label(cr, 4, oy - 6, f"drag · scroll={self.scale:.1f}x · right-click locks")
        if self.text:
            self._bubble(cr, sw + 8, oy + 8, BUBBLE_W - 12, self.text)
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

    def _label(self, cr, x, y, text) -> None:
        cr.select_font_face("monospace")
        cr.set_font_size(10)
        cr.set_source_rgba(0.08, 0.08, 0.08, 0.85)
        ext = cr.text_extents(text)
        cr.rectangle(x - 3, y - ext.height - 3, ext.width + 6, ext.height + 6)
        cr.fill()
        cr.set_source_rgb(1, 0.6, 0.18)
        cr.move_to(x, y)
        cr.show_text(text)

    def _bubble(self, cr, x, y, w, text) -> None:
        import textwrap
        lines = textwrap.wrap(text, 30)[:5]
        h = 14 * len(lines) + 12
        cr.set_source_rgba(0.08, 0.08, 0.08, 0.92)
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgb(1, 0.6, 0.18)
        cr.rectangle(x, y, w, 2)
        cr.fill()
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.select_font_face("monospace")
        cr.set_font_size(11)
        for i, ln in enumerate(lines):
            cr.move_to(x + 6, y + 16 + 14 * i)
            cr.show_text(ln)


def main() -> None:
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
