"""Run a goal in the user's Chrome through jev-ultrafast, with Jev reached the way this machine
can reach it. Import applies the patches; `run()` is the whole API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from forge import config


@dataclass
class Result:
    ok: bool
    steps: list[str] = field(default_factory=list)
    url: str = ""
    title: str = ""
    note: str = ""
    elapsed_ms: int = 0
    decide_ms: list[float] = field(default_factory=list)
    seen: str = ""

    @property
    def median_decision_ms(self) -> float:
        import statistics
        return round(statistics.median(self.decide_ms), 1) if self.decide_ms else 0.0


def _patch(cfg: config.Config) -> None:
    """Point the decision call at OpenRouter and stop the tab hiding. Both upstream call sites
    are single lines; wrapping them keeps `pip install -U jev-ultrafast` working."""
    from jev_ultrafast import browser as jb
    from jev_ultrafast import model as jm

    if not getattr(jm, "_forge_patched", False):
        upstream_post = jm.post_json
        or_base = cfg.decide.adapter_base_url.rstrip("/").removesuffix("/v1")

        def post_json(url: str, key: str, body: dict):
            if "typesafe.ai" in url:
                body = {**body, "model": cfg.decide.openrouter_slug, "session_id": "forge-browse"}
                return upstream_post(f"{or_base}/alpha/decisions",
                                     os.environ[cfg.decide.adapter_key_env], body)
            return upstream_post(url, key, body)

        jm.post_json = post_json
        jm._forge_patched = True

    if not getattr(jb, "_forge_patched", False):
        upstream_cdp = jb.cdp

        def cdp(method: str, **params):
            if method == "Target.createTarget":
                params["background"] = False    # he works where you can see him
            return upstream_cdp(method, **params)

        jb.cdp = cdp
        jb._forge_patched = True


def cdp_ws(port: int, timeout: float = 2.0) -> str | None:
    """The browser's WebSocket endpoint, or None when nothing is listening."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return json.loads(r.read())["webSocketDebuggerUrl"]
    except (OSError, ValueError, KeyError):
        return None


def ensure_chrome(cfg: config.Config, wait: float = 20.0) -> str | None:
    """Make sure his Chrome is up and return its CDP endpoint. browser-harness only scans the
    standard profile directories, so a dedicated profile has to be handed over explicitly via
    BU_CDP_WS — which is also the documented path for a remote browser."""
    import subprocess
    import time as _t
    b = cfg.browser
    if ws := cdp_ws(b.cdp_port):
        return ws
    if not b.autostart:
        return None
    b.profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        ["systemd-run", "--user", "--quiet", "--collect", "--scope",
         "--slice=app-graphical.slice", b.binary, f"--user-data-dir={b.profile}",
         f"--remote-debugging-port={b.cdp_port}", "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = _t.time() + wait
    while _t.time() < deadline:
        _t.sleep(0.5)
        if ws := cdp_ws(b.cdp_port):
            return ws
    return None


def available() -> bool:
    try:
        import jev_ultrafast  # noqa: F401
        from browser_harness.admin import ensure_daemon  # noqa: F401
    except ImportError:
        return False
    return True


def run(goal: str, url: str | None = None, cfg: config.Config | None = None,
        max_seconds: float = 90.0) -> Result:
    """Drive Chrome toward `goal`. `url` is the page to start from; without one the goal's own
    address (or a search for it) is used, the same resolution the desktop agent does."""
    cfg = cfg or config.load()
    if not available():
        return Result(False, note="jev-ultrafast not installed")
    from forge.desktop.agent import _navigate_url

    start = url or _navigate_url(goal, cfg, in_browser=True)
    if not start:
        return Result(False, note="no address in the goal")

    ws = ensure_chrome(cfg)
    if not ws:
        return Result(False, note=f"his Chrome isn't reachable on :{cfg.browser.cdp_port}")
    os.environ["BU_CDP_WS"] = ws
    _patch(cfg)
    # the text helper writes field values; point it at the same model the smith speaks with
    os.environ.setdefault("TEXT_MODEL_BASE_URL", cfg.compose.fallback_base_url)
    os.environ.setdefault("TEXT_MODEL", cfg.compose.fallback_model)
    os.environ.setdefault("TEXT_MODEL_REASONING", "none")
    if key := os.environ.get(cfg.decide.adapter_key_env):
        os.environ.setdefault("TEXT_MODEL_API_KEY", key)
    os.environ.setdefault("TYPESAFE_API_KEY", "via-openrouter")   # unused, upstream reads it

    from jev_ultrafast import Agent

    res = Result(False)
    try:
        with Agent(start, goal) as agent:
            for state in agent.run():
                res.elapsed_ms = state.get("elapsed_ms", res.elapsed_ms)
                # `decision` is cleared once acted on, so the trace lives in `history`:
                # one entry per executed action, with the decision that produced it.
                for h in state.get("history", [])[len(res.steps):]:
                    line = f"{h.get('kind', '?')} {h.get('action', '')}".strip()
                    if h.get("text"):
                        line += f" = {h['text']!r}"
                    p = h.get("probability")
                    lat = h.get("latency_ms")
                    if p is not None and lat is not None:
                        line += f"  [p={p:.2f}, {lat:.0f}ms]"
                    res.steps.append(line[:140])
                res.decide_ms = [h["latency_ms"] for h in state.get("history", [])
                                 if h.get("latency_ms") is not None]
                page = state.get("page") or {}
                res.url = page.get("url") or state.get("url") or res.url
                res.title = page.get("title") or state.get("title") or res.title
                status = state.get("status", "")
                if status in ("done", "blocked", "error"):
                    res.ok = status == "done"
                    res.note = "" if res.ok else status
                    break
                if res.elapsed_ms > max_seconds * 1000:
                    res.note = "time limit"
                    break
    except Exception as e:  # noqa: BLE001 — a browser failure is a result, not a crash
        res.note = f"{type(e).__name__}: {e}"[:200]
    return res
