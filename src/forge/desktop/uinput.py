"""Real pointer + keyboard over /dev/uinput (pure ctypes; the keyboard half is lifted from
hebrew-dictate/uinput_kbd.py). Absolute-position mouse so a click lands on AT-SPI desktop
coordinates across all monitors. Needs the user in the `input` group.

Text goes in via the clipboard + ctrl+v (wl-copy), like hebrew-dictate — key-by-key typing
would need a keymap and breaks on non-ASCII."""

from __future__ import annotations

import fcntl
import os
import struct
import subprocess
import time

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
ABS_X, ABS_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
REL_WHEEL = 0x08
EV_REL = 0x02

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_DEV_SETUP = 0x405C5503
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045567, 0x40045566
UI_ABS_SETUP = 0x401C5504  # _IOW('U', 4, struct uinput_abs_setup /* 28 bytes */)
BUS_USB = 0x03

KEYS = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125, "enter": 28, "escape": 1, "tab": 15,
        "space": 57, "backspace": 14, "delete": 111, "up": 103, "down": 108, "left": 105,
        "right": 106, "home": 102, "end": 107, "pageup": 104, "pagedown": 109, "f5": 63,
        "a": 30, "c": 46, "v": 47, "l": 38, "t": 20, "w": 17, "f": 33, "r": 19}

ABS_MAX = 65535


def _ioc_abs_setup(code: int, maximum: int) -> bytes:
    # struct uinput_abs_setup { __u16 code; struct input_absinfo { __s32 value,min,max,fuzz,flat,res } }
    return struct.pack("<HxxiiiIii", code, 0, 0, maximum, 0, 0, 0)


class Pointer:
    """Absolute mouse spanning the virtual desktop (desk_w × desk_h in px)."""

    def __init__(self, desk_w: int, desk_h: int, name: str = "forge pointer"):
        self.dw, self.dh = desk_w, desk_h
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        for b in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, b)
        fcntl.ioctl(self.fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(self.fd, UI_SET_ABSBIT, ABS_Y)
        fcntl.ioctl(self.fd, UI_SET_RELBIT, REL_WHEEL)
        fcntl.ioctl(self.fd, UI_ABS_SETUP, _ioc_abs_setup(ABS_X, ABS_MAX))
        fcntl.ioctl(self.fd, UI_ABS_SETUP, _ioc_abs_setup(ABS_Y, ABS_MAX))
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("<HHHH80sI", BUS_USB, 0x1209, 0x4843, 1, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.4)  # libinput enumerates the device

    def _emit(self, t, c, v):
        os.write(self.fd, struct.pack("@llHHi", 0, 0, t, c, v))

    def _syn(self):
        self._emit(EV_SYN, SYN_REPORT, 0)

    def move(self, x: int, y: int) -> None:
        self._emit(EV_ABS, ABS_X, int(x * ABS_MAX / max(1, self.dw - 1)))
        self._emit(EV_ABS, ABS_Y, int(y * ABS_MAX / max(1, self.dh - 1)))
        self._syn()

    def click(self, x: int, y: int, button: int = BTN_LEFT, n: int = 1) -> None:
        self.move(x, y)
        time.sleep(0.05)
        for _ in range(n):
            self._emit(EV_KEY, button, 1); self._syn()
            time.sleep(0.03)
            self._emit(EV_KEY, button, 0); self._syn()
            time.sleep(0.05)

    def wheel(self, clicks: int) -> None:
        for _ in range(abs(clicks)):
            self._emit(EV_REL, REL_WHEEL, -1 if clicks > 0 else 1); self._syn()
            time.sleep(0.02)

    def close(self) -> None:
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None


class Keyboard:
    def __init__(self, name: str = "forge keyboard"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        for code in sorted(set(KEYS.values())):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("<HHHH80sI", BUS_USB, 0x1209, 0x4842, 1, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.4)

    def _emit(self, t, c, v):
        os.write(self.fd, struct.pack("@llHHi", 0, 0, t, c, v))

    def _syn(self):
        self._emit(EV_SYN, SYN_REPORT, 0)

    def tap(self, combo: str, hold: float = 0.02) -> None:
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        mods, key = [KEYS[p] for p in parts[:-1]], KEYS[parts[-1]]
        for m in mods:
            self._emit(EV_KEY, m, 1); self._syn(); time.sleep(0.005)
        self._emit(EV_KEY, key, 1); self._syn(); time.sleep(hold)
        self._emit(EV_KEY, key, 0); self._syn()
        for m in reversed(mods):
            self._emit(EV_KEY, m, 0); self._syn()

    def type_text(self, text: str, enter: bool = False, select_all_first: bool = True) -> None:
        subprocess.run(["wl-copy", "--", text], check=False, timeout=5)
        time.sleep(0.05)
        if select_all_first:
            self.tap("ctrl+a"); time.sleep(0.03)
        self.tap("ctrl+v")
        time.sleep(0.08)
        if enter:
            self.tap("enter")

    def close(self) -> None:
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None


def desktop_size() -> tuple[int, int]:
    """Bounding box of all monitors (GDK), for the absolute pointer."""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    d = Gdk.Display.get_default()
    w = h = 0
    for i in range(d.get_n_monitors()):
        g = d.get_monitor(i).get_geometry()
        w, h = max(w, g.x + g.width), max(h, g.y + g.height)
    return w or 1920, h or 1080
