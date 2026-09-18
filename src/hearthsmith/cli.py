"""hearthsmith: talk to the store from a shell. add / ls / done / block / snooze / state / nags."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from hearthsmith import config
from hearthsmith.adapters.hyperpanes import Hyperpanes
from hearthsmith.daemon import build_state
from hearthsmith.store import Store


def _fmt(t) -> str:
    due = ""
    if t.due:
        h = (t.due - time.time()) / 3600
        due = f"  overdue {-h:.0f}h" if h < 0 else f"  due {datetime.fromtimestamp(t.due):%m-%d %H:%M}"
    flags = "".join(f for f, on in (("z", t.snoozed), ("!", t.overdue)) if on)
    return f"{t.id}  {t.state:<9} {t.title}{due}  {('[' + t.project + ']') if t.project else ''} {flags}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="hearthsmith")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--due"); a.add_argument("--project"); a.add_argument("--tags", default="")
    ls = sub.add_parser("ls"); ls.add_argument("--all", action="store_true"); ls.add_argument("--project")
    for name in ("done", "block"):
        p = sub.add_parser(name); p.add_argument("task_id")
    sn = sub.add_parser("snooze"); sn.add_argument("task_id"); sn.add_argument("--minutes", type=int, default=120)
    sub.add_parser("state", help="print the T0 state paragraph the decider sees")
    sub.add_parser("nags")
    rr = sub.add_parser("runs", help="what he did, and the steps he took"); rr.add_argument("run_id", nargs="?"); rr.add_argument("-n", type=int, default=12); rr.add_argument("--task")
    mu = sub.add_parser("mute", help="stop all nagging for a while"); mu.add_argument("minutes", type=int, nargs="?", default=60)
    sub.add_parser("unmute")
    br = sub.add_parser("browse", help="Jev drives your Firefox toward a goal"); br.add_argument("goal", nargs="+"); br.add_argument("--json", action="store_true")
    do = sub.add_parser("do", help="computer use: Jev drives any app (AT-SPI + uinput)"); do.add_argument("goal", nargs="+"); do.add_argument("--json", action="store_true"); do.add_argument("--dry", action="store_true"); do.add_argument("--hands", action="store_true", help="let him use the real mouse/keyboard (exclusive — you two share one cursor)")
    sub.add_parser("windows", help="what the smith can see on screen")
    bw = sub.add_parser("web", help="run a goal in his Chrome (jev-ultrafast over CDP)"); bw.add_argument("goal", nargs="+"); bw.add_argument("--json", action="store_true")
    sy = sub.add_parser("say", help="tell the blacksmith something; Jev routes it"); sy.add_argument("text", nargs="+"); sy.add_argument("--json", action="store_true")
    sp = sub.add_parser("speak", help="hear him say it (AuK clone of assets/voice)"); sp.add_argument("text", nargs="+"); sp.add_argument("--no-play", action="store_true", help="synth only, print wav paths")
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
    elif args.cmd == "do":
        from hearthsmith.desktop.agent import run
        r = run(" ".join(args.goal), cfg, dry=args.dry, hands=args.hands or None)
        print(json.dumps(r.__dict__) if args.json else
              ("done" if r.ok else f"not done ({r.note})") + f" @ {r.window}\n  " + "\n  ".join(r.steps))
    elif args.cmd == "web":
        from hearthsmith.browser import jev
        r = jev.run(" ".join(args.goal), cfg=cfg)
        if args.json:
            print(json.dumps(r.__dict__))
        else:
            print(("done" if r.ok else f"not done ({r.note})")
                  + f" in {r.elapsed_ms}ms @ {r.title or r.url}"
                  + (f"  (median decision {r.median_decision_ms}ms)" if r.decide_ms else ""))
            for s in r.steps:
                print("  ", s)
    elif args.cmd == "windows":
        from hearthsmith.desktop import atspi
        for w in atspi.windows():
            print(f"{'*' if w.active else ' '} {w.app:<28} {w.title[:50]:<50} {w.x},{w.y} {w.w}x{w.h} {'shell' if w.shell_id else 'NO-GEOM'}")
    elif args.cmd == "browse":
        from hearthsmith.browse import browse
        r = browse(" ".join(args.goal), cfg)
        print(json.dumps(r.__dict__) if args.json else
              ("done" if r.ok else f"not done ({r.note})") + f" @ {r.title or r.url}\n  " + "\n  ".join(r.steps))
    elif args.cmd == "say":
        from hearthsmith.route import route
        r = route(" ".join(args.text), cfg)
        print(json.dumps({"intent": r.intent, "text": r.text, "task_id": r.task_id, "target": r.target}) if args.json
              else f"[{r.intent}] {r.text}")
        if not args.json:  # a human in a terminal hears him; the sprite speaks for itself
            from hearthsmith.voice import Voice
            Voice(cfg.voice).speak(r.text)
    elif args.cmd == "speak":
        from hearthsmith.voice import Voice
        v = Voice(cfg.voice)
        if not v.available():
            raise SystemExit(f"voice off or reference clip missing: {cfg.voice.ref}")
        text = " ".join(args.text)
        if args.no_play:
            print("\n".join(str(w) for w in v.synth(text)))
        elif not v.speak(text):
            raise SystemExit("could not speak (space down? no audio sink?) — see log")
    elif args.cmd == "runs":
        if args.run_id:
            r = store.run(args.run_id)
            if not r:
                print("no such run")
            else:
                print(f"{datetime.fromtimestamp(r['at']):%m-%d %H:%M}  {r['body']}  "
                      f"{'done' if r['ok'] else 'not done'}  {r['note']}")
                print(f"  goal: {r['goal']}")
                if r["target"]:
                    print(f"  on:   {r['target'][:100]}")
                for s in json.loads(r["steps"]):
                    print("   ", s)
                if lat := json.loads(r["decide_ms"]):
                    import statistics
                    print(f"  decisions: {len(lat)}, median {statistics.median(lat):.0f}ms")
                if r["seen"]:
                    print(f"  looked: {r['seen'][:160]}")
        else:
            for r in store.runs(args.n, args.task):
                mark = "✓" if r["ok"] else "×"
                print(f"{r['id']}  {datetime.fromtimestamp(r['at']):%m-%d %H:%M}  {mark} "
                      f"{r['body']:<8} {len(json.loads(r['steps'])):>2} steps  {r['goal'][:60]}")
    elif args.cmd == "nags":
        for n in store.recent_nags(10):
            print(f"{datetime.fromtimestamp(n['at']):%m-%d %H:%M}  {n['urgency']:<9} {n['channel']:<10} {n['text']}")
            d = json.loads(n["decision"])
            if e := d.get("backend_error"):
                print(f"           ! {e}")


if __name__ == "__main__":
    main()
