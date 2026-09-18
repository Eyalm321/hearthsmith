"""What he's doing right now, on his own face.

Both bodies (Chrome and the desktop) narrate through here, so the avatar shows the same thing
whichever one is working. Progress lines are written `instant`: they change every few hundred
milliseconds and typing them out one character at a time would never finish a sentence.
"""

from __future__ import annotations

import re

from forge import config
from forge.sinks import SpriteSink

# CDP and AT-SPI label the same intent differently; say it the way a person would.
VERBS = {"click": "clicking", "fill": "typing", "type": "typing", "select": "choosing",
         "scroll": "scrolling", "wait": "waiting", "navigate": "opening", "goto": "opening",
         "launch": "opening", "focus": "switching to", "key": "pressing", "commit": "confirming"}
NOISE = re.compile(r"\s*\[p=[\d.]+,\s*[\d.]+ms\]\s*$")


class Narrator:
    """Cheap enough to call on every step; a no-op when the avatar isn't running."""

    def __init__(self, cfg: config.Config | None = None):
        cfg = cfg or config.load()
        self.sprite = SpriteSink(cfg.sprite_path)
        self.on = self.sprite.alive()
        self.last = ""

    def step(self, line: str, state: str = "forge") -> None:
        if not self.on:
            return
        line = NOISE.sub("", line).strip()
        if not line or line == self.last:
            return
        self.last = line
        verb, _, rest = line.partition(" ")
        said = f"{VERBS.get(verb.lower(), verb)} {rest}".strip() if rest else line
        self.sprite.write(state, said[:110], "soon", instant=True)

    def done(self, text: str) -> None:
        if self.on:
            self.sprite.write("idle", text[:160], "ignorable", instant=True)

    def failed(self, why: str) -> None:
        if self.on:
            self.sprite.write("alert", why[:160], "now", instant=True)
