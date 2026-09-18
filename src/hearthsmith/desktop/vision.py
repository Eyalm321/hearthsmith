"""The verifier. Jev decides what to do; it cannot see, so "is this actually done?" is answered
by a screenshot and a local vision model — once per task, not per step.

This exists because the accessibility tree can be honestly ambiguous: after filling a flight
search, the readable text is `$88 $65 $55 …`, which is either flight prices or the date picker's
per-day prices. One look settles it.

Capture goes through the hearthsmith-windows GNOME extension (Wayland gives clients no way to read the
screen; the desktop portal prompts on every call). Vision is Ornith on the Mac via Ollama —
`/api/chat` with `images`. Both are optional: no extension or no model means `verify()` returns
None and the caller keeps its own verdict rather than inventing one.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

import httpx

from hearthsmith.config import ComposeCfg

MAX_EDGE = 1400          # Ornith reads UI fine at this width; full 4K frames just cost time


def capture(rect: tuple[int, int, int, int] | None = None, timeout: float = 15.0) -> Path | None:
    """PNG of a screen region (None = whole screen), or None when the extension isn't loaded."""
    x, y, w, h = rect or (0, 0, 0, 0)
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
             "--object-path", "/org/hearthsmith/Windows", "--method", "org.hearthsmith.Windows.Screenshot",
             str(int(x)), str(int(y)), str(int(w)), str(int(h))],
            capture_output=True, text=True, timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"\('(.+)',\)", out)
    if not m or m.group(1).startswith("ERROR"):
        return None
    p = Path(m.group(1))
    return p if p.exists() and p.stat().st_size > 1000 else None


def _shrink(p: Path) -> str:
    from PIL import Image
    im = Image.open(p).convert("RGB")
    if max(im.size) > MAX_EDGE:
        s = MAX_EDGE / max(im.size)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def look(cfg: ComposeCfg, image: Path, question: str, timeout: float = 120.0) -> str | None:
    try:
        r = httpx.post(f"{cfg.ollama_url}/api/chat", timeout=timeout,
                       json={"model": cfg.ollama_model, "stream": False, "think": False,
                             "options": {"temperature": 0.1, "num_predict": 120},
                             "messages": [{"role": "user", "content": question,
                                           "images": [_shrink(image)]}]})
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, ValueError, OSError):
        return None


def verify(cfg: ComposeCfg, goal: str, rect: tuple[int, int, int, int] | None = None
           ) -> tuple[bool | None, str]:
    """(verdict, why). None means "couldn't look" — never a failure on its own."""
    shot = capture(rect)
    if shot is None:
        return None, "no screenshot (hearthsmith-windows extension not loaded?)"
    ans = look(cfg, shot, "Look at this screenshot of an application.\n"
               f"The user asked for: {goal}\n\n"
               "Is that visibly done on screen right now? Beware of a dialog or date picker "
               "still open over the result, or a form that is filled in but not submitted.\n"
               'Answer with exactly one line: "YES: <what you see>" or "NO: <what is missing>".')
    try:
        shot.unlink()
    except OSError:
        pass
    if not ans:
        return None, "vision model unavailable"
    head = ans.strip().strip('"').upper()
    if head.startswith("YES"):
        return True, ans[:200]
    if head.startswith("NO"):
        return False, ans[:200]
    return None, f"unclear answer: {ans[:120]}"


def available() -> bool:
    try:
        out = subprocess.run(["gdbus", "introspect", "--session", "--dest", "org.gnome.Shell",
                              "--object-path", "/org/hearthsmith/Windows"],
                             capture_output=True, text=True, timeout=5, check=False).stdout
        return "Screenshot" in out
    except (OSError, subprocess.SubprocessError):
        return False


def _cli() -> None:
    """hearthsmith-look "<goal>" — take one look and say whether it is visibly done."""
    import sys

    from hearthsmith import config
    ok, why = verify(config.load().compose, " ".join(sys.argv[1:]) or "anything")
    print(json.dumps({"verdict": ok, "why": why}))


if __name__ == "__main__":
    _cli()
