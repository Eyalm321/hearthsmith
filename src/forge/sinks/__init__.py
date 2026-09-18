"""Delivery channels. Each sink: send(text, urgency, task) -> bool. Failures never raise."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from forge.adapters.hyperpanes import Hyperpanes
from forge.store import Task


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


class SpriteSink:
    """Not a delivery channel — writes the avatar state file the renderer polls."""

    name = "sprite"

    def __init__(self, path: Path):
        self.path = path

    def write(self, state: str, text: str = "", urgency: str = "ignorable") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"state": state, "text": text, "urgency": urgency,
                                         "at": int(time.time())}))
