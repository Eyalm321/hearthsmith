"""Zero-dep importer: `- [ ] title @due(2026-09-20) #tag +project` lines in a markdown file.
One-way: file → store. Ticking a box in the file marks the task done; forge never writes the file.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from forge.store import Store

LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
DUE = re.compile(r"@due\((\d{4}-\d{2}-\d{2})\)")
PROJ = re.compile(r"\+(\S+)")
TAG = re.compile(r"#(\S+)")


def sync(path: Path, store: Store) -> int:
    if not path or not path.exists():
        return 0
    n = 0
    for line in path.read_text().splitlines():
        m = LINE.match(line)
        if not m:
            continue
        checked, body = m.group(1) != " ", m.group(2).strip()
        due = None
        if d := DUE.search(body):
            due = int(datetime.fromisoformat(d.group(1)).timestamp())
        project = PROJ.search(body).group(1) if PROJ.search(body) else None
        tags = ",".join(TAG.findall(body))
        title = TAG.sub("", PROJ.sub("", DUE.sub("", body))).strip()
        sid = hashlib.sha1(title.lower().encode()).hexdigest()[:12]
        t = store.upsert_external("markdown", sid, title, due=due, project=project)
        if tags and t.tags != tags:
            store.db.execute("UPDATE tasks SET tags=? WHERE id=?", (tags, t.id))
        if checked and t.state == "open":
            store.set_state(t.id, "done")
        n += 1
    return n
