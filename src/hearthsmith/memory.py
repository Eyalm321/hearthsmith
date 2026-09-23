"""What he knows about you: things you told him, and things he noticed.

Told ("remember I don't want nags before 10", "don't nag me about email", "the site launch is
what matters this week", "I work best in the evenings"): kept as you said them. The ones with an
obvious meaning also become a rule he obeys, not just a line he reads:

    quiet_end / quiet_start   no nags before / after that hour (tightens nag.quiet_hours)
    weekends                  "off": nothing on Saturday or Sunday
    mute                      tasks whose project or title matches aren't nagged about
    focus                     that project's tasks come first, until the focus expires

Everything, rule or not, goes into the paragraph the decider and the compose model read.

Noticed (`noticed`, from the store, no model): the hours you actually finish things, and tasks
nagged about again and again without moving — for those he's told to suggest splitting or
handing off rather than repeating himself.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta

from hearthsmith.store import Store, Task
from hearthsmith.when import _clock

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
  id         TEXT PRIMARY KEY,
  text       TEXT NOT NULL,
  rule       TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  expires_at INTEGER
);
"""

_T = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
_WHO = (r"(?:(?:don'?t|do\s+not|never|no|stop)\s+(?:you\s+)?(?:nag(?:ging)?|bother(?:ing)?|ping(?:ing)?"
        r"|remind(?:ing)?|talk(?:ing)?\s+to)\s+me"
        # "I don't want nags before 10", "no pings after 9pm"
        r"|(?:i\s+(?:don'?t|do\s+not)\s+want|no)\s+(?:any\s+)?(?:nags?|nagging|pings?|reminders?|"
        r"interruptions?|noise)(?:\s+from\s+you)?)")
QUIET_END = re.compile(rf"{_WHO}\s+(?:before|until|till|til)\s+{_T}", re.IGNORECASE)
QUIET_START = re.compile(rf"{_WHO}\s+after\s+{_T}", re.IGNORECASE)
WEEKENDS = re.compile(rf"{_WHO}\s+(?:on\s+)?(?:the\s+)?weekends?|weekends?\s+(?:are|is)\s+(?:off|mine|free)",
                      re.IGNORECASE)
MUTE = re.compile(rf"{_WHO}\s+about\s+(?:the\s+|my\s+)?(.+?)(?:\s+(?:tasks?|stuff|things))?[.!]*$",
                  re.IGNORECASE)
FOCUS = re.compile(r"(?:focus(?:ing)?\s+(?:is\s+)?on|prioriti[sz]e|priority\s+is)\s+(?:the\s+|my\s+)?(.+?)"
                   r"(?:\s+(today|this\s+week|next\s+week|until\s+\w+|for\s+now))?[.!]*$"
                   r"|(?:the\s+|my\s+)?(.+?)\s+(?:is\s+what\s+matters|matters\s+most|is\s+the\s+priority|comes\s+first)"
                   r"(?:\s+(today|this\s+week|next\s+week|until\s+\w+|for\s+now))?[.!]*$", re.IGNORECASE)
# "remember that …", "note: …" — the fact without the asking
LEAD = re.compile(r"^\s*(?:hey\s+|oi\s+)?(?:smith[,\s]+)?(?:please\s+)?(?:remember|note|keep\s+in\s+mind|"
                  r"know|fyi|for\s+the\s+record)(?:\s+that)?[:,\s]+", re.IGNORECASE)
# Is this about the user rather than a job to do? "remember that I…", "I prefer…", or a rule above
ABOUT_ME = re.compile(r"^\s*(?:(?:remember|note|keep\s+in\s+mind|fyi)(?:\s+that)?[:,\s]+)?"
                      r"(?:i\s+(?:prefer|like|love|hate|dislike|don'?t\s+like|work|usually|always|never|"
                      r"tend|am\s+(?:a|an|most|more|usually|never|always)|'?m\s+(?:a|an|most|more)|"
                      r"get\s+more\s+done|focus\s+better)|my\s+(?:mornings?|evenings?|weekends?|"
                      r"focus|priority|priorities|schedule|hours|week)\b)", re.IGNORECASE)
FORGET = re.compile(r"^\s*(?:please\s+)?(?:forget|drop|stop\s+remembering|never\s+mind)\s+(?:that\s+|about\s+)?(.+)$",
                    re.IGNORECASE)
