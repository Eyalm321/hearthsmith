"""The morning brief and the evening wrap: once a day each, he tells you where things stand.

    morning  what's overdue, what's due today, what landed overnight (answers, finished agents),
             what's stuck, what a pane is waiting on you for
    evening  what you finished today, what's still due today, what's due tomorrow

The facts are gathered from the store with no model (`facts`), so what he says is what the
ledger holds; a compose model only phrases it, and a template does when no model answers.

The heartbeat delivers each once per day, at `brief.morning` / `brief.evening` or the first
heartbeat after — and, with `brief.wait_for_you`, only once you have touched the keyboard or
mouse in the last couple of minutes, so the morning brief greets you instead of an empty room.
`hearthsmith brief` gives one on demand; so does telling him "brief me".
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timedelta

from hearthsmith import config
from hearthsmith.compose import _ollama, _openrouter, _trim
from hearthsmith.store import Store, Task

AGENT_BODIES = ("research", "spawn", "pane", "agent")


def kind_for(now: datetime) -> str:
    """On demand: before 15:00 you want the day ahead, after it the day behind."""
    return "morning" if now.hour < 15 else "evening"


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def facts(store: Store, kind: str, now: datetime | None = None) -> dict:
    """Everything the brief may mention, straight from the store."""
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    # morning looks back to last night's wrap (or 16h); evening looks at today
    last = int(store.kv_get("brief_last_at", "0") or 0)
    since = (max(last, int((now - timedelta(hours=16)).timestamp())) if kind == "morning"
             else int(today.timestamp()))
    open_ = store.list("open")

    def within(t: Task, a: datetime, b: datetime) -> bool:
        return t.due is not None and a.timestamp() <= t.due < b.timestamp()

    runs = [r for r in store.runs(200) if r["at"] >= since]
    out = {
        "kind": kind,
        "now": f"{now:%A %H:%M}",
        "overdue": [t.title for t in open_ if t.due is not None and t.due < now.timestamp()],
        "due_today": [f"{t.title} ({datetime.fromtimestamp(t.due):%H:%M})" for t in open_
                      if within(t, now, tomorrow)],
        "open_count": len(open_),
        "blocked": [t.title for t in store.list("blocked")],
        "done": _done_since(store, since),
        "agents_finished": [r["goal"][:100] for r in runs if r["body"] in AGENT_BODIES and r["ok"]],
        "agents_failed": [r["goal"][:100] for r in runs if r["body"] in AGENT_BODIES and not r["ok"]],
        "waiting_on_you": [f"'{s['label']}' wants to: {s['text'][:100]}" for s in store.suggestions()
                           if s["state"] == "reported"],
    }
    if kind == "evening":
        out["due_tomorrow"] = [t.title for t in open_ if within(t, tomorrow, tomorrow + timedelta(days=1))]
        out["added_today"] = len([t for t in store.list(None) if t.created_at >= today.timestamp()])
    else:
        week = today + timedelta(days=7)
        out["due_this_week"] = len([t for t in open_ if within(t, tomorrow, week)])
        out["someday"] = len([t for t in open_ if t.due is None])
    return out


def _done_since(store: Store, since: int) -> list[str]:
    """Finished tasks, a step only when its task isn't also on the list — finishing the launch
    finishes its steps, and "you struck off 'deploy' and 'launch'" counts the same work twice."""
    done = [t for t in store.list("done") if t.updated_at >= since]
    ids = {t.id for t in done}
    return [t.title for t in done if t.parent_id not in ids]


def quiet(f: dict) -> bool:
    """An evening with nothing done and nothing coming isn't worth a speech."""
    return f["kind"] == "evening" and not any(
        f[k] for k in ("done", "overdue", "due_today", "due_tomorrow", "agents_finished",
                       "agents_failed", "waiting_on_you"))


def _list(xs: list[str], n: int = 3) -> str:
    xs = [f"'{x}'" for x in xs[:n]] + ([f"{len(xs) - n} more"] if len(xs) > n else [])
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def template(f: dict) -> str:
    """The brief without a model. Plain, complete, never wrong."""
    s = []
    if f["kind"] == "morning":
        s.append("Morning.")
        if f["agents_finished"]:
            s.append(f"Overnight the agents finished {_list(f['agents_finished'], 2)}.")
        if f["overdue"]:
            s.append(f"Overdue: {_list(f['overdue'])}.")
        if f["due_today"]:
            s.append(f"Due today: {_list(f['due_today'])}.")
        if not f["overdue"] and not f["due_today"]:
            s.append("Nothing due today" + (f"; {f['due_this_week']} due later this week."
                                            if f["due_this_week"] else "."))
    else:
        s.append(f"Day's end. You struck off {_list(f['done'])}." if f["done"]
                 else "Day's end.")
        if f["agents_finished"]:
            s.append(f"The agents finished {_list(f['agents_finished'], 2)}.")
        if f["overdue"] or f["due_today"]:
            s.append(f"Still open: {_list(f['overdue'] + f['due_today'])}.")
        if f["due_tomorrow"]:
            s.append(f"Tomorrow: {_list(f['due_tomorrow'])}.")
    if f["agents_failed"]:
        s.append(f"{_list(f['agents_failed'], 1)} didn't finish.")
    if f["blocked"]:
        s.append(f"Stuck: {_list(f['blocked'], 2)}.")
    if f["waiting_on_you"]:
        s.append(f"{len(f['waiting_on_you'])} pane{'s' if len(f['waiting_on_you']) > 1 else ''} "
                 "waiting on a yes from you.")
    return " ".join(s)


