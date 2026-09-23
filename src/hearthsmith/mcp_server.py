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
                    tags: str = "", notes: str = "") -> str:
    """Add a task. due is ISO date/datetime (local). project is a hyperpanes project id or name."""
    return json.dumps(store().add(title, due=_due(due), project=project, tags=tags,
                                  notes=notes).as_dict())


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
def hearthsmith_nags_recent(n: int = 5) -> str:
    """What the blacksmith said recently, with the decision that drove it."""
    return json.dumps(store().recent_nags(n), indent=1)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
