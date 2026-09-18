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
        return httpx.Client(base_url=self._base, timeout=5.0,
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

    # -- speak -----------------------------------------------------------------------------

    def message(self, pane_id: str, text: str) -> bool:
        """Out-of-band message to the pane's agent. Not a keystroke."""
        with self._client() as c:
            r = c.post(f"/panes/{pane_id}/messages", json={"from": "forge", "body": text})
            return r.status_code < 300

    def type_into(self, pane_id: str, text: str) -> bool:
        if not self.allow_pane_input:
            raise PermissionError("pane input disabled (hyperpanes.allow_pane_input)")
        with self._client() as c:
            r = c.post(f"/panes/{pane_id}/input", json={"data": text, "submit": True})
            return r.status_code < 300

    # -- delegate --------------------------------------------------------------------------

    def enqueue(self, queue: str, title: str, prompt: str, priority: int = 5,
                dedupe_key: str | None = None) -> dict | None:
        body = {"payload": json.dumps({"prompt": prompt}), "kind": "forge", "title": title,
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
