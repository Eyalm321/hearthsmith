"""hyperpanes control API client. First-class: sense (what you're doing), speak (nag in a pane),
delegate (enqueue for a worker). Reads control.json for port + token; degrades to None when the
app is closed so the daemon keeps nagging from the store alone.

Contract as of hyperpanes 0.0.28 (rs/crates/core/src/control/routes.rs):
  GET  /health                          no auth
  GET  /state                           windows→tabs→panes{id,label,color,cwd,status,activity}
  GET  /projects                        {projects:[{id,name,path,color,lastOpenedAt}]}
  GET  /panes/{id}/output?mode=screen&tail=N
  POST /panes/{id}/messages             out-of-band message to the pane's agent (not a keystroke)
  POST /panes/{id}/input                {data,submit} — arbitrary command exec; gated by config
  POST /queues/{q}/tasks                {payload,kind?,title?,priority?,dedupeKey?,...}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class Pane:
    id: str
    label: str
    cwd: str | None
    status: str
    activity: str  # busy | idle
    tab: str
    screen: str = ""


@dataclass
class Snapshot:
    panes: list[Pane] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    taken_at: float = field(default_factory=time.time)

    def project_for_cwd(self, cwd: str | None) -> dict | None:
        if not cwd:
            return None
        best = None
        for p in self.projects:
            if cwd.startswith(p["path"]) and (best is None or len(p["path"]) > len(best["path"])):
                best = p
        return best

    def summary(self) -> str:
        if not self.panes:
            return "hyperpanes is not running."
        busy = [p for p in self.panes if p.activity == "busy"]
        idle = [p for p in self.panes if p.activity != "busy"]
        by_proj: dict[str, int] = {}
        for p in self.panes:
            pr = self.project_for_cwd(p.cwd)
            by_proj[pr["name"] if pr else (p.cwd or "?")] = by_proj.get(
                pr["name"] if pr else (p.cwd or "?"), 0) + 1
        parts = [f"{len(self.panes)} panes open ({len(busy)} busy, {len(idle)} idle)",
                 "by project: " + ", ".join(f"{k}={v}" for k, v in by_proj.items())]
        for p in busy[:3]:
            tail = p.screen.strip().splitlines()[-3:] if p.screen else []
            if tail:
                parts.append(f"pane '{p.label}' busy, last lines: " + " | ".join(tail))
        return ". ".join(parts) + "."


class Hyperpanes:
    def __init__(self, control_file: Path, tail_lines: int = 20, allow_pane_input: bool = False):
        self.control_file = control_file
        self.tail_lines = tail_lines
        self.allow_pane_input = allow_pane_input
        self._base = None
        self._token = None

    # -- plumbing --------------------------------------------------------------------------

    def _load(self) -> bool:
        try:
            d = json.loads(self.control_file.read_text())
        except (OSError, ValueError):
            return False
        self._base = f"http://127.0.0.1:{d['port']}"
        self._token = d["token"]
        return True

    def _client(self) -> httpx.Client:
        if self._base is None:
            self._load()          # every entry point used to have to call alive() first
        return httpx.Client(base_url=self._base or "http://127.0.0.1:0", timeout=8.0,
                            headers={"Authorization": f"Bearer {self._token}"})

    def alive(self) -> bool:
        if not self._load():
            return False
        try:
            with self._client() as c:
                return c.get("/health").json().get("ok", False)
        except (httpx.HTTPError, ValueError):
            return False

    # -- sense -----------------------------------------------------------------------------

    def snapshot(self, with_screens: bool = True) -> Snapshot | None:
        if not self.alive():
            return None
        snap = Snapshot()
        with self._client() as c:
            state = c.get("/state").json()
            snap.projects = c.get("/projects").json().get("projects", [])
            for w in state.get("windows", []):
                for t in w.get("tabs", []):
                    for p in t.get("panes", []):
                        pane = Pane(id=p["id"], label=p.get("label", ""), cwd=p.get("cwd"),
                                    status=p.get("status", ""), activity=p.get("activity", ""),
                                    tab=t.get("title", ""))
                        if with_screens and pane.activity == "busy":
                            r = c.get(f"/panes/{pane.id}/output",
                                      params={"mode": "screen", "tail": self.tail_lines})
                            if r.status_code == 200:
                                body = r.json() if r.headers.get("content-type", "").startswith(
                                    "application/json") else {"output": r.text}
                                pane.screen = body.get("output") or body.get("text") or ""
                        snap.panes.append(pane)
        return snap

    def screen(self, pane_id: str, tail: int | None = None) -> str:
        """Rendered screen of one pane, busy or not — `snapshot` only fetches busy panes, which
        made a freshly spawned (idle) agent look like an empty window."""
        with self._client() as c:
            r = c.get(f"/panes/{pane_id}/output",
                      params={"mode": "screen", "tail": tail or self.tail_lines})
            if r.status_code != 200:
                return ""
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") \
                else {"output": r.text}
            return body.get("output") or body.get("text") or ""

    def input_line(self, pane_id: str) -> str:
        """What sits in a Claude pane's input box: "" at an empty prompt, else the text after ❯.
        Cannot tell ghost text (the pane's own suggested next prompt) from a line the user is
        typing — the screen is plain text — so callers judge by how long it stays unchanged."""
        screen = self.screen(pane_id, tail=8)
        lines = [ln for ln in screen.splitlines() if ln.lstrip().startswith("❯")]
        if not lines:
            return ""
        return lines[-1].lstrip()[1:].replace("\xa0", " ").strip()

    def last_answer(self, pane_id: str, max_chars: int = 1600) -> str:
        """The agent's closing answer. A pane is a repainting TUI, not a transcript: the same
        sentence appears half-drawn several times as it streams, and spinner frames land in the
        middle of words. So: keep line structure, drop the chrome, prefer whatever follows the
        last "Answer:" marker, and keep the longest version of each repeated line."""
        import re as _re
        # The rendered screen is the terminal's own reconstruction — accurate. The raw buffer is
        # a stream of partial repaints, where a word can lose characters to an overdraw, so it is
        # only worth reading when the answer has already scrolled out of view.
        rendered = self.screen(pane_id, tail=80)
        raw = rendered
        if not _re.search(r"(?im)^\s*[●⎿│ ]*answer\b\s*[:\-]", rendered):
            with self._client() as c:
                r = c.get(f"/panes/{pane_id}/output", params={"tail": 30000})
                raw = r.json().get("output", "") if r.status_code == 200 else rendered
        clean = _re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)?", "", raw)
        clean = _re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", clean)
        clean = _re.sub(r"(?:Burrowing|Churning|Crunched|Cerebrating|Quantumizing|Pondering|"
                        r"Thinking)[.…]*", " ", clean)
        noise = _re.compile(r"^[\s─│╭╰┌┐└┘├┤┬┴┼·✢✽✻✶*]*$|auto mode|shift\+tab|ctrl\+|"
                            r"esc to interrupt|tokens|^\s*⏵|token usage|for shortcuts",
                            _re.IGNORECASE)
        lines = []
        for ln in clean.splitlines():
            t = ln.rstrip().lstrip("●❯⎿│ ").strip()
            if not t or noise.search(ln) or len(t) < 3:
                continue
            # a streamed line is redrawn as it grows; keep the fullest form, not the fragments
            if lines and (t.startswith(lines[-1][:20]) or lines[-1].startswith(t[:20])):
                lines[-1] = t if len(t) > len(lines[-1]) else lines[-1]
            else:
                lines.append(t)
        marked = [i for i, t in enumerate(lines) if _re.match(r"(?i)^answer\b\s*[:\-]", t)]
        tail = lines[marked[-1]:] if marked else lines[-14:]
        return "\n".join(tail)[:max_chars].strip()

    # -- speak -----------------------------------------------------------------------------

    def message(self, pane_id: str, text: str) -> bool:
        """Out-of-band message to the pane's agent. Not a keystroke."""
        with self._client() as c:
            r = c.post(f"/panes/{pane_id}/messages", json={"from": "hearthsmith", "body": text})
            return r.status_code < 300

    def type_into(self, pane_id: str, text: str, user_originated: bool = False) -> bool:
        """Keystrokes into the pane. Off for the daemon's own nags; allowed for text the user
        typed themselves (hearthsmith say), which is them talking to that agent through the smith."""
        if not (self.allow_pane_input or user_originated):
            raise PermissionError("pane input disabled (hyperpanes.allow_pane_input)")
        with self._client() as c:
            r = c.post(f"/panes/{pane_id}/input", json={"data": text, "submit": True})
            return r.status_code < 300

    # -- spawn -----------------------------------------------------------------------------

    def new_pane(self, command: str | None = None, args: list[str] | None = None,
                 cwd: str | None = None, label: str | None = None,
                 window_id: int = 0) -> str | None:
        """Open a pane and return its id. `command` is what runs in it (e.g. "claude"); the spec
        fields must be nested under "pane" or the API rejects the call."""
        spec: dict = {}
        for k, v in (("command", command), ("args", args), ("cwd", cwd), ("label", label)):
            if v:
                spec[k] = v
        with self._client() as c:
            r = c.post("/command", json={"type": "newPane", "windowId": window_id, "pane": spec})
            if r.status_code >= 300:
                return None
            body = r.json()
            # the API answers {"ok": true, "result": "<uuid>"} and /state reports that id
            # verbatim — older panes carry a "pane-" prefix, freshly created ones do not
            res = body.get("result")
            if isinstance(res, dict):
                res = res.get("paneId") or res.get("id")
            return res if isinstance(res, str) else None

    def close_pane(self, pane_id: str) -> bool:
        with self._client() as c:
            r = c.post("/command", json={"type": "closePane", "paneId": pane_id})
            return r.status_code < 300

    TRUST = ("i trust this folder", "trust the files in this folder", "trust this workspace",
             "do you trust the files", "no, exit")
    # Claude's own input hint — NOT hyperpanes' status bar, which draws "⏵⏵ auto mode" in every
    # pane including one sitting on a modal dialog.
    PROMPT = ("? for shortcuts", "│ >", "esc to interrupt")

    def answer_trust(self, pane_id: str, timeout: float = 25.0) -> bool:
        """Claude asks whether it trusts the folder before it will take any input. Anything typed
        at that dialog is swallowed (and can end the session), so it has to be answered first.
        True = a prompt was seen and accepted."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            screen = self.screen(pane_id, tail=40).lower()
            if any(t in screen for t in self.TRUST):
                # the dialog opens on "No, exit" — confirming blind would end the session
                with self._client() as c:
                    c.post(f"/panes/{pane_id}/input", json={"keys": ["down"]})
                    time.sleep(0.3)
                    c.post(f"/panes/{pane_id}/input", json={"keys": ["enter"]})
                time.sleep(2.0)
                return True
            if any(p in screen for p in self.PROMPT):
                return False          # already at the input box, nothing to trust
            time.sleep(1.0)
        return False

    def wait_ready(self, pane_id: str, needle: str = "", timeout: float = 60.0) -> bool:
        """Wait for a freshly spawned agent to reach its prompt: idle, with something on screen.
        Typing into it before that goes into the void."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.0)
            snap = self.snapshot(with_screens=True)
            if not snap:
                return False
            pane = next((p for p in snap.panes if p.id == pane_id), None)
            if pane is None:
                continue
            screen = self.screen(pane_id)
            # an agent CLI paints its prompt and then sits there; "idle with something drawn" is
            # the general signal, and these markers are what Claude's prompt looks like
            low = screen.lower()
            if any(t in low for t in self.TRUST):
                return False          # caller must answer the trust dialog first
            ready = bool(screen.strip()) and (pane.activity != "busy"
                                              or any(m in screen for m in self.PROMPT))
            if ready and (not needle or needle.lower() in screen.lower()):
                return True
        return False

    # -- delegate --------------------------------------------------------------------------

    def enqueue(self, queue: str, title: str, prompt: str, priority: int = 5,
                dedupe_key: str | None = None) -> dict | None:
        body = {"payload": json.dumps({"prompt": prompt}), "kind": "hearthsmith", "title": title,
                "priority": priority}
        if dedupe_key:
            body["dedupeKey"] = dedupe_key
        with self._client() as c:
            r = c.post(f"/queues/{queue}/tasks", json=body)
            return r.json() if r.status_code < 300 else None

    def queue_counts(self, queue: str) -> dict | None:
        with self._client() as c:
            for q in c.get("/queues").json().get("queues", []):
                if q["queue"] == queue:
                    return q["counts"]
        return None