RECALL = re.compile(r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\s+me\b|\bwhat\s+have\s+you\s+noticed\b",
                    re.IGNORECASE)


def _hour(h: str, m: str | None, ampm: str | None) -> int | None:
    c = _clock(h, m, ampm.replace(".", "") if ampm else None)
    if c is None:
        return None
    return c[0] + (1 if c[1] else 0)       # "before 9:30" is quiet through 9:59 — round up


def _until(word: str | None, now: datetime) -> int | None:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not word or word.lower() == "for now":
        return int((today + timedelta(days=7)).timestamp())
    w = word.lower()
    if w == "today":
        return int((today + timedelta(days=1)).timestamp())
    if w.startswith("next"):      # said at a Friday review: from now through next Sunday
        return int((today + timedelta(days=14 - today.weekday())).timestamp())
    if w.startswith("this"):
        return int((today + timedelta(days=7 - today.weekday())).timestamp())    # through Sunday
    from hearthsmith.when import parse
    _, due = parse(w, now)
    return int((datetime.fromtimestamp(due) + timedelta(days=1)).replace(hour=0, minute=0).timestamp()) \
        if due else int((today + timedelta(days=7)).timestamp())


def rule_for(text: str, now: datetime | None = None) -> tuple[dict, int | None]:
    """(rule, expires_at) read from how it was said; ({}, None) when it's only a fact."""
    now = now or datetime.now()
    if (m := QUIET_END.search(text)) and (h := _hour(m[1], m[2], m[3])) is not None:
        return {"quiet_end": h}, None
    if (m := QUIET_START.search(text)) and (h := _hour(m[1], m[2], m[3])) is not None:
        return {"quiet_start": h}, None
    if WEEKENDS.search(text):
        return {"weekends": "off"}, None
    if m := MUTE.search(text):
        return {"mute": m[1].strip(" .!'\"")}, None
    if m := FOCUS.search(text):
        what, when_ = (m[1], m[2]) if m[1] else (m[3], m[4])
        return {"focus": what.strip(" .!'\"")}, _until(when_, now)
    return {}, None


def confirm(row: dict) -> str:
    """How he says back what he'll now do differently."""
    r = json.loads(row["rule"])
    if "quiet_end" in r:
        return f"No nags before {r['quiet_end']:02d}:00, then."
    if "quiet_start" in r:
        return f"I'll keep quiet after {r['quiet_start']:02d}:00."
    if r.get("weekends") == "off":
        return "Weekends are yours. Not a word from me."
    if "mute" in r:
        return f"I'll leave {r['mute']} alone. It stays on the ledger."
    if "focus" in r:
        until = datetime.fromtimestamp(row["expires_at"]) if row["expires_at"] else None
        last = until - timedelta(seconds=1) if until else None
        # "through Sunday" is ambiguous once the Sunday meant is next week's
        fmt = "%A" if last and last - datetime.now() < timedelta(days=6) else "%A %d %b"
        return f"{r['focus']} first" + (f", through {last:{fmt}}." if last else ".")
    return f"Noted: {row['text']}."


def is_memory(text: str) -> bool:
    """Said about the user (a preference, a rule, a focus) rather than a task to remember."""
    if re.match(r"^\s*(?:please\s+)?remind\s+me\b", text, re.IGNORECASE):
        return False
    return bool(ABOUT_ME.search(text)) or bool(rule_for(text)[0])


