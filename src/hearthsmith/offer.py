"""When nagging isn't working, offer to do something about it.

A task nagged `nag.stuck_after` times with no steps and no movement is stuck; the next time the
heartbeat would nag about it, he offers instead: split it into steps, or hand it to an agent —
recommending one (an agent when the task belongs to a project hyperpanes knows, else a split).
He never does either on his own: you answer "split it" / "hand it off" / "yes" (his
recommendation) / "no", to him or from the ledger's ⋯ menu. "No" means he stops offering for
that task — and stops nagging about it too, until its due date moves or it gets a step.

The open offer lives in kv `offer`; declines in kv `offer_no:<task id>`.
"""

from __future__ import annotations

import json
import re
import time

from hearthsmith import config
from hearthsmith.store import Store, Task

RETRY_S = 3 * 86400          # an unanswered offer may come back after this
FRESH_S = 24 * 3600          # "split it" still means the offered task this long after
BARE_S = 30 * 60             # a bare "yes"/"no" only answers an offer this recent

SPLIT = re.compile(r"\b(?:split|break\s+(?:it\s+)?(?:down|up)|steps|chunk)\b", re.IGNORECASE)
HAND = re.compile(r"\b(?:hand|agent|claude|delegate|give\s+it|pass\s+it)\b", re.IGNORECASE)
NO = re.compile(r"^\s*(?:no|nope|nah|leave\s+it|not\s+now|don'?t|no\s+thanks|skip(?:\s+it)?|"
                r"stop\s+offering)\b", re.IGNORECASE)
YES = re.compile(r"^\s*(?:yes|yeah|yep|aye|sure|ok(?:ay)?|do\s+it|go\s+(?:ahead|on)|please)\b[\s.!]*$",
                 re.IGNORECASE)


def declined(store: Store, t: Task) -> bool:
    """Declined, and nothing about the task has changed since (same due, still no steps)."""
    raw = store.kv_get(f"offer_no:{t.id}")
    return bool(raw) and json.loads(raw).get("due") == t.due and not store.children(t.id)


def stuck(cfg: config.Config, store: Store, t: Task, now: float | None = None) -> bool:
    now = now or time.time()
    if t.state != "open" or t.parent_id or t.nag_count < cfg.nag.stuck_after or store.children(t.id):
        return False
    if declined(store, t):
        return False
    last = pending(store)
    return not (last and last["task_id"] == t.id and now - last["at"] < RETRY_S)


def recommend(t: Task, snap) -> str:
    """An agent when the task lives in a project there's a folder for; a split otherwise."""
    for p in (snap.projects if snap else []):
        if t.project and t.project in (p.get("id"), p.get("name")):
            return "hand"
    return "split"


def make(store: Store, t: Task, snap) -> str:
    rec = recommend(t, snap)
    store.kv_set("offer", json.dumps({"task_id": t.id, "at": int(time.time()), "recommend": rec}))
    first, other = (("hand it to an agent", "split it into steps") if rec == "hand"
                    else ("split it into steps", "hand it to an agent"))
    return (f"'{t.title}' has sat through {t.nag_count} nags without moving. Nagging again won't "
            f"help. Want me to {first}? Or {other}, or leave it be.")


def pending(store: Store) -> dict | None:
    raw = store.kv_get("offer")
    return json.loads(raw) if raw else None


def answer(cfg: config.Config, store: Store, text: str, now: float | None = None) -> str | None:
    """Reply to the open offer if `text` is an answer to it; None when it isn't."""
    now = now or time.time()
    o = pending(store)
    if not o or now - o["at"] > FRESH_S:
        return None
    t = store.get(o["task_id"])
    if t is None or t.state != "open":
        store.kv_set("offer", "")
        return None
    bare = now - o["at"] < BARE_S and (store.last_nag_at() or 0) <= o["at"] + 5
    short = len(text.split()) <= 6          # "split it" answers; "split the move into steps" is its own ask
    if NO.match(text) and bare:
        return decline(store, t)
    if short and SPLIT.search(text) and not HAND.search(text):
        return do(cfg, store, t, "split")
    if short and HAND.search(text):
        return do(cfg, store, t, "hand")
    if YES.match(text) and bare:
        return do(cfg, store, t, o["recommend"])
    return None


def decline(store: Store, t: Task) -> str:
    store.kv_set(f"offer_no:{t.id}", json.dumps({"at": int(time.time()), "due": t.due}))
    store.kv_set("offer", "")
    return f"Leaving '{t.title}' be. I won't nag about it again unless its date moves."


def do(cfg: config.Config, store: Store, t: Task, how: str) -> str:
    store.kv_set("offer", "")
    if how == "hand":
        from hearthsmith.handoff import hand
        return hand(cfg, store, t).text
    from hearthsmith.steps import split
    made = split(cfg, store, t)
    if not made:
        return f"Couldn't break '{t.title}' down — no model gave me usable steps."
    return f"'{t.title}' in {len(made)} steps. Start with: {made[0].title}."
