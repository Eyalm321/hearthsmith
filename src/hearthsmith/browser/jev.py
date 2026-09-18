"""Run a goal in the user's Chrome through jev-ultrafast, with Jev reached the way this machine
can reach it. Import applies the patches; `run()` is the whole API."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from hearthsmith import config

# Nothing here is solvable by an agent: park the tab and hand it over.
# Phrases a challenge page actually uses. The bare word "captcha" is not one of them — it shows
# up in privacy policies and cookie banners, and treating that as a wall stops honest pages.
GATES = ("verify you are human", "i'm not a robot", "complete the captcha",
         "unusual traffic from your computer", "enter the code we sent",
         "confirm your identity to continue", "solve this puzzle", "checking your browser before")


@dataclass
class Result:
    ok: bool
    steps: list[str] = field(default_factory=list)
    url: str = ""
    title: str = ""
    note: str = ""
    elapsed_ms: int = 0
    page_text: str = ""      # what was on the page when it finished — the raw material for an answer
    decide_ms: list[float] = field(default_factory=list)
    seen: str = ""

    @property
    def median_decision_ms(self) -> float:
        import statistics
        return round(statistics.median(self.decide_ms), 1) if self.decide_ms else 0.0


_keep_tab = [True]        # flipped per run, read by the patched cdp on Agent exit


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
                body = {**body, "model": cfg.decide.openrouter_slug, "session_id": "hearthsmith-browse"}
                return upstream_post(f"{or_base}/alpha/decisions",
                                     os.environ[cfg.decide.adapter_key_env], body)
            return upstream_post(url, key, body)

        jm.post_json = post_json
        jm._forge_patched = True

    if not getattr(jm, "_forge_asks", False):
        upstream_text = jm.field_text

        def field_text(context):
            """A password is not a field to be guessed: an invented one hands back an account
            whose credentials exist nowhere. Those go to the user, and the answer is typed
            without passing through any log, trace or decision payload."""
            from hearthsmith import ask as asker
            field = (context or {}).get("field") or {}
            label = " ".join(str(field.get(k) or "") for k in ("label", "role"))
            kind = asker.sensitive(label)
            if kind:
                what = field.get("label") or "this field"
                site = ((context or {}).get("page") or {}).get("title", "")
                answer = asker.ask(f"{what}\n\n{site}".strip(), secret=kind == "secret")
                if answer:
                    return {"text": answer, "model": "you", "sensitive": True}
                raise ValueError(f"{what}: needs you — nothing was typed")
            return upstream_text(context)

        jm.field_text = field_text
        jm._forge_asks = True

    if not getattr(jb, "_forge_patched", False):
        upstream_cdp = jb.cdp

        def cdp(method: str, **params):
            if method == "Target.createTarget":
                params["background"] = False    # he works where you can see him
            if method == "Target.closeTarget" and _keep_tab[0]:
                # Upstream tidies up after itself; an assistant is supposed to leave the answer
                # on screen. Failed runs still close, so a dead end doesn't pile up tabs.
                return {}
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
         f"--remote-debugging-port={b.cdp_port}", "--no-first-run", "--no-default-browser-check",
         # Chrome builds its accessibility tree lazily and stays invisible to AT-SPI without
         # this. It is what lets the desktop body reach what the DOM snapshot cannot — notably
         # cross-origin iframes, which is where signup forms live.
         "--force-renderer-accessibility"],
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


STAGES = re.compile(r",?\s+(?:and )?then\s+|\s+->\s+", re.IGNORECASE)


def run(goal: str, url: str | None = None, cfg: config.Config | None = None,
        max_seconds: float = 90.0, attempts: int = 2) -> Result:
    """Run a goal, one stage at a time.

    A compound goal is answered badly: asked to "pick the Free plan, then set the username", it
    picked the billing-cycle toggle and gave up, where "choose the Free plan" alone hits the
    right card at p=1.00. Each stage gets its own completion test, and the page carries over.
    """
    stages = [g.strip() for g in STAGES.split(goal) if g.strip()]
    if len(stages) < 2:
        return _attempt(goal, url, cfg, max_seconds, attempts)
    out = Result(False)
    for i, stage in enumerate(stages):
        # A later stage continues where the last one landed. Left to resolve its own start it
        # searches the web for its own instructions ("set the username field to …").
        start = url if i == 0 else (out.url or url)
        r = _attempt(stage, start, cfg, max_seconds, attempts)
        out.steps += [f"[{i + 1}/{len(stages)}] {s}" for s in r.steps]
        out.elapsed_ms += r.elapsed_ms
        out.decide_ms += r.decide_ms
        out.url, out.title, out.page_text = r.url or out.url, r.title or out.title, r.page_text
        if not r.ok:
            out.note = f"stage {i + 1} ({stage[:40]}): {r.note}"
            return out
    out.ok = True
    return out


def _attempt(goal: str, url: str | None, cfg: config.Config | None,
             max_seconds: float, attempts: int) -> Result:
    """A page that is still navigating when the agent attaches raises StalePage out of the loop;
    that is a timing accident, not a failure, so it is worth one more go."""
    for _ in range(attempts):
        res = _run_once(goal, url, cfg, max_seconds)
        if res.ok:
            return res
        transient = "stalepage" in res.note.lower()
        # A heavy page (Proton's plan chooser takes seconds) can look like a dead end on the
        # first observation: giving up in a couple of seconds with nothing done is not a verdict.
        too_soon = len(res.steps) <= 2 and res.elapsed_ms < 15000
        if not (transient or too_soon):
            return res
        time.sleep(4.0)
    return res


def bring_to_front(cfg: config.Config) -> bool:
    """His Chrome to the top of the stack. A page being driven behind your editor is invisible
    work, and when it stops for a captcha you'd never know. Wayland lets a window raise itself
    only from a shell extension, so ask ours first; without it, fall back to activating via the
    running Chrome's own second instance — a newly mapped window is the one case Mutter always
    focuses."""
    import subprocess

    from hearthsmith.desktop import atspi
    b = cfg.browser
    profile = str(b.profile)
    mine = [w for w in atspi.shell_windows()
            if "chrom" in (w.get("wm_class", "") + w.get("app", "")).lower()
            and _owns_profile(w.get("pid"), profile)]
    if mine:
        # the focused one if any; else the most recent (highest id), un-minimize via activate
        target = next((w for w in mine if w.get("focus")), max(mine, key=lambda w: w["id"]))
        if target.get("focus") and not target.get("minimized"):
            return True
        return atspi.activate_window(target["id"])
    # No extension: a second `chrome --user-data-dir=<same profile>` hands its argv to the
    # running instance and exits; the instance opens/raises a window and Mutter focuses it.
    # No url → it just raises the last active window rather than opening a blank one.
    try:
        subprocess.run([b.binary, f"--user-data-dir={profile}"], timeout=5, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _owns_profile(pid: int | None, profile: str) -> bool:
    """Chrome renderers share the profile flag, so any pid of his instance matches."""
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f"--user-data-dir={profile}".encode() in f.read()
    except OSError:
        return False


def _run_once(goal: str, url: str | None = None, cfg: config.Config | None = None,
              max_seconds: float = 90.0) -> Result:
    """Drive Chrome toward `goal`. `url` is the page to start from; without one the goal's own
    address (or a search for it) is used, the same resolution the desktop agent does."""
    cfg = cfg or config.load()
    if not available():
        return Result(False, note="jev-ultrafast not installed")
    from hearthsmith.desktop.agent import _navigate_url

    start = url or _navigate_url(goal, cfg, in_browser=True)
    if not start:
        return Result(False, note="no address in the goal")

    ws = ensure_chrome(cfg)
    if not ws:
        return Result(False, note=f"his Chrome isn't reachable on :{cfg.browser.cdp_port}")
    os.environ["BU_CDP_WS"] = ws
    bring_to_front(cfg)
    # The address is already handled by opening it; leaving "on account.proton.me/signup" in the
    # goal just gives the decider a phrase to match against navigation controls.
    goal = re.sub(r"^\s*(?:on|at|in)\s+\S*(?:\.\w{2,}|/)\S*\s*,?\s*", "", goal).strip() or goal
    _patch(cfg)
    # the text helper writes field values; point it at the same model the smith speaks with
    os.environ.setdefault("TEXT_MODEL_BASE_URL", cfg.compose.fallback_base_url)
    os.environ.setdefault("TEXT_MODEL", cfg.compose.fallback_model)
    os.environ.setdefault("TEXT_MODEL_REASONING", "none")
    if key := os.environ.get(cfg.decide.adapter_key_env):
        os.environ.setdefault("TEXT_MODEL_API_KEY", key)
    os.environ.setdefault("TYPESAFE_API_KEY", "via-openrouter")   # unused, upstream reads it

    from jev_ultrafast import Agent

    from hearthsmith.progress import Narrator
    say = Narrator(cfg)
    say.step(f"opening {start.split('//')[-1][:60]}", "forge")

    res = Result(False)
    # The Agent closes its tab on context exit, which runs before any of our own cleanup — so
    # the decision to keep it has to be made the moment success is seen, not afterwards.
    _keep_tab[0] = False
    try:
        with Agent(start, goal) as agent:
            for state in agent.run():
                res.elapsed_ms = state.get("elapsed_ms", res.elapsed_ms)
                # `decision` is cleared once acted on, so the trace lives in `history`:
                # one entry per executed action, with the decision that produced it.
                for h in state.get("history", [])[len(res.steps):]:
                    line = f"{h.get('kind', '?')} {h.get('action', '')}".strip()
                    if h.get("text"):
                        from hearthsmith.ask import sensitive
                        line += (" = (from you)" if sensitive(str(h.get("action", "")))
                                 else f" = {h['text']!r}")
                    p = h.get("probability")
                    lat = h.get("latency_ms")
                    if p is not None and lat is not None:
                        line += f"  [p={p:.2f}, {lat:.0f}ms]"
                    res.steps.append(line[:140])
                    say.step(line)
                res.decide_ms = [h["latency_ms"] for h in state.get("history", [])
                                 if h.get("latency_ms") is not None]
                page = state.get("page") or {}
                if txt := page.get("text"):
                    res.page_text = txt[:12000]
                res.url = page.get("url") or state.get("url") or res.url
                res.title = page.get("title") or state.get("title") or res.title
                page_txt = (page.get("text") or "").lower()[:4000]
                if any(g in page_txt for g in GATES) and len(page_txt) < 2500:
                    res.note = "your turn — it needs a human (captcha / verification)"
                    _keep_tab[0] = True
                    say.failed("Your turn: it needs a human check. Say 'continue' when done.")
                    _keep_tab[0] = True      # leave it exactly where you have to take over
                    break
                status = state.get("status", "")
                if status in ("done", "blocked", "error"):
                    res.ok = status == "done"
                    if status == "blocked":
                        # "Blocked" means it wants a human. Forms inside a frame the snapshot
                        # cannot cross (Proton's signup, most payment widgets) land here too.
                        # Either way the useful thing is the page, left where it got stuck.
                        res.note = "it's open where I got stuck — take it from here"
                        say.failed("Got as far as I can. It's open for you.")
                    # Keep the tab unless nothing happened. "Blocked" is the case where you most
                    # need it open — it means the agent wants a human, and closing the page is
                    # the one thing that makes taking over impossible.
                    _keep_tab[0] = res.ok or status == "blocked" or bool(res.steps)
                    if res.ok:
                        res.note = ""
                    elif not res.note:
                        res.note = status
                    (say.done(f"Done — {res.title or 'have a look'}.") if res.ok
                     else say.failed(f"Couldn't finish: {status}."))
                    break
                if res.elapsed_ms > max_seconds * 1000:
                    res.note = "time limit"
                    break
    except Exception as e:  # noqa: BLE001 — a browser failure is a result, not a crash
        res.note = f"{type(e).__name__}: {e}"[:200]
        say.failed(res.note)
    return res