class Memory:
    def __init__(self, store: Store):
        self.store = store
        store.db.executescript(SCHEMA)

    def add(self, text: str, now: datetime | None = None) -> dict:
        now = now or datetime.now()
        fact = LEAD.sub("", text).strip().rstrip(".")
        rule, expires = rule_for(fact, now)
        # a new quiet hour / focus replaces the old one of the same kind, not stacks on it
        for old in self.items():
            if rule and set(json.loads(old["rule"])) & set(rule) and "mute" not in rule:
                self.forget(old["id"])
        row = {"id": uuid.uuid4().hex[:8], "text": fact, "rule": json.dumps(rule),
               "created_at": int(now.timestamp()), "expires_at": expires}
        self.store.db.execute("INSERT INTO memory (id,text,rule,created_at,expires_at) "
                              "VALUES (:id,:text,:rule,:created_at,:expires_at)", row)
        return row

    def items(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [dict(r) for r in self.store.db.execute(
            "SELECT * FROM memory WHERE expires_at IS NULL OR expires_at > ? ORDER BY created_at",
            (now,))]

    def forget(self, id_or_words: str) -> list[dict]:
        """By id, or the item(s) sharing most words with what was said."""
        items = self.items()
        hit = [m for m in items if m["id"].startswith(id_or_words.strip())]
        if not hit:
            want = _words(id_or_words)
            scored = sorted(((len(want & _words(m["text"])), m) for m in items), key=lambda x: -x[0])
            if scored and scored[0][0] and scored[0][0] * 2 >= min(len(want), len(_words(scored[0][1]["text"]))):
                hit = [m for s, m in scored if s == scored[0][0]]
        for m in hit:
            self.store.db.execute("DELETE FROM memory WHERE id=?", (m["id"],))
        return hit

    # -- rules ---------------------------------------------------------------------------------

    def rules(self) -> dict:
        out: dict = {"mute": []}
        for m in self.items():
            r = json.loads(m["rule"])
            for k, v in r.items():
                if k == "mute":
                    out["mute"].append(v)
                else:
                    out[k] = v              # newest wins
        return out

    def quiet_hours(self, base: tuple[int, int]) -> tuple[int, int]:
        r = self.rules()
        return (r.get("quiet_start", base[0]), r.get("quiet_end", base[1]))

    def weekend_off(self, now: datetime | None = None) -> bool:
        return self.rules().get("weekends") == "off" and (now or datetime.now()).weekday() >= 5

    def muted(self, t: Task) -> bool:
        hay = f"{t.title} {t.project or ''} {t.tags}".lower()
        return any(_words(m) <= _words(hay) or m.lower() == (t.project or "").lower()
                   for m in self.rules()["mute"])

    def focused(self, t: Task) -> bool:
        f = self.rules().get("focus")
        if not f:
            return False
        want = _words(f)
        return bool(want) and (f.lower() == (t.project or "").lower()
                               or len(want & _words(f"{t.title} {t.tags}")) * 2 >= len(want))

    def shape(self, tasks: list[Task]) -> list[Task]:
        """What the nag loop may raise: muted out, focus first (stable, so due order holds)."""
        keep = [t for t in tasks if not self.muted(t)]
        return sorted(keep, key=lambda t: not self.focused(t))

    # -- for the models ------------------------------------------------------------------------

    def paragraph(self, now: datetime | None = None) -> str:
        told = [m["text"] + (f" (through {datetime.fromtimestamp(m['expires_at'] - 1):%a %d %b})"
                             if m["expires_at"] else "") for m in self.items()]
        seen = noticed(self.store, now)
        out = []
        if told:
            out.append("What the user has told you about themselves:\n" + "\n".join(f"  - {x}" for x in told))
        if seen:
            out.append("What you've noticed:\n" + "\n".join(f"  - {x}" for x in seen))
        return "\n".join(out)


def _words(s: str) -> set[str]:
    stop = {"the", "a", "an", "my", "i", "me", "to", "of", "about", "that", "on", "in", "and", "is"}
    return {w[:5] for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in stop}


def noticed(store: Store, now: datetime | None = None) -> list[str]:
    """Habits from the last 30 days of the store. Silent until there's enough to say."""
    now = now or datetime.now()
    since = int((now - timedelta(days=30)).timestamp())
    out = []
    done = [t for t in store.list("done") if t.updated_at >= since]
    if len(done) >= 8:
        # 3-hour windows by when things got finished
        windows = Counter(datetime.fromtimestamp(t.updated_at).hour // 3 for t in done)
        top = [w for w, n in windows.most_common(2) if n >= max(3, len(done) // 5)]
        if top:
            spans = " and ".join(f"{w * 3:02d}:00–{w * 3 + 3:02d}:00" for w in sorted(top))
            out.append(f"Gets things done mostly around {spans} ({len(done)} tasks in 30 days); "
                       "a nag lands better then.")
    from hearthsmith.offer import declined
    for t in store.list("open"):
        if t.nag_count >= 4 and not store.children(t.id) and not declined(store, t):
            out.append(f"'{t.title}' has been nagged {t.nag_count} times without moving — suggest "
                       "splitting it into steps or handing it to an agent instead of nagging again.")
    return out[:6]
