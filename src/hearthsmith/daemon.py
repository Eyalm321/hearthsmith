"""hearthsmithd: one heartbeat = sense → decide → compose → deliver. Run once (`hearthsmithd --once`) from a
systemd timer, or loop (`hearthsmithd --every 600`). Every stage degrades; the loop never dies."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from hearthsmith import config
from hearthsmith.adapters import markdown
from hearthsmith.adapters.hyperpanes import Hyperpanes
from hearthsmith.compose import compose
from hearthsmith.decide import Decision, decide
from hearthsmith.sinks import HyperpanesSink, NotifySink, SpriteSink, VoiceSink
from hearthsmith.store import Store, Task

log = logging.getLogger("hearthsmith")


def build_state(store: Store, hp: Hyperpanes, cfg: config.Config) -> tuple[str, list[Task]]:
    """T0: the dense paragraph every backend reads. No model."""
    markdown.sync(cfg.nag.markdown_file, store)
    tasks = [t for t in store.list("open") if not t.snoozed]
    now = time.time()
    lines = [f"Local time {datetime.now():%A %H:%M}."]
    snap = hp.snapshot()
    lines.append(snap.summary() if snap else "hyperpanes is not running.")
    if tasks:
        lines.append(f"{len(tasks)} open tasks:")
        for t in tasks[:15]:
            due = ""
            if t.due:
                h = (t.due - now) / 3600
                due = f" — overdue by {-h:.0f}h" if h < 0 else f" — due in {h:.0f}h"
            proj = f" [{t.project}]" if t.project else ""
            nag = f" (nagged {t.nag_count}x)" if t.nag_count else ""
            lines.append(f"  - {t.title}{proj}{due}{nag}")
    else:
        lines.append("No open tasks.")
    last = store.last_nag_at()
    lines.append("Last nag: " + (f"{(now - last) / 60:.0f} min ago." if last else "never."))
    return "\n".join(lines), tasks


def in_quiet_hours(cfg: config.NagCfg) -> bool:
    start, end = cfg.quiet_hours
    h = datetime.now().hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def collect_research(cfg: config.Config, store: Store, hp: Hyperpanes,
                     settle_s: int = 45) -> list[dict]:
    """Bring back answers from agents that were sent off to research something. A pane counts as
    finished when it is idle and its screen has stopped changing — an agent mid-thought repaints
    constantly, so stillness is the signal."""
    import hashlib
    done = []
    for w in store.watches():
        snap = hp.snapshot(with_screens=False)
        pane = next((p for p in (snap.panes if snap else []) if p.id == w["pane_id"]), None)
        if pane is None:                       # closed before it answered
            store.unwatch(w["pane_id"])
            continue
        screen = hp.screen(w["pane_id"], tail=60)
        digest = hashlib.sha1(screen.encode()).hexdigest()[:16]
        still = store.touch_watch(w["pane_id"], digest)
        if pane.activity == "busy" or still < settle_s:
            continue
        answer = hp.last_answer(w["pane_id"])
        if len(answer) < 40:
            continue
        store.unwatch(w["pane_id"])
        if w["task_id"]:
            store.set_state(w["task_id"], "done")
            store.db.execute("UPDATE tasks SET notes=notes||? WHERE id=?",
                             (f"\n\nanswer: {answer}", w["task_id"]))
        store.record_run(w["question"], "research", True, ["asked an agent", "collected its answer"],
                         task_id=w["task_id"], target=w["pane_id"], started_at=w["started_at"])
        done.append({"question": w["question"], "answer": answer, "pane": w["pane_id"]})
    return done


SUGGEST = {
    "accept": "the suggested next step is the obvious continuation of the work and safe to run "
              "unattended; nothing else needs to finish first",
    "wait": "sensible, but a sibling pane on the same goal is still working and should finish "
            "first, or the pane itself might still change its mind",
    "dismiss": "off-track, redundant, or something the user clearly would not want done "
               "automatically (deploys, deletes, pushes, spending, anything irreversible)",
    "ask": "worth doing but the user should say yes first",
}


def gather_org(hp: Hyperpanes, pane, siblings: list) -> dict:
    """Everything the org already emits about goal `pane.meta.goal`, read, not asked for: the
    spec agent's text, each sibling's role/state/last words, reports on the bus, subtask states
    in the goal's work queue. Pure data; the models see only this."""
    goal = pane.meta.get("goal", "")
    project = pane.meta.get("project", "") or (pane.cwd or "")
    spec = next((q for q in siblings if q.meta.get("role") == "spec"), None)
    parent = pane.meta.get("parent")
    reports = []
    for pid in {pane.id, parent, spec.id if spec else None} - {None}:
        for m in hp.messages(pid):
            reports.append({"to": pid[:8], "from": str(m.get("from", ""))[:12],
                            "body": str(m.get("body", ""))[:300]})
    # queue name convention from the goal-orchestrator skill: <project name>-<goal id>
    qname = f"{Path(project).name}-{goal}" if project else goal
    tasks = hp.queue_tasks(qname) or hp.queue_tasks(goal)
    return {
        "goal": goal, "project": Path(project).name if project else "",
        "pane": {"label": pane.label, "role": pane.meta.get("role")},
        "spec_text": hp.last_answer(spec.id, 1200) if spec else "",
        "siblings": [{"label": q.label, "role": q.meta.get("role"), "activity": q.activity,
                      "last": hp.last_answer(q.id, 400)} for q in siblings],
        "reports": reports[-12:],
        "queue": {"name": qname, "tasks": tasks,
                  "counts": {st: sum(1 for t in tasks if t["state"] == st)
                             for st in ("queued", "claimed", "done", "failed", "dead")}},
    }


