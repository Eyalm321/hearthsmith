"""The weekly review: once a week (Friday afternoon by default), the week read back — what got
done and whether it was on time, what's been carried along or nagged about for nothing, what the
agents did, how the focus went — then the week ahead, and a question: what's next week's focus?
The answer ("focus on the launch next week") lands in memory like any focus.

Same shape as the daily brief (brief.py does the scheduling, delivery and recording): facts from
the store with no model, a model only to phrase them, a template when none answers.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from hearthsmith.store import Store, Task

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def facts(store: Store, now: datetime | None = None, events: list | None = None,
          stuck_after: int = 4) -> dict:
    from hearthsmith.memory import Memory
    from hearthsmith.offer import declined
    now = now or datetime.now()
    since = int((now - timedelta(days=7)).timestamp())
    ahead = now + timedelta(days=7)
    top = [t for t in store.list(None) if not t.parent_id]

    done = sorted((t for t in top if t.state == "done" and t.updated_at >= since),
                  key=lambda t: t.updated_at)
    late = [t for t in done if t.due and t.updated_at > t.due]
    open_ = [t for t in top if t.state == "open"]
    overdue = sorted((t for t in open_ if t.due and t.due < now.timestamp()), key=lambda t: t.due)
    # carried: on the ledger over a week, never done, the most-nagged first
    carried = sorted((t for t in open_ if t.created_at < since and t not in overdue),
                     key=lambda t: -t.nag_count)
    stuck = [t for t in open_ if t.nag_count >= stuck_after and not store.children(t.id)
             and not declined(store, t)]
    runs = [r for r in store.runs(500) if r["at"] >= since]

    def days(t: Task) -> int:
        return max(1, int((now.timestamp() - t.due) // 86400))

    out: dict = {
        "kind": "weekly",
        "now": f"{now:%A %d %b}",
        "done_count": len(done),
        "done": [t.title for t in done][-8:],
        "done_late": [t.title for t in late][:5],
        "added_count": sum(1 for t in top if t.created_at >= since),
        "by_project": dict(Counter(t.project for t in done if t.project).most_common(4)),
        "best_day": (Counter(f"{datetime.fromtimestamp(t.updated_at):%A}" for t in done)
                     .most_common(1)[0][0] if len(done) >= 4 else None),
        "overdue": [f"{t.title} ({days(t)}d)" for t in overdue][:6],
        "carried": [f"{t.title}" + (f" (nagged {t.nag_count}×)" if t.nag_count else "")
                    for t in carried][:4],
        "stuck": [t.title for t in stuck][:4],
        "agents_handed": sum(1 for r in runs if r["body"] == "handoff" and r["ok"]),
        "agents_reported": sum(1 for r in runs if r["body"] in ("agent", "research")),
        "agents_failed": [r["goal"][:80] for r in runs if r["body"] in ("handoff", "spawn") and not r["ok"]][:3],
        "repeats_missed": [t.title for t in overdue if t.repeat][:4],
        "due_next_week": [f"{t.title} ({datetime.fromtimestamp(t.due):%a})" for t in open_
                          if t.due and now.timestamp() <= t.due < ahead.timestamp()][:8],
        "open_count": len(open_),
    }
    mem = Memory(store)
    if focus := mem.rules().get("focus"):
        out["focus"] = focus
        out["focus_done"] = sum(1 for t in done if mem.focused(t))
    if events:
        timed = [e for e in events if not e.all_day and now.timestamp() <= e.start < ahead.timestamp()]
        per_day = Counter(f"{datetime.fromtimestamp(e.start):%A}" for e in timed)
        hours = sum((e.end - e.start) for e in timed) / 3600
        out["meetings_next_week"] = len(timed)
        out["meeting_hours_next_week"] = round(hours, 1)
        if per_day:
            day, n = per_day.most_common(1)[0]
            out["busiest_day"] = f"{day} ({n} meetings)"
    return out


def _list(xs: list[str], n: int = 3) -> str:
    from hearthsmith.brief import _list as bl
    return bl(xs, n)


def template(f: dict) -> str:
    s = ["The week."]
    if f["done_count"]:
        s.append(f"You struck off {f['done_count']}" + (f", {len(f['done_late'])} of them late"
                                                         if f["done_late"] else "")
                 + f", and added {f['added_count']}.")
        if f.get("best_day"):
            s.append(f"{f['best_day']} did the most.")
    else:
        s.append(f"Nothing struck off; {f['added_count']} added.")
    if "focus" in f:
        s.append(f"The focus was {f['focus']}: {f['focus_done']} done for it.")
    if f["agents_handed"] or f["agents_reported"]:
        s.append(f"Agents took {f['agents_handed']} and reported back on {f['agents_reported']}.")
    if f["overdue"]:
        s.append(f"Still overdue: {_list(f['overdue'])}.")
    if f["stuck"]:
        s.append(f"Going nowhere: {_list(f['stuck'], 2)} — split them, hand them off, or drop them.")
    elif f["carried"]:
        s.append(f"Carried all week: {_list(f['carried'], 2)}.")
    if f["due_next_week"]:
        s.append(f"Next week: {_list(f['due_next_week'])}.")
    if f.get("meetings_next_week"):
        s.append(f"{f['meetings_next_week']} meetings next week"
                 + (f", {f['busiest_day']} the heaviest." if f.get("busiest_day") else "."))
    s.append("What's the focus next week?")
    return " ".join(s)


WHAT = ("the WEEKLY REVIEW: the week behind — how much got done and whether on time, what "
        "dragged or went nowhere (say plainly what to do about it: split, hand to an agent, or drop), "
        "how the focus went, what the agents did — then the week ahead: what's due and how full "
        "the calendar is. End by asking what next week's focus should be")


def prompt(f: dict) -> str:
    return (f"Facts (JSON, complete — mention nothing that isn't here):\n{json.dumps(f)}\n\n"
            f"Give {WHAT}. Five or six short sentences, spoken aloud. Name tasks by their titles. "
            "Skip empty categories. No lists, no markdown.")
