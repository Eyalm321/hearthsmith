"""forged: one heartbeat = sense → decide → compose → deliver. Run once (`forged --once`) from a
systemd timer, or loop (`forged --every 600`). Every stage degrades; the loop never dies."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime

from forge import config
from forge.adapters import markdown
from forge.adapters.hyperpanes import Hyperpanes
from forge.compose import compose
from forge.decide import Decision, decide
from forge.sinks import HyperpanesSink, NotifySink, SpriteSink
from forge.store import Store, Task

log = logging.getLogger("forge")


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


def heartbeat(cfg: config.Config, store: Store, hp: Hyperpanes, dry: bool = False,
              force: bool = False) -> dict:
    """force = the user asked ("Nag me now", `forge say "what should I do"`): skip the quiet-hours
    and min-gap gates and always say something, even if Jev would have stayed quiet."""
    state, tasks = build_state(store, hp, cfg)
    sprite = SpriteSink(cfg.sprite_path)
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
            return {"forced": True, "text": text}
        d.task_id = tasks[0].id

    task = store.get(d.task_id)
    if task is None:
        return {"skipped": "task_vanished"}

    if d.channel == "delegate" and not cfg.hyperpanes.delegate_enabled:
        d.channel = "hyperpanes"  # no worker drains the queue yet; don't lose the task into it
    if d.channel == "delegate" and hp.alive():
        job = hp.enqueue(cfg.hyperpanes.delegate_queue, task.title,
                         f"Task from the forge ledger: {task.title}\n{task.notes}".strip(),
                         dedupe_key=f"forge:{task.id}")
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
    store.mark_nagged(task.id)
    store.log_nag(task.id, ",".join(delivered) or "none", d.urgency, text, d.as_json())
    store.record_run(f"nag about {task.title}", "nag", bool(delivered),
                     [f"{d.backend} decided {d.urgency}", f"said: {text[:120]}"],
                     task_id=task.id, target=",".join(delivered) or "nobody")
    result["delivered"] = delivered
    return result


def main() -> None:
    ap = argparse.ArgumentParser(prog="forged")
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