CONDENSE = (
    "You summarise the state of a software goal being worked by several AI agent panes so a "
    "separate decider can judge one pane's proposed next step. Write ONE paragraph, at most 120 "
    "words, plain prose, in this order: what the goal is; what is finished; what is in flight or "
    "blocked (name the subtask and who holds it); whether the PROPOSED STEP depends on anything "
    "not yet finished, and on what; whether it is irreversible (push, deploy, delete, spend, "
    "mark complete). State facts from the data only. Do not recommend."
)


def condense(cfg: config.Config, org: dict, proposed: str) -> str:
    """One chat call turns the gathered org into the paragraph Jev reads. Any failure → a
    mechanical summary from the queue counts, so the decision still happens on something."""
    from hearthsmith.compose import _openrouter
    body = json.dumps(org, ensure_ascii=False)[:24000]
    out = _openrouter(cfg.compose, [{"role": "system", "content": CONDENSE},
                                    {"role": "user", "content": f"PROPOSED STEP: {proposed}\n\nDATA:\n{body}"}],
                      model=cfg.compose.judge_model, max_tokens=220, reasoning=False)
    if out:
        return out[:900]
    k = org["queue"]["counts"]
    inflight = [t["title"] for t in org["queue"]["tasks"] if t["state"] in ("queued", "claimed")]
    busy = [q["label"] for q in org["siblings"] if q["activity"] == "busy"]
    return (f"Goal {org['goal']} in {org['project']}: {k['done']} subtask(s) done, "
            f"{k['claimed']} claimed, {k['queued']} queued, {k['failed'] + k['dead']} failed. "
            f"In flight: {'; '.join(inflight[:4]) or 'nothing'}. Busy panes: {', '.join(busy) or 'none'}. "
            f"(Condenser unavailable; this is a count-only summary.)")


