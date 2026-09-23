"""Hand a task to an agent: the ledger's "Hand to agent", `hearthsmith hand <id>`, MCP
hearthsmith_tasks_hand, or "give the changelog to an agent".

The agent goes where the work lives — a Claude pane already on this work in the task's project
(route._where decides: continue it, or start a fresh one), else a new pane in the project's
folder. It gets the task as a brief: title, notes, open steps, and an ask to finish with a short
report. The task goes to "delegated" (the ledger's Agents tab) and the pane is watched; when the
agent has gone quiet, the heartbeat brings its report back into the task's notes, reopens it
for you to tick off, and tells you. He doesn't mark it done himself — the agent saying it's done
and it being done are different things.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from hearthsmith import config
from hearthsmith.adapters.hyperpanes import Hyperpanes
from hearthsmith.store import Store, Task

REPORT = ("When you're finished, or stuck on something only I can decide, end with a short "
          "report: what you did, what's left, anything I need to check. Two to five sentences.")


@dataclass
class Handed:
    ok: bool
    text: str                       # what he says about it
    pane_id: str | None = None
    steps: list[str] = field(default_factory=list)


def brief_for(store: Store, t: Task) -> str:
    out = [f"Task from my hearthsmith ledger: {t.title}"]
    if t.due:
        from hearthsmith.when import describe
        out.append(f"Due {describe(t.due)}.")
    if notes := t.notes.strip():
        out.append(f"Notes:\n{notes[:1500]}")
    if steps := store.children(t.id, "open"):
        out.append("Steps:\n" + "\n".join(f"- {s.title}" for s in steps))
    out.append(REPORT)
    return "\n\n".join(out)


def workdir_for(t: Task, snap) -> str:
    """The task's project folder when hyperpanes knows it, else what the title names, else ~."""
    from hearthsmith.route import _workdir
    for p in (snap.projects if snap else []):
        if t.project and t.project in (p.get("id"), p.get("name")):
            return p["path"]
    return _workdir(t.title, snap)


def start_agent(hp: Hyperpanes, cwd: str, label: str, brief: str,
                meta: dict | None = None) -> tuple[str | None, bool, list[str]]:
    """Fresh Claude pane in `cwd`, folder trusted, brief typed. (pane id, briefed, steps)."""
    # absolute path: the GUI app's PATH is the session's, not the shell's, so a bare "claude"
    # silently falls back to a plain shell pane
    exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    pane_id = hp.new_pane(command=exe, cwd=cwd, label=label, meta=meta)
    if not pane_id:
        return None, False, ["couldn't open a pane"]
    steps = [f"opened a pane in {cwd}"]
    hp.answer_trust(pane_id)             # nothing is accepted until the folder is trusted
    if not hp.wait_ready(pane_id):
        return pane_id, False, [*steps, "pane still starting; brief not sent"]
    if not hp.type_into(pane_id, brief, user_originated=True):
        return pane_id, False, [*steps, "couldn't type into it"]
    return pane_id, True, [*steps, "answered the folder-trust prompt", "typed the brief"]


def hand(cfg: config.Config, store: Store, t: Task, hp: Hyperpanes | None = None) -> Handed:
    from hearthsmith.route import _label, _where
    hp = hp or Hyperpanes(cfg.hyperpanes.control_file, cfg.hyperpanes.tail_lines,
                          cfg.hyperpanes.allow_pane_input)
    started = int(time.time())
    if t.state == "delegated" and any(w["task_id"] == t.id for w in store.watches()):
        return Handed(False, f"'{t.title}' is already with an agent.")
    snap = hp.snapshot(with_screens=False)
    if snap is None:
        return Handed(False, "hyperpanes isn't running — no agent to hand it to.")
    brief = brief_for(store, t)
    cwd = workdir_for(t, snap)
    placed, pane_id = _where(cfg, t.title, cwd, snap, hp)
    steps: list[str] = []
    ok = False
    if placed == "pane" and pane_id:
        pane = next((p for p in snap.panes if p.id == pane_id), None)
        ok = hp.type_into(pane_id, brief, user_originated=True)
        steps = [f"handed to '{pane.label if pane else pane_id}' (already on this work)"
                 + ("" if ok else " — couldn't type into it")]
        where = f"'{pane.label}'" if pane else "the pane already on it"
    else:
        pane_id, ok, steps = start_agent(hp, cwd, _label(t.title), brief, meta={"task": t.id})
        where = f"a new pane in {Path(cwd).name or cwd}"
    store.record_run(f"hand to agent: {t.title}", "handoff", ok, steps, task_id=t.id,
                     target=pane_id, started_at=started)
    if not ok:
        return Handed(False, f"Couldn't hand '{t.title}' over: {steps[-1] if steps else 'no pane'}.",
                      pane_id, steps)
    store.set_state(t.id, "delegated")
    store.watch(pane_id, t.title, t.id, kind="task")
    return Handed(True, f"'{t.title}' is with {where}. I'll bring back its report.", pane_id, steps)


CLAUDE_PROJECTS = Path.home() / ".claude/projects"


def transcript_answer(cwd: str | None, prompt: str, since: int,
                      root: Path = CLAUDE_PROJECTS) -> str:
    """The agent's last reply, from Claude Code's own session transcript — clean text, where the
    screen is a repainting TUI with the brief echoed back and spaces lost to overdraw. Only a
    session in `cwd` written since `since` whose first prompt contains `prompt` counts, so your
    own Claude in the same folder is never mistaken for his agent. "" when there's none."""
    if not cwd:
        return ""
    folder = root / re.sub(r"[^A-Za-z0-9]", "-", cwd.rstrip("/"))
    needle = " ".join(prompt.split())[:120]
    try:
        files = sorted((f for f in folder.glob("*.jsonl") if f.stat().st_mtime >= since),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return ""
    for f in files[:6]:
        first, last = None, ""
        try:
            for line in f.open():
                d = json.loads(line)
                m = d.get("message") or {}
                content = m.get("content")
                if d.get("type") == "user" and first is None and m.get("role") == "user":
                    first = content if isinstance(content, str) else " ".join(
                        c.get("text", "") for c in content or [] if isinstance(c, dict))
                elif d.get("type") == "assistant" and isinstance(content, list):
                    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
                    last = text.strip() or last
        except (OSError, ValueError):
            continue
        if first and needle in " ".join(first.split()):
            return last
    return ""


def take_back(store: Store, t: Task) -> None:
    """Stop waiting on the agent: the task is yours again, the pane is left as it is."""
    for w in store.watches():
        if w["task_id"] == t.id:
            store.unwatch(w["pane_id"])
    store.set_state(t.id, "open")
