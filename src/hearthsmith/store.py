"""The task store. SQLite, one table, the pet owns it. Harnesses are clients via hearthsmith-mcp."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'open',   -- open | done | blocked | delegated
  due         INTEGER,                        -- epoch seconds, nullable
  project     TEXT,                           -- hyperpanes project id or free text
  source      TEXT NOT NULL DEFAULT 'hearthsmith',  -- hearthsmith | markdown | dsh | github | ...
  source_id   TEXT,
  tags        TEXT NOT NULL DEFAULT '',       -- comma-separated
  notes       TEXT NOT NULL DEFAULT '',
  snoozed_until INTEGER,
  nag_count   INTEGER NOT NULL DEFAULT 0,
  last_nag_at INTEGER,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  repeat      TEXT NOT NULL DEFAULT '',       -- when.py rule: 1d 2w 1m weekday mon,thu
  parent_id   TEXT,                           -- a step of another task
  next_id     TEXT                            -- the occurrence spawned when this one was done
);
CREATE INDEX IF NOT EXISTS tasks_parent ON tasks(parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_source ON tasks(source, source_id) WHERE source_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS nags (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        INTEGER NOT NULL,
  task_id   TEXT,
  channel   TEXT NOT NULL,
  urgency   TEXT NOT NULL,
  text      TEXT NOT NULL,
  decision  TEXT NOT NULL                     -- json blob of the Jev/adapter answers
);
-- What he actually did, so "what happened with that task?" is a query and not an archaeology
-- dig through a terminal's scrollback.
CREATE TABLE IF NOT EXISTS runs (
  id        TEXT PRIMARY KEY,
  at        INTEGER NOT NULL,
  ended_at  INTEGER,
  task_id   TEXT,
  goal      TEXT NOT NULL,
  body      TEXT NOT NULL,      -- browser | desktop | spawn | pane | nag
  ok        INTEGER NOT NULL DEFAULT 0,
  note      TEXT NOT NULL DEFAULT '',
  target    TEXT,               -- pane id, url, window — whatever it acted on
  steps     TEXT NOT NULL DEFAULT '[]',
  decide_ms TEXT NOT NULL DEFAULT '[]',
  seen      TEXT NOT NULL DEFAULT ''   -- what the verifier saw, when it looked
);
CREATE INDEX IF NOT EXISTS runs_at ON runs(at DESC);
-- Work handed to an agent that will take minutes. The heartbeat checks these and brings the
-- answer back, so "research X" ends with an answer rather than a pane you have to remember.
CREATE TABLE IF NOT EXISTS watches (
  pane_id   TEXT PRIMARY KEY,
  task_id   TEXT,
  question  TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  last_hash TEXT NOT NULL DEFAULT '',
  stable_since INTEGER,
  kind      TEXT NOT NULL DEFAULT 'research'   -- research: answer closes it | task: report reopens it
);
-- A Claude pane's own "next prompt" suggestion, sitting unaccepted in its input box. One row
-- per pane: the text changes as the pane regenerates it, so it is keyed by pane, not text.
CREATE TABLE IF NOT EXISTS suggestions (
  pane_id    TEXT PRIMARY KEY,
  label      TEXT NOT NULL DEFAULT '',
  project    TEXT NOT NULL DEFAULT '',
  text       TEXT NOT NULL,
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  siblings_busy INTEGER NOT NULL DEFAULT 0,
  state      TEXT NOT NULL DEFAULT 'seen'   -- seen | reported | accepted | dismissed
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


@dataclass
class Task:
    id: str
    title: str
    state: str = "open"
    due: int | None = None
    project: str | None = None
    source: str = "hearthsmith"
    source_id: str | None = None
    tags: str = ""
    notes: str = ""
    snoozed_until: int | None = None
    nag_count: int = 0
    last_nag_at: int | None = None
    created_at: int = 0
    updated_at: int = 0
    repeat: str = ""
    parent_id: str | None = None
    next_id: str | None = None

    @property
    def overdue(self) -> bool:
        return self.due is not None and self.due < time.time() and self.state == "open"

    @property
    def snoozed(self) -> bool:
        return self.snoozed_until is not None and self.snoozed_until > time.time()

    def as_dict(self) -> dict:
        return asdict(self)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # columns added after the table first shipped; CREATE IF NOT EXISTS won't add them
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(tasks)")}
        if have:
            for col, ddl in (("repeat", "TEXT NOT NULL DEFAULT ''"), ("parent_id", "TEXT"),
                             ("next_id", "TEXT")):
                if col not in have:
                    self.db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(watches)")}
        if have and "kind" not in have:
            self.db.execute("ALTER TABLE watches ADD COLUMN kind TEXT NOT NULL DEFAULT 'research'")
        self.db.executescript(SCHEMA)

    # -- tasks -----------------------------------------------------------------------------

    def add(self, title: str, *, due: int | None = None, project: str | None = None,
            source: str = "hearthsmith", source_id: str | None = None, tags: str = "",
            notes: str = "", repeat: str = "", parent_id: str | None = None) -> Task:
        now = int(time.time())
        if parent_id and (parent := self.get(parent_id)):
            parent_id, project = parent.id, project or parent.project   # a step lives where its task does
        t = Task(id=uuid.uuid4().hex[:12], title=title, due=due, project=project, source=source,
                 source_id=source_id, tags=tags, notes=notes, created_at=now, updated_at=now,
                 repeat=repeat, parent_id=parent_id)
        self.db.execute(
            "INSERT INTO tasks (id,title,state,due,project,source,source_id,tags,notes,"
            "created_at,updated_at,repeat,parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.id, t.title, t.state, t.due, t.project, t.source, t.source_id, t.tags, t.notes,
             t.created_at, t.updated_at, t.repeat, t.parent_id))
        return t

    def upsert_external(self, source: str, source_id: str, title: str, **kw) -> Task:
        """One-way import: external source wins on title/due, hearthsmith keeps its own nag state."""
        row = self.db.execute("SELECT * FROM tasks WHERE source=? AND source_id=?",
                              (source, source_id)).fetchone()
        if row is None:
            return self.add(title, source=source, source_id=source_id, **kw)
        self.db.execute("UPDATE tasks SET title=?, due=?, project=COALESCE(?,project), updated_at=? "
                        "WHERE id=?", (title, kw.get("due"), kw.get("project"), int(time.time()),
                                       row["id"]))
        return self.get(row["id"])

    def get(self, task_id: str | None) -> Task | None:
        if not task_id:
            return None
        row = self.db.execute("SELECT * FROM tasks WHERE id=? OR id LIKE ?",
                              (task_id, task_id + "%")).fetchone()
        return Task(**dict(row)) if row else None

    def list(self, state: str | None = "open", project: str | None = None) -> list[Task]:
        q, args = "SELECT * FROM tasks", []
        conds = []
        if state:
            conds.append("state=?"); args.append(state)
        if project:
            conds.append("project=?"); args.append(project)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY COALESCE(due, 1e12), created_at"
        return [Task(**dict(r)) for r in self.db.execute(q, args)]

    def set_state(self, task_id: str, state: str) -> Task | None:
        """Every way a task gets finished comes through here — ledger, CLI, MCP, "that's done" —
        so this is where a repeat rolls over and a finished task takes its open steps with it."""
        self.db.execute("UPDATE tasks SET state=?, updated_at=? WHERE id=?",
                        (state, int(time.time()), task_id))
        t = self.get(task_id)
        if t and state == "done":
            for c in self.children(t.id, "open"):
                self.set_state(c.id, "done")
            if t.repeat and not (t.next_id and self.get(t.next_id)):
                self._roll(t)
                t = self.get(task_id)
        return t

    def _roll(self, t: Task) -> Task:
        """The next occurrence of a repeating task: same title, project, tags and steps (fresh,
        open), due at the rule's next date. Done→reopen→done again won't make a second one."""
        from hearthsmith.when import next_due
        nxt = self.add(t.title, due=next_due(t.repeat, t.due), project=t.project, tags=t.tags,
                       repeat=t.repeat, parent_id=t.parent_id)
        for c in self.children(t.id):
            self.add(c.title, project=c.project, tags=c.tags, parent_id=nxt.id)
        self.db.execute("UPDATE tasks SET next_id=? WHERE id=?", (nxt.id, t.id))
        return nxt

    def children(self, task_id: str, state: str | None = None) -> list[Task]:
        q, args = "SELECT * FROM tasks WHERE parent_id=?", [task_id]
        if state:
            q += " AND state=?"; args.append(state)
        return [Task(**dict(r)) for r in self.db.execute(q + " ORDER BY created_at, rowid", args)]

    def edit(self, task_id: str, **fields) -> Task | None:
        """Change what the task says (title, due, project, tags, notes); nag state is left alone."""
        cols = {k: v for k, v in fields.items()
                if k in ("title", "due", "project", "tags", "notes", "repeat", "parent_id")}
        if cols:
            sets = ", ".join(f"{k}=?" for k in cols)  # column names come from the whitelist above
            self.db.execute(f"UPDATE tasks SET {sets}, updated_at=? WHERE id=?",
                            (*cols.values(), int(time.time()), task_id))
        return self.get(task_id)

    def delete(self, task_id: str) -> None:
        """Its steps go with it."""
        for c in self.children(task_id):
            self.delete(c.id)
        self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def snooze(self, task_id: str, minutes: int) -> Task | None:
        until = int(time.time()) + minutes * 60
        self.db.execute("UPDATE tasks SET snoozed_until=?, updated_at=? WHERE id=?",
                        (until, int(time.time()), task_id))
        return self.get(task_id)

    def mark_nagged(self, task_id: str | None) -> None:
        if task_id:
            self.db.execute("UPDATE tasks SET nag_count=nag_count+1, last_nag_at=? WHERE id=?",
                            (int(time.time()), task_id))

    # -- runs ------------------------------------------------------------------------------

    def record_run(self, goal: str, body: str, ok: bool, steps: list[str],
                   task_id: str | None = None, note: str = "", target: str | None = None,
                   decide_ms: list | None = None, seen: str = "",
                   started_at: int | None = None) -> str:
        """One row per thing he was asked to do, with the steps he took. Values he was given by
        the user are already masked upstream ("(from you)") — nothing secret reaches here."""
        import json as _json
        rid = uuid.uuid4().hex[:12]
        now = int(time.time())
        self.db.execute(
            "INSERT INTO runs (id,at,ended_at,task_id,goal,body,ok,note,target,steps,decide_ms,seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, started_at or now, now, task_id, goal, body, int(ok), note, target,
             _json.dumps(steps), _json.dumps(decide_ms or []), seen))
        return rid

    def last_run(self, task_id: str) -> dict | None:
        """The run that most recently *finished* for a task — a watched agent's run starts when
        it was handed the work, so start time would put the hand-off after its own report."""
        r = self.db.execute("SELECT * FROM runs WHERE task_id=? ORDER BY COALESCE(ended_at, at) DESC, "
                            "rowid DESC LIMIT 1", (task_id,)).fetchone()
        return dict(r) if r else None

    def runs(self, n: int = 20, task_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM runs"
        args: list = []
        if task_id:
            q += " WHERE task_id=?"
            args.append(task_id)
        q += " ORDER BY at DESC LIMIT ?"
        args.append(n)
        return [dict(r) for r in self.db.execute(q, args)]

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE id=? OR id LIKE ?",
                              (run_id, run_id + "%")).fetchone()
        return dict(row) if row else None

    # -- watches ---------------------------------------------------------------------------

    def watch(self, pane_id: str, question: str, task_id: str | None = None,
              kind: str = "research") -> None:
        self.db.execute("INSERT OR REPLACE INTO watches (pane_id,task_id,question,started_at,kind) "
                        "VALUES (?,?,?,?,?)", (pane_id, task_id, question, int(time.time()), kind))

    def watches(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM watches ORDER BY started_at")]

    def touch_watch(self, pane_id: str, digest: str) -> int:
        """Remember what the pane looked like; returns how long it has looked the same."""
        row = self.db.execute("SELECT last_hash, stable_since FROM watches WHERE pane_id=?",
                              (pane_id,)).fetchone()
        now = int(time.time())
        if row is None:
            return 0
        if row["last_hash"] != digest:
            self.db.execute("UPDATE watches SET last_hash=?, stable_since=? WHERE pane_id=?",
                            (digest, now, pane_id))
            return 0
        since = row["stable_since"] or now
        return now - since

    def unwatch(self, pane_id: str) -> None:
        self.db.execute("DELETE FROM watches WHERE pane_id=?", (pane_id,))

    # -- suggestions -----------------------------------------------------------------------

    def see_suggestion(self, pane_id: str, text: str, label: str, project: str,
                       siblings_busy: int) -> dict:
        """Upsert what the pane is offering; returns the row with `age` = seconds the same text
        has been sitting there (0 when it just appeared or changed)."""
        now = int(time.time())
        row = self.db.execute("SELECT * FROM suggestions WHERE pane_id=?", (pane_id,)).fetchone()
        if row is None or row["text"] != text:
            self.db.execute("INSERT OR REPLACE INTO suggestions "
                            "(pane_id,label,project,text,first_seen,last_seen,siblings_busy,state) "
                            "VALUES (?,?,?,?,?,?,?,'seen')",
                            (pane_id, label, project, text, now, now, siblings_busy))
            first = now
        else:
            first = row["first_seen"]
            self.db.execute("UPDATE suggestions SET last_seen=?, siblings_busy=?, label=?, project=? "
                            "WHERE pane_id=?", (now, siblings_busy, label, project, pane_id))
        out = dict(self.db.execute("SELECT * FROM suggestions WHERE pane_id=?", (pane_id,)).fetchone())
        out["age"] = now - first
        return out

    def set_suggestion_state(self, pane_id: str, state: str) -> None:
        self.db.execute("UPDATE suggestions SET state=? WHERE pane_id=?", (state, pane_id))

    def drop_suggestion(self, pane_id: str) -> None:
        self.db.execute("DELETE FROM suggestions WHERE pane_id=?", (pane_id,))

    def suggestions(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM suggestions ORDER BY first_seen")]

    # -- nag log + kv ----------------------------------------------------------------------

    def log_nag(self, task_id: str | None, channel: str, urgency: str, text: str,
                decision: str) -> None:
        self.db.execute("INSERT INTO nags (at,task_id,channel,urgency,text,decision) "
                        "VALUES (?,?,?,?,?,?)",
                        (int(time.time()), task_id, channel, urgency, text, decision))

    def last_nag_at(self) -> int | None:
        row = self.db.execute("SELECT MAX(at) AS at FROM nags").fetchone()
        return row["at"]

    def recent_nags(self, n: int = 5) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM nags ORDER BY at DESC LIMIT ?", (n,))]

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def kv_set(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, v))