def _judge(cfg: config.Config, sg: dict, siblings: list) -> tuple[str, float]:
    """Jev's call on one settled suggestion. Falls back to `ask` on any backend trouble — the
    conservative answer is the one that only costs the user a sentence."""
    from typesafe_sdk import Choice, Score

    from hearthsmith.route import _jev
    sib = "; ".join(f"{q.label}: {'busy' if q.activity == 'busy' else 'idle'}"
                    f"{' [' + q.meta.get('role') + ']' if q.meta.get('role') else ''}" for q in siblings) or "none"
    state = (f"Local time {datetime.now():%A %H:%M}.\n"
             f"An AI agent pane '{sg['label']}' (role {sg.get('role') or 'unknown'}, goal "
             f"{sg.get('goal') or 'unknown'}, project {sg['project'] or 'unknown'}) finished a turn "
             f"and now proposes as its own next prompt: \"{sg['text']}\".\n"
             f"It has been sitting unaccepted for {sg['age']}s.\n"
             f"Sibling panes on the same goal: {sib}.\n"
             f"Last thing the pane said: {sg.get('last_answer', '')[:600]}")
    if sg.get("org_summary"):
        state += f"\nState of the whole goal, condensed: {sg['org_summary']}"
    qs = {"verdict": Choice(instructions="What should happen to this suggested next prompt?",
                            criteria=SUGGEST)}
    if sg.get("org_summary"):
        qs["depends"] = Score(
            instructions="Does the proposed step depend on work that is not yet finished?",
            criteria=["nothing it needs is outstanding; everything it builds on is done",
                      "unclear, or it touches something a sibling is still changing",
                      "it clearly needs a subtask still in the queue, a sibling still working, or a report not yet in"])
    try:
        a = _jev(cfg.decide, state, qs)
        v = a["verdict"]["choice"]
        p = float(a["verdict"].get("probabilities", {}).get(v, 0.0))
        # Score comes back as a criterion index (0..len-1); 0..1 is what the rule reads
        dep = float(a.get("depends", {}).get("score", 0.0)) / 2.0 if "depends" in qs else 0.0
        sg["depends"] = dep
        if dep >= 0.5 and v == "accept":
            return "wait", dep  # hard rule: never run ahead of the org
        return v, p
    except Exception as e:  # noqa: BLE001 — decider down: ask, don't guess
        log.warning("suggestion judge failed: %s", e)
        return "ask", 0.0


def collect_suggestions(cfg: config.Config, store: Store, hp: Hyperpanes) -> list[dict]:
    """Notice a Claude pane offering its own next prompt (ghost text after a turn) and, once it
    has sat there unchanged long enough to not be you mid-sentence, deal with it once.

    observe: tell you. accept: in panes he spawned, Jev judges accept/wait/dismiss/ask; accept
    is Tab+Enter (named keys — no text can go in this way), after re-reading the input line so a
    suggestion the pane regenerated meanwhile is never the one pressed. Your own panes are only
    ever reported. Returns the ones that need your ear."""
    snap = hp.snapshot(with_screens=False)
    mode = cfg.hyperpanes.suggestions
    if not snap or mode == "off":
        return []
    settle = cfg.hyperpanes.suggestion_settle_s
    live = set()
    fresh = []
    for pane in snap.panes:
        if pane.activity == "busy":
            continue
        text = hp.input_line(pane.id)
        if not text:
            continue
        live.add(pane.id)
        proj = (snap.project_for_cwd(pane.cwd) or {}).get("name", "")
        # "same feature" = same goal when the org stamps one, else same project dir
        goal = pane.meta.get("goal")
        siblings = [q for q in snap.panes if q.id != pane.id
                    and ((goal and q.meta.get("goal") == goal) or (not goal and q.cwd == pane.cwd))]
        siblings_busy = sum(1 for q in siblings if q.activity == "busy")
        row = store.see_suggestion(pane.id, text, pane.label, proj, siblings_busy)
        if row["state"] != "seen" or row["age"] < settle:
            continue
        row.update(siblings_busy=siblings_busy, role=pane.meta.get("role"), goal=goal)
        pressable = pane.pressable(cfg.hyperpanes.suggestion_accept_roles) \
            and pane.meta.get("role") != "goals-orch"
        if mode != "accept" or not pressable:
            store.set_suggestion_state(pane.id, "reported")
            store.record_run(f"{pane.label} suggests: {text}", "suggestion", True,
                             [f"sat {row['age']}s", f"{siblings_busy} sibling(s) busy", "reported"],
                             target=pane.id)
            fresh.append(row)
            continue
        if siblings_busy:
            continue  # wait, silently; re-judged next heartbeat once they settle
        row["last_answer"] = hp.last_answer(pane.id)
        if goal and cfg.hyperpanes.suggestion_org_aware:
            org = gather_org(hp, pane, siblings)
            row["org_summary"] = condense(cfg, org, text)
        verdict, p = _judge(cfg, row, siblings)
        steps = [f"sat {row['age']}s", f"jev: {verdict} p={p:.2f}"]
        if "org_summary" in row:
            steps.append(f"depends={row.get('depends', 0):.2f}: {row['org_summary'][:300]}")
        if verdict == "accept" and p >= cfg.hyperpanes.suggestion_accept_min_p:
            # the pane may have regenerated it since we read; press only what we judged
            if hp.input_line(pane.id) != text:
                steps.append("changed under us; not pressed")
                store.drop_suggestion(pane.id)
                store.record_run(f"{pane.label} suggests: {text}", "suggestion", False, steps, target=pane.id)
                continue
            ok = hp.press(pane.id, "tab") and hp.press(pane.id, "enter")
            steps.append("pressed tab, enter" if ok else "press failed")
            store.set_suggestion_state(pane.id, "accepted" if ok else "seen")
            store.record_run(f"{pane.label} suggests: {text}", "suggestion", ok, steps, target=pane.id)
            continue
        if verdict == "dismiss":
            # leave it; the pane's next turn or the user's next key clears it. Nothing pressed:
            # a dismiss keystroke would be typing into their box.
            store.set_suggestion_state(pane.id, "dismissed")
            store.record_run(f"{pane.label} suggests: {text}", "suggestion", True, steps, target=pane.id)
            continue
        if verdict == "wait":
            store.record_run(f"{pane.label} suggests: {text}", "suggestion", True, steps, target=pane.id)
            continue
        store.set_suggestion_state(pane.id, "reported")
        store.record_run(f"{pane.label} suggests: {text}", "suggestion", True, [*steps, "asked you"],
                         target=pane.id)
        fresh.append(row)
    # a suggestion that vanished (accepted, dismissed, pane closed) is not ours to remember
    for old in store.suggestions():
        if old["pane_id"] not in live:
            store.drop_suggestion(old["pane_id"])
    return fresh


