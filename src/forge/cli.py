"""forge: talk to the store from a shell. add / ls / done / block / snooze / state / nags."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from forge import config
from forge.adapters.hyperpanes import Hyperpanes
from forge.daemon import build_state
from forge.store import Store


def _fmt(t) -> str:
    due = ""
    if t.due:
        h = (t.due - time.time()) / 3600
        due = f"  overdue {-h:.0f}h" if h < 0 else f"  due {datetime.fromtimestamp(t.due):%m-%d %H:%M}"
    flags = "".join(f for f, on in (("z", t.snoozed), ("!", t.overdue)) if on)
    return f"{t.id}  {t.state:<9} {t.title}{due}  {('[' + t.project + ']') if t.project else ''} {flags}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="forge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--due"); a.add_argument("--project"); a.add_argument("--tags", default="")
    ls = sub.add_parser("ls"); ls.add_argument("--all", action="store_true"); ls.add_argument("--project")
    for name in ("done", "block"):
        p = sub.add_parser(name); p.add_argument("task_id")
    sn = sub.add_parser("snooze"); sn.add_argument("task_id"); sn.add_argument("--minutes", type=int, default=120)
    sub.add_parser("state", help="print the T0 state paragraph the decider sees")
    sub.add_parser("nags")
    mu = sub.add_parser("mute", help="stop all nagging for a while"); mu.add_argument("minutes", type=int, nargs="?", default=60)
    sub.add_parser("unmute")
    br = sub.add_parser("browse", help="Jev drives agent-browser toward a goal"); br.add_argument("goal", nargs="+"); br.add_argument("--json", action="store_true")
    sy = sub.add_parser("say", help="tell the blacksmith something; Jev routes it"); sy.add_argument("text", nargs="+"); sy.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    store = Store(cfg.db_path)
    if args.cmd == "add":
        due = int(datetime.fromisoformat(args.due).timestamp()) if args.due else None
        print(_fmt(store.add(args.title, due=due, project=args.project, tags=args.tags)))
    elif args.cmd == "ls":
        for t in store.list(None if args.all else "open", args.project):
            print(_fmt(t))
    elif args.cmd in ("done", "block"):
        t = store.get(args.task_id)
        print(_fmt(store.set_state(t.id, args.cmd if args.cmd == "done" else "blocked")) if t else "no such task")
    elif args.cmd == "snooze":
        t = store.get(args.task_id)
        print(_fmt(store.snooze(t.id, args.minutes)) if t else "no such task")
    elif args.cmd == "state":
        hp = Hyperpanes(cfg.hyperpanes.control_file, cfg.hyperpanes.tail_lines)
        print(build_state(store, hp, cfg)[0])
    elif args.cmd == "mute":
        until = int(time.time()) + args.minutes * 60
        store.kv_set("muted_until", str(until))
        print(f"muted until {datetime.fromtimestamp(until):%H:%M}")
    elif args.cmd == "unmute":
        store.kv_set("muted_until", "0"); print("unmuted")
    elif args.cmd == "browse":
        from forge.browse import browse
        r = browse(" ".join(args.goal), cfg)
        print(json.dumps(r.__dict__) if args.json else
              ("done" if r.ok else f"not done ({r.note})") + f" @ {r.title or r.url}\n  " + "\n  ".join(r.steps))
    elif args.cmd == "say":
        from forge.route import route
        r = route(" ".join(args.text), cfg)
        print(json.dumps({"intent": r.intent, "text": r.text, "task_id": r.task_id, "target": r.target}) if args.json
              else f"[{r.intent}] {r.text}")
    elif args.cmd == "nags":
        for n in store.recent_nags(10):
            print(f"{datetime.fromtimestamp(n['at']):%m-%d %H:%M}  {n['urgency']:<9} {n['channel']:<10} {n['text']}")
            d = json.loads(n["decision"])
            if e := d.get("backend_error"):
                print(f"           ! {e}")


if __name__ == "__main__":
    main()
