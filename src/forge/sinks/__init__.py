"""Delivery channels. Each sink: send(text, urgency, task) -> bool. Failures never raise."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from forge.adapters.hyperpanes import Hyperpanes
from forge.config import VoiceCfg
from forge.store import Task
from forge.voice import Voice


class NotifySink:
    name = "notify"

    def send(self, text: str, urgency: str, task: Task | None) -> bool:
        level = {"now": "critical", "soon": "normal"}.get(urgency, "low")
        try:
            subprocess.run(["notify-send", "-a", "forge", "-u", level, "-i", "applications-engineering",
                            "The forge", text], check=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            return False


class HyperpanesSink:
    """Out-of-band message to the most relevant pane's agent (project match > busy > first)."""

    name = "hyperpanes"

    def __init__(self, hp: Hyperpanes):
        self.hp = hp

    def send(self, text: str, urgency: str, task: Task | None) -> bool:
        snap = self.hp.snapshot(with_screens=False)
        if not snap or not snap.panes:
            return False
        target = None
        if task and task.project:
            for p in snap.panes:
                pr = snap.project_for_cwd(p.cwd)
                if pr and (pr["id"] == task.project or pr["name"] == task.project):
                    target = p
                    break
        if target is None:
            target = next((p for p in snap.panes if p.activity == "busy"), snap.panes[0])
        try:
            return self.hp.message(target.id, text)
        except Exception:  # noqa: BLE001 — delivery must never take the daemon down
            return False


class VoiceSink:
    """He says it out loud, in the cloned dwarf voice. Additive: the text still goes to the
    sprite/pane, sound is on top — so it never counts as the only delivery."""

    name = "voice"

    def __init__(self, cfg: VoiceCfg):
        self.voice = Voice(cfg)

    def send(self, text: str, urgency: str, task: Task | None) -> bool:
        try:
            return self.voice.speak(text)
        except Exception:  # noqa: BLE001 — delivery must never take the daemon down
            return False


class SpriteSink:
    """The avatar. Writes the state file the renderer polls; counts as delivered only when the
    renderer is alive (it touches sprite.alive every 2s), so notify-send can take over when he's
    hidden."""

    name = "sprite"

    def __init__(self, path: Path):
        self.path = path
        self.alive_file = path.parent / "sprite.alive"

    def alive(self) -> bool:
        try:
            return time.time() - self.alive_file.stat().st_mtime < 6
        except OSError:
            return False

    def send(self, text: str, urgency: str, task: Task | None) -> bool:
        if not self.alive():
            return False
        self.write("alert" if urgency == "now" else "forge", text, urgency)
        return True

    def write(self, state: str, text: str = "", urgency: str = "ignorable",
              instant: bool = False) -> None:
        """`instant` skips the typewriter: a nag is worth typing out, a running commentary that
        changes every few hundred milliseconds is not."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"state": state, "text": text, "urgency": urgency,
                                         "instant": instant, "at": int(time.time())}))