def heartbeat(cfg: config.Config, store: Store, hp: Hyperpanes, dry: bool = False,
              force: bool = False) -> dict:
    """force = the user asked ("Nag me now", `hearthsmith say "what should I do"`): skip the quiet-hours
    and min-gap gates and always say something, even if Jev would have stayed quiet."""
    sprite = SpriteSink(cfg.sprite_path)
    voice = VoiceSink(cfg.voice)
    # an answer you asked for outranks a reminder you didn't
    for found in collect_research(cfg, store, hp):
        text = f"Your answer on '{found['question'][:60]}': {found['answer'][:400]}"
        if not dry:
            if not sprite.send(text, "soon", None):
                NotifySink().send(text, "soon", None)
            voice.send(text, "soon", None)
            store.log_nag(found.get("task_id"), "research", "soon", text, "{}")
        return {"answered": found["question"], "answer": found["answer"][:400]}

    # a pane waiting on a yes from you outranks a reminder, but not an answer you asked for
    for sg in collect_suggestions(cfg, store, hp):
        hold = f" ({sg['siblings_busy']} other pane{'s' if sg['siblings_busy'] > 1 else ''} still busy there)" \
            if sg["siblings_busy"] else ""
        text = f"'{sg['label']}' wants to: {sg['text'][:140]}{hold}. Yes or no?"
        if not dry:
            if not sprite.send(text, "soon", None):
                NotifySink().send(text, "soon", None)
            voice.send(text, "soon", None)
            store.log_nag(None, "suggestion", "soon", text, "{}")
        return {"suggestion": sg["text"], "pane": sg["pane_id"], "text": text}

    # once a day each: where things stand, at the first heartbeat you're there for. Muted or
    # quiet hours hold it (not skip it) — the next heartbeat inside the window still owes it.
    if not force and not in_quiet_hours(cfg.nag) \
            and int(store.kv_get("muted_until", "0") or 0) <= time.time():
        from hearthsmith import brief
        if kind := brief.due_now(cfg.brief, store, idle=brief.idle_ms()):
            if dry:
                b = brief.make(cfg, store, kind)
                return {"brief": kind, "text": b["text"], "quiet": b["quiet"]}
            b = brief.deliver(cfg, store, kind)
            if not b["quiet"]:
                return {"brief": kind, "text": b["text"], "delivered": b["delivered"]}

    state, tasks = build_state(store, hp, cfg)
    last = store.last_nag_at()

    if not force:
        muted = int(store.kv_get("muted_until", "0") or 0)
        if muted > time.time():
            sprite.write("sleep")
            return {"skipped": "muted", "until": muted}
        if in_quiet_hours(cfg.nag):
            sprite.write("sleep")
            return {"skipped": "quiet_hours"}
        if last and time.time() - last < cfg.nag.min_gap_minutes * 60:
            sprite.write("idle")
            return {"skipped": "min_gap",
                    "next_in_min": round(cfg.nag.min_gap_minutes - (time.time() - last) / 60)}

    d: Decision = decide(cfg.decide, state, tasks, last, cfg.nag.min_gap_minutes)
    log.info("decision %s", d.as_json())
    if not force and (d.should_nag < 0.5 or d.task_id is None):
        # He works the forge when he has something to say. Using it as an ambient mood for
        # "there is pending work" left him hammering at nothing until the next heartbeat.
        sprite.write("idle", urgency=d.urgency)
        return {"skipped": "not_now", "decision": json.loads(d.as_json())}
    if d.task_id is None:
        if not tasks:
            text = "Ledger's clean. Nothing to hammer on."
            sprite.write("idle", text, "ignorable")
            if not dry and not sprite.alive():
                NotifySink().send(text, "ignorable", None)
            if not dry:
                voice.send(text, "ignorable", None)
            return {"forced": True, "text": text}
        d.task_id = tasks[0].id

    task = store.get(d.task_id)
    if task is None:
        return {"skipped": "task_vanished"}

    if d.channel == "delegate" and not cfg.hyperpanes.delegate_enabled:
        d.channel = "hyperpanes"  # no worker drains the queue yet; don't lose the task into it
    if d.channel == "delegate" and hp.alive():
        job = hp.enqueue(cfg.hyperpanes.delegate_queue, task.title,
                         f"Task from the hearthsmith ledger: {task.title}\n{task.notes}".strip(),
                         dedupe_key=f"hearthsmith:{task.id}")
        if job:
            store.set_state(task.id, "delegated")
            text = f"Handing '{task.title}' to a worker pane."
            sprite.write("forge", text, d.urgency)
            store.log_nag(task.id, "delegate", d.urgency, text, d.as_json())
            if not dry and not sprite.alive():
                NotifySink().send(text, d.urgency, task)
            return {"delegated": task.id, "job": job}

    text, tier = compose(cfg.compose, state, task.title, d.urgency, want_llm=d.needs_llm >= 0.5)
    result = {"task": task.id, "urgency": d.urgency, "text": text, "compose": tier,
              "decide": d.backend}
    if dry:
        return {"dry_run": result}

    delivered = []
    sinks = {"sprite": sprite, "notify": NotifySink(), "hyperpanes": HyperpanesSink(hp)}
    # he speaks if he's on screen; the pane agent gets it too when Jev picked that channel;
    # notify-send is the fallback when nobody else could deliver
    if sprite.send(text, d.urgency, task):
        delivered.append("sprite")
    if d.channel == "hyperpanes" and sinks["hyperpanes"].send(text, d.urgency, task):
        delivered.append("hyperpanes")
    if not delivered and NotifySink().send(text, d.urgency, task):
        delivered.append("notify")
    if not delivered:
        sprite.write("alert" if d.urgency == "now" else "forge", text, d.urgency)
    # the voice rides on top of whatever carried the words
    if voice.send(text, d.urgency, task):
        delivered.append("voice")
    store.mark_nagged(task.id)
    store.log_nag(task.id, ",".join(delivered) or "none", d.urgency, text, d.as_json())
    store.record_run(f"nag about {task.title}", "nag", bool(delivered),
                     [f"{d.backend} decided {d.urgency}", f"said: {text[:120]}"],
                     task_id=task.id, target=",".join(delivered) or "nobody")
    result["delivered"] = delivered
    return result


def main() -> None:
    ap = argparse.ArgumentParser(prog="hearthsmithd")
    ap.add_argument("--once", action="store_true", help="one heartbeat and exit")
    ap.add_argument("--every", type=int, default=600, help="seconds between heartbeats")
    ap.add_argument("--dry", action="store_true", help="decide + compose, deliver nothing")
    ap.add_argument("--force", action="store_true", help="user asked: ignore quiet hours / min gap, always speak")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    store = Store(cfg.db_path)
    hp = Hyperpanes(cfg.hyperpanes.control_file, cfg.hyperpanes.tail_lines,
                    cfg.hyperpanes.allow_pane_input)
    while True:
        try:
            out = heartbeat(cfg, store, hp, dry=a.dry, force=a.force)
            print(json.dumps(out))
        except Exception:  # the loop is the product; log and go again
            log.exception("heartbeat failed")
        if a.once:
            break
        time.sleep(a.every)


if __name__ == "__main__":
    main()
