"""Cut a generated 4x2 sprite sheet into a forge pack: 8 cells → per-state PNG frames + manifest.

    hearthsmith-sprite-slice sheet.png ~/.config/hearthsmith/pack [--chroma FF00FF] [--cell 96]

Cells are found by connected components on alpha (after optional chroma-key), sorted into
reading order, then each is trimmed, scaled so the tallest cell = --cell px (nearest neighbour,
never smooth), bottom-aligned on a square canvas, and written as <state>_<n>.png.
Order is the prompt's: idle A, idle B, forge up, forge down, alert A, alert B, sleep A, sleep B.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ORDER = [("idle", 0), ("idle", 1), ("forge", 0), ("forge", 1),
         ("alert", 0), ("alert", 1), ("sleep", 0), ("sleep", 1)]


def chroma_to_alpha(im: Image.Image, hexcol: str, tol: int = 60) -> Image.Image:
    r0, g0, b0 = (int(hexcol[i:i + 2], 16) for i in (0, 2, 4))
    im = im.convert("RGBA")
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, _a = px[x, y]
            if abs(r - r0) + abs(g - g0) + abs(b - b0) < tol:
                px[x, y] = (0, 0, 0, 0)
    return im


def components(alpha: Image.Image, min_area: int = 400) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of opaque blobs, via a coarse grid flood fill (fast enough for 1.5k px)."""
    w, h = alpha.size
    a = alpha.load()
    seen = bytearray(w * h)
    boxes = []
    for sy in range(h):
        for sx in range(w):
            if seen[sy * w + sx] or a[sx, sy] < 16:
                continue
            stack = [(sx, sy)]
            x0 = x1 = sx
            y0 = y1 = sy
            n = 0
            while stack:
                x, y = stack.pop()
                if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x] or a[x, y] < 16:
                    continue
                seen[y * w + x] = 1
                n += 1
                x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
                stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1),
                              (x + 2, y), (x - 2, y), (x, y + 2), (x, y - 2)))
            if n >= min_area:
                boxes.append((x0, y0, x1 + 1, y1 + 1))
    return boxes


def merge_boxes(boxes, gap: int) -> list[tuple[int, int, int, int]]:
    """Sparks, Z's and '!' are separate blobs near their body — merge boxes within `gap` px."""
    boxes = sorted(boxes)
    merged = True
    while merged:
        merged = False
        out = []
        while boxes:
            b = boxes.pop(0)
            i = 0
            while i < len(boxes):
                c = boxes[i]
                if not (b[2] + gap < c[0] or c[2] + gap < b[0] or b[3] + gap < c[1] or c[3] + gap < b[1]):
                    b = (min(b[0], c[0]), min(b[1], c[1]), max(b[2], c[2]), max(b[3], c[3]))
                    boxes.pop(i)
                    merged = True
                else:
                    i += 1
            out.append(b)
        boxes = out
    return boxes


def reading_order(boxes, rows: int) -> list[tuple[int, int, int, int]]:
    boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    per = max(1, round(len(boxes) / rows))
    out = []
    for r in range(rows):
        out += sorted(boxes[r * per:(r + 1) * per], key=lambda b: b[0])
    return out


def grid_boxes(im: Image.Image, cols: int, rows: int) -> list[tuple[int, int, int, int]]:
    """Fixed grid, each cell trimmed to its alpha bbox. Generators lay a regular grid but the
    figures touch across cell borders, so blob detection merges neighbours; the grid doesn't."""
    cw, ch = im.width / cols, im.height / rows
    boxes = []
    for r in range(rows):
        for c in range(cols):
            cell = im.crop((round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)))
            bb = cell.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
            if bb is None:
                raise SystemExit(f"empty cell at row {r} col {c}")
            boxes.append((round(c * cw) + bb[0], round(r * ch) + bb[1],
                          round(c * cw) + bb[2], round(r * ch) + bb[3]))
    return boxes


def slice_sheet(sheet: Path, out: Path, cell: int, chroma: str | None, rows: int = 2,
                cols: int = 4, blobs: bool = False) -> dict:
    im = Image.open(sheet).convert("RGBA")
    if chroma:
        im = chroma_to_alpha(im, chroma)
    if blobs:
        boxes = reading_order(merge_boxes(components(im.getchannel("A")), gap=40), rows)
    else:
        boxes = grid_boxes(im, cols, rows)
    if len(boxes) != len(ORDER):
        raise SystemExit(f"found {len(boxes)} cells, expected {len(ORDER)}: {boxes}")
    tallest = max(b[3] - b[1] for b in boxes)
    scale = cell / tallest
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"cell": cell, "states": {}, "fps": {"idle": 1.2, "forge": 4, "alert": 3, "sleep": 0.8}}
    for (state, n), b in zip(ORDER, boxes):
        crop = im.crop(b)
        w, h = max(1, round(crop.width * scale)), max(1, round(crop.height * scale))
        crop = crop.resize((w, h), Image.NEAREST)
        canvas = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
        canvas.paste(crop, ((cell - w) // 2, cell - h), crop)
        name = f"{state}_{n}.png"
        canvas.save(out / name)
        manifest["states"].setdefault(state, []).append(name)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(prog="hearthsmith-sprite-slice")
    ap.add_argument("sheet", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--cell", type=int, default=96)
    ap.add_argument("--chroma", help="hex colour to key out, e.g. FF00FF; omit if the PNG has alpha")
    ap.add_argument("--blobs", action="store_true", help="detect cells by blobs instead of a fixed grid")
    a = ap.parse_args()
    m = slice_sheet(a.sheet, a.out, a.cell, a.chroma, blobs=a.blobs)
    print(json.dumps(m["states"]))


if __name__ == "__main__":
    main()
