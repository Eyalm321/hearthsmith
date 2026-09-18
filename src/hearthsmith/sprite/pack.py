"""A sprite pack: PNG frames + manifest.json (from hearthsmith-sprite-slice). Same interface as
`procedural` — states, fps, frame count — so the renderer doesn't care which it got."""

from __future__ import annotations

import json
from pathlib import Path

from hearthsmith.config import STATE_DIR
from hearthsmith.sprite import procedural

DEFAULT_PACK = Path.home() / ".config/hearthsmith/pack"


class Pack:
    def __init__(self, root: Path):
        self.root = root
        m = json.loads((root / "manifest.json").read_text())
        self.cell: int = m["cell"]
        self.states: dict[str, list[Path]] = {s: [root / f for f in fs] for s, fs in m["states"].items()}
        self.fps: dict[str, float] = {**procedural.FPS, **m.get("fps", {})}

    def frames(self, state: str) -> list[Path]:
        return self.states.get(state) or self.states.get("idle") or []

    @property
    def size(self) -> tuple[int, int]:
        return self.cell, self.cell


def load(path: Path | None = None) -> Pack | None:
    for p in ([path] if path else []) + [DEFAULT_PACK, STATE_DIR / "pack"]:
        if p and (p / "manifest.json").exists():
            return Pack(p)
    return None
