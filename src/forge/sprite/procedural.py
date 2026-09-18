"""Placeholder blacksmith drawn from pixel strings. Same layer slots the real pack will use, so
swapping in art is a manifest change. 16x24 cells, integer-scaled by the renderer.

Palette letters map to avatar.yaml colors; '.' is transparent. Frame 0/1 = idle breathe,
forge = hammer up/down, alert = hammer raised + bubble, sleep = eyes shut."""

from __future__ import annotations

from pathlib import Path

AVATAR_CFG = Path.home() / ".config/forge/avatar.yaml"
W, H = 16, 24

DEFAULT_PALETTE = {
    "s": "#e0b090",  # skin
    "S": "#b07050",  # skin shade
    "h": "#5a3a1e",  # hair / beard
    "b": "#3a2410",  # beard shade
    "t": "#8b1e1e",  # tunic
    "T": "#5e1212",  # tunic shade
    "a": "#4a4a4a",  # apron
    "A": "#2e2e2e",  # apron shade
    "g": "#6e4b2a",  # gauntlets / boots
    "m": "#9a9a9a",  # hammer head
    "w": "#7a5230",  # hammer handle
    "e": "#101010",  # eyes / outline
    "f": "#ff9a2e",  # forge glow / sparks
    "z": "#cfd8e6",  # zzz
}

# -- base body per state (rows top→bottom). 'X' marks where the tool layer anchors. --------

_BODY_IDLE_0 = [
    "................",
    ".....hhhhhh.....",
    "....hhhhhhhh....",
    "....hsssssssh...",
    "....hsesssesh...",
    "....hsssssssh...",
    "....hbbbbbbbh...",
    ".....bbbbbb.....",
    "....ttttttttt...",
    "...ttaaaaaattt..",
    "...tsaaAAaast...",
    "..ggsaaaaaasgg..",
    "..ggsaaaaaasgg..",
    "....aaAAAAaa....",
    "....aaaaaaaa....",
    "....aAAAAAAa....",
    "....gg....gg....",
    "....gg....gg....",
    "....gg....gg....",
    "...ggg....ggg...",
    "................",
    "................",
    "................",
    "................",
]

# breathe: shift torso down one row
_BODY_IDLE_1 = ["................"] + _BODY_IDLE_0[:-1]

# forge frame 0: hammer raised (arm up right)  — reuse body, tool layer moves
_BODY_FORGE = _BODY_IDLE_0

_BODY_SLEEP = [r.replace("e", "S") for r in _BODY_IDLE_0]  # eyes shut


def _tool(raised: bool) -> list[str]:
    g = ["................" for _ in range(H)]
    if raised:
        rows = {5: "...........mmm..", 6: "...........mmm..", 7: "............w...", 8: "............w...",
                9: "............w...", 10: "............w..."}
    else:
        rows = {12: "..............w.", 13: ".............w..", 14: "............w...",
                15: "...........mmm..", 16: "...........mmm.."}
    for i, r in rows.items():
        g[i] = r
    return g


def _fx(kind: str, frame: int) -> list[str]:
    g = ["................" for _ in range(H)]
    if kind == "sparks":
        for i, r in {13: ".............f..", 14: "..............f.", 15: "...........f....", 16: "..............ff"}.items():
            g[i] = r if frame % 2 == 0 else r.replace("f", ".").replace("..............", ".............f")
    elif kind == "zzz":
        for i, r in {1: ".............z..", 2: "..............zz", 3: "...........z....", 4: "............zz.."}.items():
            g[i] = r if frame % 2 == 0 else r[1:] + "."
    return g


# state -> list of frames; frame = list of layers (bottom→top); layer = grid
STATES: dict[str, list[list[list[str]]]] = {
    "idle":  [[_BODY_IDLE_0, _tool(False)], [_BODY_IDLE_1, _tool(False)]],
    "forge": [[_BODY_FORGE, _tool(True)], [_BODY_FORGE, _tool(False), _fx("sparks", 1)]],
    "alert": [[_BODY_FORGE, _tool(True), _fx("sparks", 0)], [_BODY_FORGE, _tool(True)]],
    "sleep": [[_BODY_SLEEP, _tool(False), _fx("zzz", 0)], [_BODY_SLEEP, _tool(False), _fx("zzz", 1)]],
}

FPS = {"idle": 1.2, "forge": 4, "alert": 3, "sleep": 0.8}


def composite(state: str, frame: int) -> list[list[str | None]]:
    """Flatten layers into a WxH grid of palette keys (None = transparent)."""
    frames = STATES.get(state, STATES["idle"])
    layers = frames[frame % len(frames)]
    out: list[list[str | None]] = [[None] * W for _ in range(H)]
    for layer in layers:
        for y, row in enumerate(layer[:H]):
            for x, ch in enumerate(row[:W]):
                if ch != ".":
                    out[y][x] = ch
    return out
