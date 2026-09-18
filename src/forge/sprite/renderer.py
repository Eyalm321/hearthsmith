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
    for k, v in patch.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
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
        self._suppress_save = 0.0
        self._press: tuple[int, int, int] | None = None  # x_root, y_root, time
        self._dragging = False

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
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.on_scroll)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
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
            self._press = (int(ev.x_root), int(ev.y_root), ev.time)
            self._dragging = False
        elif ev.button == 3:
            self.open_menu(ev)
        return True

    def on_motion(self, _w, ev) -> bool:
        if self._press and not self._dragging and ev.state & Gdk.ModifierType.BUTTON1_MASK:
            x0, y0, t0 = self._press
            if abs(ev.x_root - x0) > self.DRAG_PX or abs(ev.y_root - y0) > self.DRAG_PX:
                self._dragging = True
                self.begin_move_drag(1, x0, y0, t0)
        return True

    def on_release(self, _w, ev) -> bool:
        if ev.button == 1 and self._press and not self._dragging:
            self.open_prompt()
        self._press = None
        return True

    def on_configure(self, _w, ev) -> bool:
        # persist a drag; ignore programmatic moves right after we placed ourselves
        if time.time() > self._suppress_save:
            x, y = self.get_position()
            if (x, y) != (self.cfg.get("x"), self.cfg.get("y")):
                self.cfg["x"], self.cfg["y"] = x, y
                self.cfg.pop("corner", None)
                save_avatar_cfg({"x": x, "y": y, "corner": None})
                self._cfg_mtime = AVATAR_CFG.stat().st_mtime
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
        item("Nag me now", lambda: self._systemctl("start", "forged.service"))
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
        w.set_default_size(340, 36)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Tell the smith…  (Enter to send, Esc to close)")
        entry.set_size_request(340, 36)
        w.add(entry)
        x, y = self.get_position()
        sw, sh = self.sprite_size()
        w.move(x + sw + 8, y + self.bubble_h + sh - 40)
        entry.connect("activate", lambda e: self._submit(e.get_text()))
        w.connect("key-press-event", lambda _w, ev: self._close_prompt() if ev.keyval == Gdk.KEY_Escape else False)
        w.connect("focus-out-event", lambda *_: self._close_prompt())
        w.connect("destroy", lambda *_: setattr(self, "_prompt", None))
        self._prompt = w
        w.show_all()
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
        self.text, self.text_until = f"[{r['intent']}] {r['text']}", time.time() + 45
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
