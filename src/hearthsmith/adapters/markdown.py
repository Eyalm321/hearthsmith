"""Zero-dep importer: `- [ ] title @due(2026-09-20) @every(mon) #tag +project` lines in a markdown file.
One-way: file → store. Ticking a box in the file marks the task done; hearthsmith never writes the file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from hearthsmith.store import Store
from hearthsmith.when import from_iso, next_due, rule_of

LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
DUE = re.compile(r"@due\((\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?)\)")
PROJ = re.compile(r"\+(\S+)")
TAG = re.compile(r"#(\S+)")
EVERY = re.compile(r"@every\(([^)]*)\)")


def parse_line(body: str) -> tuple[str, int | None, str | None, str, str]:
    """`title @due(2026-09-20[T18:00]) @every(mon) +project #tag` → (title, due, project, tags,
    repeat). The ledger's add box takes the same syntax as tasks.md, so there is one way to write
    a task. A repeat with no @due starts at its next occurrence; a bad @every is a ValueError."""
    due = None
    if d := DUE.search(body):
        due = from_iso(d.group(1))
    repeat = ""
    if e := EVERY.search(body):
        if not (repeat := rule_of(e.group(1)) or ""):
            raise ValueError(f"@every({e.group(1)})")
        body = EVERY.sub("", body)
        due = due or next_due(repeat, None)
    project = p.group(1) if (p := PROJ.search(body)) else None
    tags = ",".join(TAG.findall(body))
    title = TAG.sub("", PROJ.sub("", DUE.sub("", body))).strip()
    return title, due, project, tags, repeat


def sync(path: Path, store: Store) -> int:
    if not path or not path.exists():
        return 0
    n = 0
    for line in path.read_text().splitlines():
        m = LINE.match(line)
        if not m:
            continue
        checked, body = m.group(1) != " ", m.group(2).strip()
        try:
            title, due, project, tags, repeat = parse_line(body)
        except ValueError:
            continue            # a date or repeat it can't read: leave the line alone
        sid = hashlib.sha1(title.lower().encode()).hexdigest()[:12]
        t = store.upsert_external("markdown", sid, title, due=due, project=project, repeat=repeat)
        if tags and t.tags != tags:
            store.db.execute("UPDATE tasks SET tags=? WHERE id=?", (tags, t.id))
        if checked and t.state == "open":
            store.set_state(t.id, "done")
        n += 1
    return n
