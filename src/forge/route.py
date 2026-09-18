"""Inbound: the user says something to the blacksmith. Jev classifies it, forge acts.

    add        remember it as a task (Jev also buckets a due date + picks a project)
    pane       hand it to the agent already working in a hyperpanes pane (Jev picks the pane)
    delegate   enqueue for a worker pane (hyperpanes worker --queue forge)
    done       an existing task is finished (Jev picks which)
    snooze     stop nagging about an existing task for a while
    nag        "what should I be doing?" → run a heartbeat now
    ask        a question — Ornith/OpenRouter answers in character

One Jev call for intent + target + due; one optional compose call for the reply line.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from typesafe_sdk import Choice, Noul

from forge import config
from forge.adapters import markdown
from forge.adapters.hyperpanes import Hyperpanes, Snapshot
from forge.compose import _ollama, _openrouter, _trim
from forge.decide import _wire
from forge.store import Store

INTENTS = {
    "add": "a new task or reminder to remember for later (not to be done right now by an agent)",
    "browse": "something to do in a web browser: open a site, search, click through, read a page",
    "desktop": "something to do in another app on screen: files, settings, a window, a menu, a dialog",
    "spawn": "start a NEW pane/agent for this work — the user asked to open one, or the work "
             "deserves its own agent rather than interrupting one that is already busy",
    "pane": "work that should go to an AI agent ALREADY running in one of the open terminal panes",
    "delegate": "mechanical work to hand to a fresh background worker agent now",
    "done": "the user is saying an existing task is finished",
    "snooze": "the user wants to be left alone about an existing task for a while",
    "nag": "the user is asking what they should be doing / wants a status check",
    "ask": "a question or remark that just needs an answer, nothing to track",
}
DUE = ["no deadline", "today", "tomorrow", "within this week", "next week or later"]
DUE_SECS = [None, 12 * 3600, 36 * 3600, 5 * 86400, 10 * 86400]


@dataclass
class Reply:
    intent: str
    text: str
    task_id: str | None = None
    target: str | None = None
    raw: dict = field(default_factory=dict)


def _jev(cfg: config.DecideCfg, state: str, questions: dict) -> dict:
    r = httpx.post(f"{cfg.adapter_base_url.rstrip('/').removesuffix('/v1')}/alpha/decisions",
                   timeout=15.0,
                   headers={"Authorization": f"Bearer {os.environ[cfg.adapter_key_env]}"},
                   json={"model": cfg.openrouter_slug, "state": state,
                         "questions": {k: _wire(v) for k, v in questions.items()},
                         "session_id": "forge-route"})
    r.raise_for_status()
    return r.json()["answers"]


def _say(cfg: config.ComposeCfg, situation: str, instruction: str) -> str | None:
    msgs = [{"role": "system", "content": cfg.persona},
            {"role": "user", "content": f"{situation}\n\n{instruction}"}]
    out = _ollama(cfg, msgs) or _openrouter(cfg, msgs)
    return _trim(out) if out else None


# "open a new pane with claude and ask it to research X" — the pane is the plumbing, X is the job.
BRIEF_RE = re.compile(r"\b(?:and\s+)?(?:ask|tell|get)\s+it\s+to\s+(.+)$", re.IGNORECASE | re.DOTALL)
PLUMBING = re.compile(r"^\s*(?:on\s+hyperpanes[,\s]*)?(?:please\s+)?(?:open|start|spawn|make|create)\s+"
                      r"(?:a\s+)?new\s+(?:pane|agent|terminal|window)\s*(?:with\s+\w+)?\s*(?:and\s+)?",
                      re.IGNORECASE)


def _brief(text: str) -> str:
    """Strip the 'open a pane and ask it to' scaffolding; what's left is the actual assignment."""
    if m := BRIEF_RE.search(text):
        return m.group(1).strip(" .")
    return PLUMBING.sub("", text).strip(" .") or text


