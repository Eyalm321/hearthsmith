"""The last few things said between you and him, so a follow-up has something to follow.

Every `route` call — typed, spoken, `hearthsmith say` — logs your words and his reply, with the
task the exchange was about. Exchanges from the last `WINDOW_S` go into what the decider and the
compose model read, and `last_task` is what "it" / "that" means in "move it to Friday" or
"actually, that's done".
"""

from __future__ import annotations

import re
import time

from hearthsmith.store import Store

WINDOW_S = 10 * 60
KEEP = 6                       # exchanges
SCHEMA = """
CREATE TABLE IF NOT EXISTS talk (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      INTEGER NOT NULL,
  said    TEXT NOT NULL,
  reply   TEXT NOT NULL,
  intent  TEXT NOT NULL DEFAULT '',
  task_id TEXT
);
"""
PRONOUN = re.compile(r"\b(?:it|that|this|that\s+one|this\s+one|the\s+last\s+one)\b", re.IGNORECASE)


def _db(store: Store):
    store.db.executescript(SCHEMA)
    return store.db


def log(store: Store, said: str, reply: str, intent: str, task_id: str | None) -> None:
    _db(store).execute("INSERT INTO talk (at,said,reply,intent,task_id) VALUES (?,?,?,?,?)",
                       (int(time.time()), said, reply, intent, task_id))


def recent(store: Store, now: float | None = None) -> list[dict]:
    now = now or time.time()
    rows = _db(store).execute("SELECT * FROM talk WHERE at>=? ORDER BY id DESC LIMIT ?",
                              (int(now - WINDOW_S), KEEP)).fetchall()
    return [dict(r) for r in reversed(rows)]


def last_task(store: Store, now: float | None = None) -> str | None:
    """The task the conversation was most recently about."""
    return next((r["task_id"] for r in reversed(recent(store, now)) if r["task_id"]), None)


def refers_back(text: str) -> bool:
    return bool(PRONOUN.search(text))


def transcript(rows: list[dict]) -> str:
    return "\n".join(f"User: {r['said']}\nYou: {r['reply']}" for r in rows)
