"""Zero-dep importer: `- [ ] title @due(2026-09-20) #tag +project` lines in a markdown file.
One-way: file → store. Ticking a box in the file marks the task done; hearthsmith never writes the file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from hearthsmith.store import Store
from hearthsmith.when import from_iso

LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
DUE = re.compile(r"@due\((\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?)\)")
PROJ = re.compile(r"\+(\S+)")
TAG = re.compile(r"#(\S+)")


def parse_line(body: str) -> tuple[str, int | None, str | None, str]:
    """`title @due(2026-09-20[T18:00]) +project #tag` → (title, due, project, tags). The ledger's
    add box takes the same syntax as tasks.md, so there is one way to write a task."""
    due = None
    if d := DUE.search(body):
        due = from_iso(d.group(1))
    project = p.group(1) if (p := PROJ.search(body)) else None
    tags = ",".join(TAG.findall(body))
    title = TAG.sub("", PROJ.sub("", DUE.sub("", body))).strip()
    return title, due, project, tags


def sync(path: Path, store: Store) -> int:
    if not path or not path.exists():
        return 0
    n = 0
    for line in path.read_text().splitlines():
        m = LINE.match(line)
        if not m:
            continue
        checked, body = m.group(1) != " ", m.group(2).strip()
        title, due, project, tags = parse_line(body)
        sid = hashlib.sha1(title.lower().encode()).hexdigest()[:12]
        t = store.upsert_external("markdown", sid, title, due=due, project=project)
        if tags and t.tags != tags:
            store.db.execute("UPDATE tasks SET tags=? WHERE id=?", (tags, t.id))
        if checked and t.state == "open":
            store.set_state(t.id, "done")
        n += 1
    return n
