"""Ask the user for a value the assistant must not invent.

A model writing a *password* is not a small bug: it hands you an account whose credentials exist
nowhere. Same for a username you care about, a card number, a verification code. Those fields get
a native popup instead — the user types it, it goes straight into the page, and it never reaches
a transcript, a log line, a step trace or a decision payload.

The dialog runs as its own process under the graphical session, and the value comes back over a
pipe. Nothing here returns the value into anything that gets serialised.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Fields nothing may guess at. Matched against the field's label/role text.
SECRET = re.compile(r"\b(password|passphrase|pin|cvv|cvc|card number|security code|"
                    r"verification code|otp|2fa|one.?time|secret|api key|token|seed phrase)\b",
                    re.IGNORECASE)
# Fields a model shouldn't decide *for* you even though they aren't secret.
YOURS = re.compile(r"\b(username|user name|display name|handle|email address|e-?mail|phone|"
                   r"mobile number|address|full name|date of birth|dob|zip|postal)\b",
                   re.IGNORECASE)

DIALOG = r'''
import os, sys
os.environ.setdefault("GDK_BACKEND", "x11")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

prompt, secret = sys.argv[1], sys.argv[2] == "1"
win = Gtk.Window(title="hearthsmith")
win.set_keep_above(True)
win.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
win.set_default_size(460, -1)
win.set_border_width(14)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
label = Gtk.Label(label=prompt)
label.set_line_wrap(True)
label.set_xalign(0)
box.pack_start(label, False, False, 0)
entry = Gtk.Entry()
entry.set_visibility(not secret)
entry.set_activates_default(True)
if secret:
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
box.pack_start(entry, False, False, 0)
row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
row.set_halign(Gtk.Align.END)
cancel, ok = Gtk.Button(label="Cancel"), Gtk.Button(label="Send")
row.pack_start(cancel, False, False, 0)
row.pack_start(ok, False, False, 0)
box.pack_start(row, False, False, 0)
win.add(box)

out = {"v": None}
def send(*_):
    out["v"] = entry.get_text()
    Gtk.main_quit()
ok.connect("clicked", send)
entry.connect("activate", send)
cancel.connect("clicked", lambda *_: Gtk.main_quit())
win.connect("destroy", Gtk.main_quit)
win.connect("key-press-event",
            lambda w, e: Gtk.main_quit() if e.keyval == Gdk.KEY_Escape else False)
win.show_all()
win.present()
entry.grab_focus()
Gtk.main()
if out["v"] is not None:
    sys.stdout.write(out["v"])
'''


def _session_env() -> dict[str, str]:
    """DISPLAY/XAUTHORITY as the live graphical session sees them."""
    out: dict[str, str] = {}
    try:
        text = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                              text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        text = ""
    for line in text.splitlines():
        k, _, v = line.partition("=")
        if k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
            out[k] = v
    for k in ("DISPLAY", "XAUTHORITY"):
        out.setdefault(k, os.environ.get(k, ""))
    return {k: v for k, v in out.items() if v}


def sensitive(label: str) -> str | None:
    """'secret', 'yours', or None — how careful to be about this field."""
    if SECRET.search(label or ""):
        return "secret"
    if YOURS.search(label or ""):
        return "yours"
    return None


def ask(prompt: str, secret: bool = True, timeout: float = 300.0) -> str | None:
    """Pop the question on the user's screen. None = cancelled, unavailable, or nothing typed.

    The return value is deliberately never logged here; callers must hand it straight to the
    field and keep it out of step traces.
    """
    # Ask the session manager where the display is rather than trusting this process's
    # environment: a shell started before a re-login still carries the previous session's
    # XAUTHORITY, and X then refuses the connection.
    env = _session_env()
    cmd = ["systemd-run", "--user", "--quiet", "--collect", "--scope",
           "--slice=app-graphical.slice",
           f"--setenv=DISPLAY={env.get('DISPLAY', ':0')}", "--setenv=GDK_BACKEND=x11"]
    if xauth := env.get("XAUTHORITY"):
        cmd.append(f"--setenv=XAUTHORITY={xauth}")
    cmd += ["/usr/bin/python3", "-c", DIALOG, prompt, "1" if secret else "0"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False,
                           env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    except (OSError, subprocess.SubprocessError):
        return None
    value = p.stdout
    return value if value else None


def _cli() -> None:
    v = ask(" ".join(sys.argv[1:]) or "Value?", secret=True)
    print("(got a value)" if v else "(cancelled)")


if __name__ == "__main__":
    _cli()
