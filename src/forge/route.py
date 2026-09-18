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
import time
from dataclasses import dataclass, field

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
    "pane": "work that should go to an AI agent already running in one of the open terminal panes",
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

    if intent == "pane" and "pane" in a and snap:
        pane_id = a["pane"]["choice"]
        pane = next((p for p in snap.panes if p.id == pane_id), None)
        if pane:
            proj = (snap.project_for_cwd(pane.cwd) or {}).get("name")
            if pane.activity != "busy" and cfg.hyperpanes.say_types_into_idle_pane:
                if hp.type_into(pane_id, text, user_originated=True):
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
