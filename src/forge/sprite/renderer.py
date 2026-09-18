"""forge-sprite: GTK3 always-on-top, click-through, transparent window in the corner that polls
sprite.json and plays the matching state. XWayland path (GDK_BACKEND=x11) because Mutter has no
layer-shell; a GNOME Shell extension can replace this later — the state file contract stays.

Launch from your own terminal or via hp-gui, never from an agent pane (cgroup cap + oomd).
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
import yaml
from gi.repository import Gdk, GLib, Gtk

from forge.config import STATE_DIR
from forge.sprite import procedural
from forge.sprite.procedural import AVATAR_CFG


def _rgb(hexs: str) -> tuple[float, float, float]:
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


class Sprite(Gtk.Window):
    def __init__(self, state_file: Path, scale: int, corner: str, palette: dict[str, str]):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.state_file, self.scale = state_file, scale
        self.palette = {k: _rgb(v) for k, v in palette.items()}
        self.state, self.frame, self.text, self.text_until = "idle", 0, "", 0.0
        self._mtime = 0.0

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

        self.bubble_h = 3 * scale * 4
        w, h = procedural.W * scale, procedural.H * scale + self.bubble_h
        self.set_default_size(w + 220, h)
        self.set_size_request(w + 220, h)
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.connect("realize", lambda *_: self._place(corner, w + 220, h))
        self.connect("realize", lambda *_: self._click_through())
        GLib.timeout_add(250, self._tick)

    def _place(self, corner: str, w: int, h: int) -> None:
        mon = self.get_screen().get_display().get_primary_monitor().get_workarea()
        x = mon.x + (mon.width - w - 16 if "right" in corner else 16)
        y = mon.y + (mon.height - h - 16 if "bottom" in corner else 16)
        self.move(x, y)

    def _click_through(self) -> None:
        import cairo
        self.get_window().input_shape_combine_region(cairo.Region(), 0, 0)

    def _tick(self) -> bool:
        try:
            m = self.state_file.stat().st_mtime
            if m != self._mtime:
                self._mtime = m
                d = json.loads(self.state_file.read_text())
                new = d.get("state", "idle")
                if new != self.state:
                    self.state, self.frame = new, 0
                if d.get("text") and d.get("at", 0) > time.time() - 600:
                    self.text, self.text_until = d["text"], time.time() + 90
        except (OSError, ValueError):
            pass
        fps = procedural.FPS.get(self.state, 1)
        if time.time() * fps - int(time.time() * fps) < 0.25:
            self.frame += 1
        if self.text and time.time() > self.text_until:
            self.text = ""
        self.area.queue_draw()
        return True

    def on_draw(self, _w, cr) -> bool:
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(1)  # CAIRO_OPERATOR_SOURCE
        cr.paint()
        cr.set_operator(2)  # OVER
        s = self.scale
        grid = procedural.composite(self.state, self.frame)
        oy = self.bubble_h
        for y, row in enumerate(grid):
            for x, key in enumerate(row):
                if key and key in self.palette:
                    cr.set_source_rgb(*self.palette[key])
                    cr.rectangle(x * s, oy + y * s, s, s)
                    cr.fill()
        if self.text:
            self._bubble(cr, procedural.W * s + 8, oy + 2 * s, 200, self.text)
        return True

    def _bubble(self, cr, x, y, w, text) -> None:
        import textwrap
        lines = textwrap.wrap(text, 30)[:5]
        h = 14 * len(lines) + 12
        cr.set_source_rgba(0.08, 0.08, 0.08, 0.92)
        cr.rectangle(x, y, w, h); cr.fill()
        cr.set_source_rgb(1, 0.6, 0.18)
        cr.rectangle(x, y, w, 2); cr.fill()
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.select_font_face("monospace"); cr.set_font_size(11)
        for i, ln in enumerate(lines):
            cr.move_to(x + 6, y + 16 + 14 * i); cr.show_text(ln)


def main() -> None:
    ap = argparse.ArgumentParser(prog="forge-sprite")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--corner", default="bottom-right")
    ap.add_argument("--state-file", type=Path, default=STATE_DIR / "sprite.json")
    a = ap.parse_args()
    palette = dict(procedural.DEFAULT_PALETTE)
    if AVATAR_CFG.exists():
        palette.update((yaml.safe_load(AVATAR_CFG.read_text()) or {}).get("palette", {}))
    win = Sprite(a.state_file, a.scale, a.corner, palette)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