def _workdir(text: str, snap) -> str:
    """Which folder the new agent starts in — Claude inherits it as its workspace, so it decides
    what the agent can see. Matched against the assignment only: "on hyperpanes open a pane…"
    names the app that hosts the pane, not the folder the work belongs to."""
    low = _brief(text).lower()
    for p in (snap.projects if snap else []):
        if p["name"].lower() in low:
            return p["path"]
    if m := re.search(r"(~?/[\w.-]+(?:/[\w.-]+)+)", text):      # an explicit path wins
        p = Path(m.group(1)).expanduser()
        if p.is_dir():
            return str(p)
    # Deliberately NOT whatever pane happens to be in front: a research errand inherited
    # someone else's repo that way, which is both wrong and a wider grant than intended.
    return str(Path.home())


def _label(brief: str) -> str:
    words = [w for w in re.split(r"\W+", brief) if len(w) > 3][:4]
    return " ".join(words)[:40] or "forge task"


def route(text: str, cfg: config.Config | None = None) -> Reply:
    cfg = cfg or config.load()
    store = Store(cfg.db_path)
    hp = Hyperpanes(cfg.hyperpanes.control_file, cfg.hyperpanes.tail_lines,
                    cfg.hyperpanes.allow_pane_input)
    markdown.sync(cfg.nag.markdown_file, store)
    tasks = store.list("open")
    snap: Snapshot | None = hp.snapshot(with_screens=False)

    state = {"user_said": text,
             "open_tasks": {t.id: t.title for t in tasks[:40]},
             "panes": {p.id: f"{p.label} (cwd {p.cwd}, {p.activity})" for p in (snap.panes if snap else [])},
             "projects": [p["name"] for p in (snap.projects if snap else [])]}
    q = {"intent": Choice(instructions="What does the user want done with what they said?",
                          criteria=INTENTS),
         "due": Choice(instructions="If this is a task, when is it due?",
                       criteria={str(i): d for i, d in enumerate(DUE)}),
         "urgent": Noul(instructions="This needs attention right now, not later.")}
    if tasks:
        q["task"] = Choice(instructions="If the user refers to an existing task, which one?",
                           criteria={t.id: t.title for t in tasks[:40]})
    if snap and snap.panes:
        q["pane"] = Choice(instructions="If this should go to a running agent, which pane fits best?",
                           criteria={p.id: f"{p.label} — {p.cwd}" for p in snap.panes})
    if snap and snap.projects:
        q["project"] = Choice(instructions="Which project does this belong to?",
                              criteria={p["name"]: p["path"] for p in snap.projects})

    try:
        a = _jev(cfg.decide, json.dumps(state), q)
        intent = a["intent"]["choice"]
        conf = a["intent"].get("probabilities", {}).get(intent, 0)
    except Exception as e:  # noqa: BLE001 — no decider → store it, never lose the input
        t = store.add(text)
        return Reply("add", f"Noted '{text}'. (decider down: {type(e).__name__})", t.id)

    raw = {"answers": a}
    if intent == "browse":
        # Inside a page a DOM snapshot beats the accessibility tree; his Chrome handles the web.
        from forge.browser import jev
        if jev.available():
            j = jev.run(text, cfg=cfg)
            if j.ok:
                return Reply(intent, f"Done — {j.title or j.url or 'in Chrome'}.",
                             raw={**raw, "steps": j.steps, "ms": j.elapsed_ms})
            if j.note and "isn't reachable" not in j.note:
                return Reply(intent, f"Couldn't finish in the browser ({j.note}).",
                             raw={**raw, "steps": j.steps})
        # no Chrome: fall through to the desktop body rather than refusing outright

    if intent in ("browse", "desktop"):
        from forge.desktop.agent import run
        b = run(text, cfg)
        if b.ok:
            return Reply(intent, f"Done — {b.window}." if b.window else "Done.", raw={**raw, "steps": b.steps})
        return Reply(intent, f"Couldn't finish ({b.note}). Was in {b.window or 'nowhere'}.",
                     raw={**raw, "steps": b.steps})
    if intent == "add":
        due_i = int(a["due"]["choice"]); due = int(time.time() + DUE_SECS[due_i]) if DUE_SECS[due_i] else None
        project = a["project"]["choice"] if "project" in a and a["project"].get("confidence", 0) > 0.5 else None
        t = store.add(text, due=due, project=project)
        line = _say(cfg.compose, f"The user just asked you to remember: '{text}' (due: {DUE[due_i]}).",
                    "Confirm in one short line, in character.") or f"Noted: {text} ({DUE[due_i]})."
        return Reply(intent, line, t.id, raw=raw)

    if intent == "spawn":
        brief = _brief(text)
        # absolute path: the GUI app's PATH is the session's, not the shell's, so a bare
        # "claude" silently falls back to a plain shell pane
        exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        cwd = _workdir(text, snap)
        pane_id = hp.new_pane(command=exe, cwd=cwd, label=_label(brief))
        if not pane_id:
            intent = "pane"          # couldn't open one; fall through to an existing pane
        else:
            t = store.add(brief)
            store.set_state(t.id, "delegated")
            hp.answer_trust(pane_id)          # nothing is accepted until the folder is trusted
            ready = hp.wait_ready(pane_id)
            if ready and hp.type_into(pane_id, brief, user_originated=True):
                where = Path(cwd).name or cwd
                return Reply(intent, f"Opened a pane in {where} and set it on it: {_label(brief)}.",
                             t.id, pane_id, raw)
            return Reply(intent, "Opened a pane — it's still starting, so I left the brief for "
                         "you to send." if not ready else "Opened a pane but couldn't type into it.",
                         t.id, pane_id, raw)

    if intent == "pane" and "pane" in a and snap:
        pane_id = a["pane"]["choice"]
        pane = next((p for p in snap.panes if p.id == pane_id), None)
        if pane:
            proj = (snap.project_for_cwd(pane.cwd) or {}).get("name")
            if (pane.activity != "busy" and cfg.hyperpanes.say_types_into_idle_pane
                    and hp.type_into(pane_id, text, user_originated=True)):
                t = store.add(text, project=proj)
                store.set_state(t.id, "delegated")
                return Reply(intent, f"Told '{pane.label}' — it's on it.", t.id, pane_id, raw)
            if hp.message(pane_id, f"[from the forge] {text}"):
                t = store.add(text, project=proj)
                store.set_state(t.id, "delegated")
                return Reply(intent, f"'{pane.label}' is busy — left it in its inbox; it'll see it "
                             "when it checks messages.", t.id, pane_id, raw)
        intent = "delegate"  # fall through

    if intent == "delegate":
        job = (hp.enqueue(cfg.hyperpanes.delegate_queue, text[:80], text, dedupe_key=None)
               if snap and cfg.hyperpanes.delegate_enabled else None)
        t = store.add(text)
        if job:
            store.set_state(t.id, "delegated")
            return Reply(intent, f"Queued for a worker ({cfg.hyperpanes.delegate_queue}).", t.id, raw=raw)
        return Reply("add", "No worker available; kept it on the ledger.", t.id, raw=raw)

    if intent in ("done", "snooze") and "task" in a:
        tid = a["task"]["choice"]
        t = store.get(tid)
        if t:
            if intent == "done":
                store.set_state(tid, "done")
                return Reply(intent, f"'{t.title}' — struck off.", tid, raw=raw)
            store.snooze(tid, cfg.nag.snooze_default_minutes)
            return Reply(intent, f"'{t.title}' — the forge cools for {cfg.nag.snooze_default_minutes} min.", tid, raw=raw)

    if intent == "nag":
        from forge.daemon import heartbeat
        out = heartbeat(cfg, store, hp, dry=True, force=True)
        if "dry_run" in out:
            return Reply(intent, out["dry_run"]["text"], out["dry_run"]["task"], raw=raw)
        return Reply(intent, out.get("text", "Nothing pressing. The bellows are quiet."), raw=raw)

    # ask / fallthrough
    situation = (f"The user said: '{text}'.\nOpen tasks: " +
                 ("; ".join(t.title for t in tasks[:10]) or "none") +
                 (f"\nPanes: {', '.join(p.label for p in snap.panes)}" if snap else ""))
    line = _say(cfg.compose, situation, "Answer briefly, in character.") or "Aye."
    return Reply("ask", line, raw={**raw, "intent_conf": conf})