def phrase(cfg: config.ComposeCfg, f: dict) -> tuple[str, str]:
    """(text, tier). The model gets the facts and may only rephrase them."""
    what = ("the MORNING BRIEF: start the day — what landed overnight, what's overdue, what's due "
            "today, anything stuck or waiting on the user"
            if f["kind"] == "morning" else
            "the EVENING WRAP: close the day — what got done, what's still open today, what's "
            "due tomorrow")
    msgs = [{"role": "system", "content": cfg.persona},
            {"role": "user", "content":
                f"Facts (JSON, complete — mention nothing that isn't here):\n{json.dumps(f)}\n\n"
                f"Give {what}. Three or four short sentences, spoken aloud. Most important first. "
                "Name tasks by their titles. Skip empty categories. No lists, no markdown."}]
    for tier, fn in (("ornith", _ollama), ("openrouter", _openrouter)):
        if out := fn(cfg, msgs):
            return _trim(out), tier
    return template(f), "template"


def idle_ms() -> int | None:
    """How long since you last touched the keyboard or mouse (GNOME's idle monitor). None when
    it can't be asked — treated as present, so a missing bus never swallows the brief."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                              "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                              "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
                             capture_output=True, text=True, timeout=3, check=True).stdout
        return int(out.strip().strip("(),").split()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def due_now(cfg: config.BriefCfg, store: Store, now: datetime | None = None,
            idle: int | None = None) -> str | None:
    """Which brief the heartbeat owes you right now, if any."""
    if not cfg.enabled:
        return None
    now = now or datetime.now()
    day = f"{now:%Y-%m-%d}"
    hm = (now.hour, now.minute)
    kind = None
    if (_hm(cfg.morning) <= hm and now.hour < cfg.morning_until
            and store.kv_get("brief_morning") != day):
        kind = "morning"
    elif _hm(cfg.evening) <= hm and store.kv_get("brief_evening") != day:
        kind = "evening"
    if kind and cfg.wait_for_you and idle is not None and idle > cfg.present_within_s * 1000:
        return None        # nobody at the desk; the next heartbeat asks again
    return kind


def mark(store: Store, kind: str, now: datetime | None = None) -> None:
    now = now or datetime.now()
    store.kv_set(f"brief_{kind}", f"{now:%Y-%m-%d}")
    store.kv_set("brief_last_at", str(int(now.timestamp())))


def make(cfg: config.Config, store: Store, kind: str | None = None) -> dict:
    """Gather + phrase one brief. Returns {kind, text, tier, facts, quiet}."""
    kind = kind or kind_for(datetime.now())
    f = facts(store, kind)
    if quiet(f):
        return {"kind": kind, "text": "", "tier": "none", "facts": f, "quiet": True}
    text, tier = phrase(cfg.compose, f)
    return {"kind": kind, "text": text, "tier": tier, "facts": f, "quiet": False}


def record(store: Store, b: dict, delivered: list[str], started: float) -> None:
    store.log_nag(None, ",".join(delivered) or "none", "ignorable", b["text"],
                  json.dumps({"brief": b["kind"], "tier": b["tier"]}))
    store.record_run(f"{b['kind']} brief", "brief", bool(delivered),
                     [f"phrased by {b['tier']}", f"said: {b['text'][:200]}"],
                     target=",".join(delivered) or "nobody", started_at=int(started))


def deliver(cfg: config.Config, store: Store, kind: str | None = None) -> dict:
    """Make one and put it on the sprite (notify-send when he's hidden) and in his voice."""
    from hearthsmith.sinks import NotifySink, SpriteSink, VoiceSink
    started = time.time()
    b = make(cfg, store, kind)
    if b["quiet"]:
        mark(store, b["kind"])
        return {**b, "delivered": []}
    delivered = []
    if SpriteSink(cfg.sprite_path).send(b["text"], "soon", None):
        delivered.append("sprite")
    elif NotifySink().send(b["text"], "soon", None):
        delivered.append("notify")
    if VoiceSink(cfg.voice).send(b["text"], "soon", None):
        delivered.append("voice")
    mark(store, b["kind"])
    record(store, b, delivered, started)
    return {**b, "delivered": delivered}
