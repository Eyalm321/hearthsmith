"""Render every state/frame to a PNG contact sheet without a display. For checking art in a
transcript or CI: `forge-sprite-preview out.png`."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

from forge.sprite import procedural
from forge.sprite.procedural import AVATAR_CFG


def render(out: Path, scale: int = 6) -> Path:
    palette = dict(procedural.DEFAULT_PALETTE)
    if AVATAR_CFG.exists():
        palette.update((yaml.safe_load(AVATAR_CFG.read_text()) or {}).get("palette", {}))
    states = list(procedural.STATES)
    cols = max(len(f) for f in procedural.STATES.values())
    cw, ch = procedural.W * scale + 8, procedural.H * scale + 20
    img = Image.new("RGBA", (cols * cw, len(states) * ch), (40, 40, 48, 255))
    d = ImageDraw.Draw(img)
    for r, st in enumerate(states):
        d.text((4, r * ch + 2), st, fill=(220, 220, 220))
        for c in range(len(procedural.STATES[st])):
            grid = procedural.composite(st, c)
            ox, oy = c * cw + 4, r * ch + 16
            for y, row in enumerate(grid):
                for x, key in enumerate(row):
                    if key and key in palette:
                        d.rectangle([ox + x * scale, oy + y * scale, ox + (x + 1) * scale - 1,
                                     oy + (y + 1) * scale - 1], fill=palette[key])
    img.save(out)
    return out


def main() -> None:
    print(render(Path(sys.argv[1] if len(sys.argv) > 1 else "sprite-preview.png")))


if __name__ == "__main__":
    main()


def icon(out: Path, size: int = 256) -> Path:
    """Idle frame on transparent, for the .desktop launcher."""
    palette = dict(procedural.DEFAULT_PALETTE)
    if AVATAR_CFG.exists():
        palette.update((yaml.safe_load(AVATAR_CFG.read_text()) or {}).get("palette", {}))
    scale = size // procedural.H
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox = (size - procedural.W * scale) // 2
    oy = (size - procedural.H * scale) // 2
    for y, row in enumerate(procedural.composite("idle", 0)):
        for x, key in enumerate(row):
            if key and key in palette:
                d.rectangle([ox + x * scale, oy + y * scale, ox + (x + 1) * scale - 1,
                             oy + (y + 1) * scale - 1], fill=palette[key])
    img.save(out)
    return out


def icon_main() -> None:
    print(icon(Path(sys.argv[1] if len(sys.argv) > 1 else "forge.png")))
