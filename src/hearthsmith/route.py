"""Inbound: the user says something to the blacksmith. Jev classifies it, hearthsmith acts.

    add        remember it as a task (Jev also buckets a due date + picks a project)
    pane       hand it to the agent already working in a hyperpanes pane (Jev picks the pane)
    delegate   enqueue for a worker pane (hyperpanes worker --queue hearthsmith)
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
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from typesafe_sdk import Choice, Noul

from hearthsmith import answer as answer_mod
from hearthsmith import config, when
from hearthsmith.adapters import markdown
from hearthsmith.adapters.hyperpanes import Hyperpanes, Snapshot
from hearthsmith.compose import _ollama, _openrouter, _trim
from hearthsmith.decide import _wire
from hearthsmith.store import Store

INTENTS = {
    "add": "a new task or reminder to remember for later (not to be done right now by an agent)",
    "research": "a question that needs looking things up and comparing sources before it can be "
                "answered honestly — an average, a going rate, a comparison, the state of a field",
    "browse": "something to do in a web browser: open a site, search, click through, read a page",
    "desktop": "something to do in another app on screen: files, settings, a window, a menu, a dialog",
    "spawn": "start a NEW pane/agent for this work — the user asked to open one, or the work "
             "deserves its own agent rather than interrupting one that is already busy",
    "pane": "work that should go to an AI agent ALREADY running in one of the open terminal panes",
    "delegate": "mechanical work to hand to a fresh background worker agent now",
    "done": "the user is saying an existing task is finished",
    "snooze": "the user wants to be left alone about an existing task for a while",
    "nag": "the user is asking what they should be doing / wants a status check",
    "ask": "a remark, or a question answerable from what you already know — no looking anything up",
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
                         "session_id": "hearthsmith-route"})
    r.raise_for_status()
    return r.json()["answers"]


def _say(cfg: config.ComposeCfg, situation: str, instruction: str) -> str | None:
    msgs = [{"role": "system", "content": cfg.persona},
            {"role": "user", "content": f"{situation}\n\n{instruction}"}]
    out = _ollama(cfg, msgs) or _openrouter(cfg, msgs)
    return _trim(out) if out else None


# "open a new pane with claude and ask it to research X" — the pane is the plumbing, X is the job.
# "can you open a terminal" is all plumbing: nothing to assign, so nothing gets typed.
BRIEF_RE = re.compile(r"\b(?:and\s+)?(?:ask|tell|get)\s+it\s+to\s+(.+)$", re.IGNORECASE | re.DOTALL)
PLUMBING = re.compile(r"^\s*(?:on\s+hyperpanes[,\s]*)?"
                      r"(?:(?:i'?d|i\s+would)\s+like\s+(?:you\s+)?to\s+|i\s+(?:want|need)\s+you\s+to\s+"
                      r"|(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
                      r"(?:open|start|spawn|make|create|launch)\s+(?:me\s+)?(?:up\s+)?"
                      r"(?:a\s+|an\s+|another\s+)?"
                      r"(?:(?:new|fresh|blank|empty|native|real|regular|normal|plain|linux|system|"
                      r"separate|standalone)\s+)*"
                      r"(?:pane|agent|terminal|window|shell|claude|ptyxis|gnome-terminal)\s*(?:window\s*)?"
                      r"(?:(?:with|running)\s+\w+)?\s*(?:for\s+me)?\s*(?:please)?\s*(?:and\s+)?",
                      re.IGNORECASE)
LOCATION = re.compile(r"(?:in|inside|at|under|for)\s+(?:the\s+)?[\w.~/-]+(?:\s+(?:project|repo|folder|dir))?",
                      re.IGNORECASE)
# "…in linux native terminal", "…outside hyperpanes": says which kind of terminal, not what to do
WHERE_WORDS = {"in", "on", "inside", "outside", "of", "using", "with", "via", "as", "the", "a", "an",
               "my", "not", "instead", "please", "linux", "native", "real", "regular", "normal",
               "actual", "system", "proper", "standalone", "separate", "plain", "terminal",
               "shell", "window", "app", "emulator", "ptyxis", "gnome", "gnome-terminal",
               "hyperpanes", "pane", "os"}
NATIVE = re.compile(r"\b(?:native|ptyxis|gnome[- ]terminal|(?:linux|system|os)\s+terminal"
                    r"|(?:not|outside(?:\s+of)?|without|instead\s+of)\s+(?:in\s+)?hyperpanes"
                    r"|(?:real|regular|normal|actual|proper|standalone|separate)\s+(?:linux\s+)?"
                    r"(?:terminal|shell|window))\b", re.IGNORECASE)
# "brief me", "what's my day look like", "how did today go" — the ledger read back, not a nag
BRIEF_ASK = re.compile(r"\b(?:brief\s+me|(?:morning|daily|evening)\s+(?:brief|briefing|wrap|recap|summary)"
                       r"|wrap\s+(?:up\s+)?(?:the|my)\s+day|recap\s+(?:the|my)\s+day"
                       r"|what(?:'?s|\s+is|\s+does)\s+(?:on\s+)?my\s+(?:day|plate|agenda)"
                       r"|how\s+(?:did|was)\s+(?:my|the)\s+day|what\s+did\s+i\s+(?:get\s+)?done\s+today)",
                       re.IGNORECASE)
WANTS_AGENT = re.compile(r"\b(?:claude|agent)\b", re.IGNORECASE)
TERMINALS = ("ptyxis", "gnome-terminal", "kgx", "foot", "konsole", "alacritty", "kitty", "xterm")


def _brief(text: str) -> str:
    """Strip the 'open a pane and ask it to' scaffolding; what's left is the actual assignment
    ("" when the request was only to open something)."""
    if m := BRIEF_RE.search(text):
        return m.group(1).strip(" .")
    rest = PLUMBING.sub("", text).strip(" ,.?!")
    # "open a terminal in forge" / "…in linux native terminal" — the tail says where, not what
    if LOCATION.fullmatch(rest) or set(re.findall(r"[\w-]+", rest.lower())) <= WHERE_WORDS:
        return ""
    return rest


def _native_terminal(text: str) -> bool:
    """He asked for a terminal outside hyperpanes — a real window on the desktop — and nothing
    else. Anything with an assignment still goes to a pane, where an agent can be watched."""
    return not _brief(text) and bool(NATIVE.search(text)) and bool(
        re.search(r"\b(?:terminal|shell|ptyxis|gnome-terminal)\b", text, re.IGNORECASE))


def _open_native_terminal(cwd: str) -> str | None:
    """A terminal window on the desktop. Returns which one, or None when there isn't any."""
    exe = next((t for t in TERMINALS if shutil.which(t)), None)
    if not exe:
        return None
    args = [exe, "--new-window"] if exe == "ptyxis" else [exe]
    # Scoped to the graphical session like desktop launches (see desktop/agent.py): a bare
    # Popen from the pet's own cgroup is what the OOM killer culls first.
    subprocess.Popen(["systemd-run", "--user", "--quiet", "--collect", "--scope",
                      "--slice=app-graphical.slice", f"--working-directory={cwd}", *args],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return exe


def _wants_agent(text: str) -> bool:
    """A pane with claude in it, or a plain shell? With an assignment it's always claude — someone
    has to do the work. Without one, only if he named the agent: "open a terminal" is a terminal."""
    return bool(_brief(text)) or bool(WANTS_AGENT.search(text))


def _workdir(text: str, snap) -> str:
    """Which folder the new agent starts in — Claude inherits it as its workspace, so it decides
    what the agent can see. Matched against the assignment only: "on hyperpanes open a pane…"
    names the app that hosts the pane, not the folder the work belongs to."""
    low = PLUMBING.sub("", text).lower()      # keeps "…in forge", drops "on hyperpanes open…"
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


def _where(cfg: config.Config, brief: str, workdir: str, snap, hp) -> tuple[str, str | None]:
    """Continue an agent's work, or start a fresh one?

    Only panes already in this project are candidates — handing work to an agent sitting in an
    unrelated repo is how a research errand ended up in someone else's checkout. Each candidate
    is described by what it is actually doing (its last few lines), because "is this part of what
    you are already doing?" cannot be answered from a pane label.
    """
    if snap is None:
        return "spawn", None
    here = [p for p in snap.panes if p.cwd and p.cwd.startswith(workdir)]
    if not here:
        return "spawn", None
    doing = {}
    for p in here[:6]:
        tail = [ln.strip() for ln in hp.screen(p.id, tail=12).splitlines() if ln.strip()][-4:]
        doing[p.id] = f"{p.label} ({p.activity}) — " + (" / ".join(tail)[:220] or "nothing on screen")
    try:
        a = _jev(cfg.decide, json.dumps({"assignment": brief, "project": workdir,
                                         "open_agents_in_this_project": doing}),
                 {"continues": Noul(instructions="This assignment continues work one of these "
                                    "agents is already doing, rather than being a separate "
                                    "concern that deserves its own agent."),
                  "agent": Choice(instructions="Which agent is already on this work?",
                                  criteria=doing)})
    except Exception:  # noqa: BLE001 — no decider: a fresh agent is the safe default
        return "spawn", None
    if a["continues"]["noul"] > 0.6:
        chosen = a["agent"]["choice"]
        pane = next((p for p in here if p.id == chosen), None)
        # never cut across an agent mid-task unless this really is the same thread of work
        if pane and (pane.activity != "busy" or a["continues"]["noul"] > 0.85):
            return "pane", chosen
    return "spawn", None


def _label(brief: str) -> str:
    words = [w for w in re.split(r"\W+", brief) if len(w) > 3][:4]
    return " ".join(words)[:40] or "hearthsmith task"


def route(text: str, cfg: config.Config | None = None) -> Reply:
    cfg = cfg or config.load()
    store = Store(cfg.db_path)
    hp = Hyperpanes(cfg.hyperpanes.control_file, cfg.hyperpanes.tail_lines,
                    cfg.hyperpanes.allow_pane_input)
    markdown.sync(cfg.nag.markdown_file, store)
    tasks = store.list("open")
    snap: Snapshot | None = hp.snapshot(with_screens=False)

    # "open a terminal in linux native terminal": a desktop window, not a pane — and no
    # decider needed, there is nothing to classify.
    if _native_terminal(text):
        cwd = _workdir(text, snap)
        exe = _open_native_terminal(cwd)
        store.record_run(text, "desktop", bool(exe),
                         [f"launched {exe} in {cwd}"] if exe else [], note="" if exe else "no terminal app found",
                         target=exe)
        if exe:
            return Reply("desktop", f"Opened {exe} in {Path(cwd).name or cwd}.")
        return Reply("desktop", "No terminal app on this machine that I know of.")

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
        title, due = when.parse(text)
        t = store.add(title, due=due)
        return Reply("add", f"Noted '{title}'" + (f", {when.describe(due)}" if due else "")
                     + f". (decider down: {type(e).__name__})", t.id)

    raw = {"answers": a}
    if BRIEF_ASK.search(text) and not when.REMINDER.match(text):
        from hearthsmith import brief
        started = time.time()
        b = brief.make(cfg, store)
        if b["quiet"]:
            return Reply("brief", "Quiet day. Nothing done, nothing due, nothing waiting.", raw=raw)
        brief.record(store, b, ["reply"], started)
        return Reply("brief", b["text"], raw={**raw, "facts": b["facts"]})
    if when.REMINDER.match(text):
        intent = "add"            # "remind me to research X" is for later, not for now
    # A question about the world is not a question for him: if it wants a fact that lives on a
    # page, go and read the page instead of answering from memory. This overrides the intent
    # whenever the alternative is him talking (or a task match that never happened) — those
    # branches fall through to the same shrug.
    researching = intent in ("research", "ask") and answer_mod.wants_research(text)
    if not researching and intent in ("research", "ask", "nag", "done", "snooze") and (
            answer_mod.needs_lookup(text) or intent == "research"):
        # One page holds it, so read the page: "research" from the decider covers everything
        # from a price to a literature review, and only the latter is worth an agent.
        intent = "browse"
    if researching:
        intent = "spawn"          # sent to an agent below, and watched so the answer comes back

    if intent == "browse":
        # Inside a page a DOM snapshot beats the accessibility tree; his Chrome handles the web.
        from hearthsmith.browser import jev
        if jev.available():
            started = int(time.time())
            # For a question the job is to *reach* the page that answers it; left as an open
            # goal the agent keeps clicking and eventually types the question into a search box.
            looking_up = answer_mod.is_question(text)
            goal = (f"Open the page that actually answers this and stop there. A list of search "
                    f"results is not an answer — click through to the real page first. "
                    f"Question: {text}" if looking_up else text)
            j = jev.run(goal, cfg=cfg)
            answered = (answer_mod.from_page(cfg, text, j.page_text, j.url)
                        if looking_up else None)
            store.record_run(text, "browser", bool(j.ok or answered),
                             j.steps + ([f"answered: {answered[:160]}"] if answered else []),
                             note="" if answered else j.note,
                             target=j.url or j.title, decide_ms=j.decide_ms, seen=j.seen,
                             started_at=started)
            # Answer from whatever page it reached — a run that ended untidily on the right page
            # still holds the answer, and reporting the stumble instead would be perverse.
            if answered:
                return Reply(intent, answered, raw={**raw, "steps": j.steps, "ms": j.elapsed_ms,
                                                    "source": j.url})
            if j.ok:
                return Reply(intent, f"Done — {j.title or j.url or 'in Chrome'}.",
                             raw={**raw, "steps": j.steps, "ms": j.elapsed_ms})
            if j.note and "isn't reachable" not in j.note:
                return Reply(intent, f"Couldn't finish in the browser ({j.note}).",
                             raw={**raw, "steps": j.steps})
        # no Chrome: fall through to the desktop body rather than refusing outright

    if intent in ("browse", "desktop"):
        from hearthsmith.desktop.agent import run
        started = int(time.time())
        b = run(text, cfg)
        store.record_run(text, "desktop", b.ok, b.steps, note=b.note, target=b.window,
                         seen=b.seen, started_at=started)
        if b.ok:
            return Reply(intent, f"Done — {b.window}." if b.window else "Done.", raw={**raw, "steps": b.steps})
        return Reply(intent, f"Couldn't finish ({b.note}). Was in {b.window or 'nowhere'}.",
                     raw={**raw, "steps": b.steps})
    if intent == "add":
        # A date he actually said beats the decider's bucket; the bucket is for "soonish".
        title, due = when.parse(text)
        if due is None:
            due_i = int(a["due"]["choice"])
            due = int(time.time() + DUE_SECS[due_i]) if DUE_SECS[due_i] else None
        project = a["project"]["choice"] if "project" in a and a["project"].get("confidence", 0) > 0.5 else None
        t = store.add(title, due=due, project=project)
        said = when.describe(due)
        line = _say(cfg.compose, f"The user just asked you to remember: '{title}' (due: {said}).",
                    "Confirm in one short line, in character, and say exactly when it's due.") \
            or f"Noted: {title} ({said})."
        return Reply(intent, line, t.id, raw={**raw, "due": due})

    if intent in ("spawn", "pane") and not _brief(text):
        intent = "spawn"          # nothing to hand to anyone — he only asked for a pane
    elif intent in ("spawn", "pane") and snap:
        # The intent says "an agent should do this"; where it lands is a separate question.
        brief_for_place = _brief(text)
        placed, pane_id = _where(cfg, brief_for_place, _workdir(text, snap), snap, hp)
        if placed == "pane" and pane_id:
            pane = next((p for p in snap.panes if p.id == pane_id), None)
            proj = (snap.project_for_cwd(pane.cwd) or {}).get("name") if pane else None
            if pane and hp.type_into(pane_id, brief_for_place, user_originated=True):
                t = store.add(brief_for_place, project=proj)
                store.set_state(t.id, "delegated")
                store.record_run(brief_for_place, "pane", True,
                                 [f"handed to '{pane.label}' (already on this work)"],
                                 task_id=t.id, target=pane_id)
                return Reply("pane", f"'{pane.label}' is already on that — handed it over.",
                             t.id, pane_id, raw)
        intent = "spawn"

    if intent == "spawn":
        brief = _brief(text)
        if researching:
            brief = (f"{text}\n\nResearch this properly, then finish with a short, direct "
                     "answer in two or three sentences — the number or conclusion first.")
        # absolute path: the GUI app's PATH is the session's, not the shell's, so a bare
        # "claude" silently falls back to a plain shell pane
        exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        cwd = _workdir(text, snap)
        where = Path(cwd).name or cwd
        if not brief:
            # "open a terminal": just the pane — a shell, or claude if he said so — and
            # nothing typed into it. His own words are not an assignment.
            with_claude = _wants_agent(text)
            pane_id = hp.new_pane(command=exe if with_claude else None, cwd=cwd,
                                  label="claude" if with_claude else "terminal")
            if not pane_id:
                return Reply(intent, "Couldn't open a pane.", raw=raw)
            if with_claude:
                hp.answer_trust(pane_id)
                hp.wait_ready(pane_id)
            store.record_run(text, "spawn", True,
                             [f"opened a {'claude' if with_claude else 'shell'} pane in {cwd}"],
                             target=pane_id)
            return Reply(intent, f"Opened a {'claude pane' if with_claude else 'terminal'} "
                         f"in {where}.", None, pane_id, raw)
        pane_id = hp.new_pane(command=exe, cwd=cwd, label=_label(brief))
        if not pane_id:
            intent = "pane"          # couldn't open one; fall through to an existing pane
        else:
            t = store.add(brief)
            store.set_state(t.id, "delegated")
            hp.answer_trust(pane_id)          # nothing is accepted until the folder is trusted
            ready = hp.wait_ready(pane_id)
            if ready and hp.type_into(pane_id, brief, user_originated=True):
                store.record_run(brief, "spawn", True,
                                 [f"opened a pane in {cwd}", "answered the folder-trust prompt",
                                  "typed the assignment"], task_id=t.id, target=pane_id)
                if researching:
                    store.watch(pane_id, text, t.id)
                    return Reply("research", "Put an agent on it — I'll bring the answer back "
                                 "when it lands.", t.id, pane_id, raw)
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
            if hp.message(pane_id, f"[from hearthsmith] {text}"):
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
        from hearthsmith.daemon import heartbeat
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
