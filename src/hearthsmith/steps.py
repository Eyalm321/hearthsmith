"""Steps: a task broken into the pieces you'd actually do. Written by hand (ledger → Add step,
`hearthsmith add --parent`, MCP parent_id) or proposed by the compose model ("break down the
launch", ledger → Split into steps, `hearthsmith split`).

A task with open steps is nagged about by its next step, carrying the parent's due date — "the
launch is overdue" is a guilt trip, "write the changelog (step of the launch)" is something to do.
"""

from __future__ import annotations

import dataclasses
import re

from hearthsmith import config
from hearthsmith.compose import _ollama, _openrouter
from hearthsmith.store import Store, Task

SPLIT = ("Break the user's task into 3 to 7 concrete steps, in the order they'd be done. One step "
         "per line, imperative, under 10 words each, no numbering, no commentary. Only steps the "
         "user themselves would do. Never ask questions: assume the typical situation.")
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|step\s+\d+[:.)]?)\s*", re.IGNORECASE)


def parse_steps(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = BULLET.sub("", line).strip().strip("*_`").rstrip(".").strip()
        # a question back ("what platform is it on?") or a paragraph is not a step
        if 2 < len(line) <= 80 and not line.endswith((":", "?")):
            out.append(line)
    return out[:7]


def split(cfg: config.Config, store: Store, task: Task) -> list[Task]:
    """Ask the compose model for steps and add them under `task`. [] when no model answered —
    nothing is invented without one."""
    # just the task: shown the rest of the ledger, the local model wrote filament steps for a
    # website launch
    msgs = [{"role": "system", "content": SPLIT},
            {"role": "user", "content": f"Task: {task.title}"
                                        + (f"\nNotes: {task.notes.strip()[:600]}" if task.notes.strip() else "")}]
    # the local 9B sometimes asks back or writes prose instead of a list; fewer than two
    # usable steps sends it to the next model
    steps: list[str] = []
    for ask in (lambda: _ollama(cfg.compose, msgs),
                lambda: _openrouter(cfg.compose, msgs, max_tokens=250, reasoning=False)):
        if len(steps := parse_steps(ask() or "")) >= 2:
            break
    else:
        return []
    have = {c.title.lower() for c in store.children(task.id)}
    return [store.add(s, parent_id=task.id) for s in steps if s.lower() not in have]


def actionable(store: Store, tasks: list[Task]) -> list[Task]:
    """What to nag about: a task with open steps is replaced by its first open step, which
    inherits the parent's due date (in memory only) so urgency survives the split."""
    ids = {t.id for t in tasks}
    out = []
    for t in tasks:
        if t.parent_id and t.parent_id in ids:
            continue                            # reached through its parent below
        steps = store.children(t.id, "open")
        if not steps:
            out.append(t)
            continue
        first = steps[0]
        out.append(dataclasses.replace(first, due=first.due or t.due,
                                       title=f"{first.title} (step of '{t.title}')"))
    return out
