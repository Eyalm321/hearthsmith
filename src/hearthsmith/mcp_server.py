"""hearthsmith-mcp: the store as an MCP server. Any harness (Claude Code, dsh, Codex, OpenClaw) is a
client. stdio transport; register with e.g. `claude mcp add hearthsmith -- hearthsmith-mcp`."""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from hearthsmith import config
from hearthsmith.store import Store
from hearthsmith.when import from_iso

mcp = MCPServer("hearthsmith", instructions="Task ledger owned by hearthsmith, the nagging blacksmith pet. Prefer "
              "hearthsmith_tasks_list before adding to avoid duplicates.")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(config.load().db_path)
    return _store


def _due(s: str | None) -> int | None:
    return from_iso(s) if s else None


@mcp.tool()
def hearthsmith_tasks_list(state: str = "open", project: str | None = None) -> str:
    """List tasks. state: open | done | blocked | delegated | all. project filters by hyperpanes
    project id/name."""
    ts = store().list(None if state == "all" else state, project)
    return json.dumps([t.as_dict() for t in ts], indent=1)


@mcp.tool()
def hearthsmith_tasks_add(title: str, due: str | None = None, project: str | None = None,
                    tags: str = "", notes: str = "", repeat: str | None = None,
                    parent_id: str | None = None) -> str:
    """Add a task. due is ISO date/datetime (local; a bare date = 18:00 that day). project is a
    hyperpanes project id or name. repeat: "day", "weekday", "mon,thu", "2 weeks", "month" —
    marking it done creates the next occurrence. parent_id makes it a step of that task."""
    from hearthsmith.when import next_due, rule_of
    rule = rule_of(repeat) if repeat else ""
    if repeat and not rule:
        return json.dumps({"error": f"can't read repeat {repeat!r}"})
    d = _due(due) if due else (next_due(rule, None) if rule else None)
    return json.dumps(store().add(title, due=d, project=project, tags=tags, notes=notes,
                                  repeat=rule, parent_id=parent_id).as_dict())


@mcp.tool()
def hearthsmith_tasks_hand(task_id: str) -> str:
    """Hand a task to a Claude agent in a hyperpanes pane (one already on that work in the
    task's project, else a new pane in the project folder). The task goes to 'delegated'; when
    the agent goes quiet, its report is appended to the task's notes and the task reopens for
    the user to check."""
    from hearthsmith.handoff import hand
    t = store().get(task_id)
    if not t:
        return json.dumps({"error": "no such task", "task_id": task_id})
    h = hand(config.load(), store(), t)
    return json.dumps({"ok": h.ok, "text": h.text, "pane_id": h.pane_id, "steps": h.steps})


@mcp.tool()
def hearthsmith_tasks_split(task_id: str) -> str:
    """Break a task into 3-7 steps (the compose model proposes them; they're added as its
    steps). The nags then name the next open step instead of the whole task."""
    from hearthsmith.steps import split
    t = store().get(task_id)
    if not t:
        return json.dumps({"error": "no such task", "task_id": task_id})
    return json.dumps([s.as_dict() for s in split(config.load(), store(), t)], indent=1)


@mcp.tool()
def hearthsmith_tasks_done(task_id: str) -> str:
    """Mark a task done. Accepts an id prefix."""
    t = store().get(task_id)
    if not t:
        return json.dumps({"error": "no such task", "task_id": task_id})
    return json.dumps(store().set_state(t.id, "done").as_dict())


@mcp.tool()
def hearthsmith_tasks_block(task_id: str, reason: str = "") -> str:
    """Mark a task blocked (the pet stops nagging about it)."""
    t = store().get(task_id)
    if not t:
        return json.dumps({"error": "no such task", "task_id": task_id})
    if reason:
        store().db.execute("UPDATE tasks SET notes=notes||? WHERE id=?", (f"\nblocked: {reason}", t.id))
    return json.dumps(store().set_state(t.id, "blocked").as_dict())


@mcp.tool()
def hearthsmith_tasks_snooze(task_id: str, minutes: int = 120) -> str:
    """Snooze nagging for a task."""
    t = store().get(task_id)
    if not t:
        return json.dumps({"error": "no such task", "task_id": task_id})
    return json.dumps(store().snooze(t.id, minutes).as_dict())


@mcp.tool()
def hearthsmith_brief(kind: str | None = None) -> str:
    """Where things stand, from the ledger: kind "morning" (overdue, due today, what landed
    overnight, what's stuck) or "evening" (done today, still open, due tomorrow). Default picks
    by time of day. Returns the spoken text plus the facts it was built from."""
    from hearthsmith import brief
    b = brief.make(config.load(), store(), kind)
    return json.dumps({"text": b["text"], "facts": b["facts"]}, indent=1)


@mcp.tool()
def hearthsmith_nags_recent(n: int = 5) -> str:
    """What the blacksmith said recently, with the decision that drove it."""
    return json.dumps(store().recent_nags(n), indent=1)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
