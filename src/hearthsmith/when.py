"""When: pull the date out of how people say a reminder. No model — "call the vet friday 5pm"
has one right reading, and a decider bucketing it into "within this week" loses the Friday.

    parse("remind me to call the vet friday at 5pm")  →  ("call the vet", <Fri 17:00>)

Understands today / tonight / tomorrow, weekdays (this/next), "in 3 hours", "in 2 weeks",
"next week", "this weekend", "end of day", dates ("sep 25", "25/9", "2026-09-25", "the 25th"),
and times ("5pm", "17:30", "at 5", "noon", "morning" / "afternoon" / "evening"). A day with no
time means that day at 18:00: due by the end of it, and still due — not overdue — all day.
What it matched is cut out of the title, along with "remind me to" and its relatives.

Repeats too ("every monday at 9", "daily", "weekdays", "every 2 weeks", "monthly"): `parse_full`
returns the rule, `next_due` walks it forward when an occurrence is struck off. Rules are short
strings — `1d` `2w` `1m` `1y` `weekday` `mon,thu` — and the same ones `@every(...)` takes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

DAY_END = 18            # a day named without a time is due by this hour
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
PARTS = {"morning": 9, "noon": 12, "midday": 12, "lunch": 12, "lunchtime": 12, "afternoon": 14,
         "evening": 18, "tonight": 20, "night": 20, "eod": DAY_END, "end of day": DAY_END,
         "end of the day": DAY_END, "close of business": 17, "cob": 17}

_WD = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
# "sun cream", "I sat down": a short name is only a day when something points at it
_WD_SHORT = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\.?"
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_ORD = r"(\d{1,2})(?:st|nd|rd|th)?"
_PART = "|".join(sorted((re.escape(p) for p in PARTS), key=len, reverse=True))

# "remind me to", "don't let me forget to", "i need to", "add a task to" — the request, not the task
LEAD = re.compile(
    r"^\s*(?:hey\s+|oi\s+|ok\s+)?(?:smith[,\s]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?"
    r"(?:remind\s+me\s+(?:to\s+|about\s+|that\s+(?:i\s+(?:need|have)\s+to\s+)?)?"
    r"|(?:don'?t|do\s+not)\s+let\s+me\s+forget\s+(?:to\s+|about\s+)?"
    r"|make\s+sure\s+i\s+"
    r"|(?:add|put|make)\s+(?:a\s+)?(?:task|reminder|todo|to-?do)\s+(?:to\s+|for\s+|:\s*)?"
    r"|(?:add|put)\s+(?:it\s+)?(?:on|to)\s+(?:the|my)\s+(?:ledger|list)\s*(?:to\s+|:\s*)?"
    r"|(?:note|remember)(?:\s+that)?[:\s]+(?:to\s+)?"
    r"|i\s+(?:need|have|must|gotta|should)\s+(?:to\s+)?)",
    re.IGNORECASE)
# Unmistakably "put this on the ledger" — whatever else the sentence mentions, it's for later
REMINDER = re.compile(r"^\s*(?:hey\s+|oi\s+|ok\s+)?(?:smith[,\s]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
                      r"(?:please\s+)?(?:remind\s+me\b|(?:don'?t|do\s+not)\s+let\s+me\s+forget\b"
                      r"|(?:add|put|make)\s+(?:a\s+)?(?:task|reminder|todo|to-?do)\b"
                      r"|(?:add|put)\s+(?:it\s+)?(?:on|to)\s+(?:the|my)\s+(?:ledger|list)\b)",
                      re.IGNORECASE)
TRAIL = re.compile(r"[\s,;:.!?-]*(?:\b(?:on|at|by|in|due|before|for|until|til+|from)\b[\s,.]*)*$",
                   re.IGNORECASE)


def from_iso(s: str) -> int:
    """`2026-09-25` or `2026-09-25T18:30` → epoch. A bare date is due by DAY_END that day, the
    same as "on the 25th" said out loud; midnight would make it overdue the whole day."""
    d = datetime.fromisoformat(s.strip())
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.strip()):
        d = d.replace(hour=DAY_END)
    return int(d.timestamp())


# -- repeats ---------------------------------------------------------------------------------

_WDS = rf"{_WD}s?|(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)"
_WD_LIST = rf"(?:{_WDS})(?:\s*(?:,|and|&|\+)\s*(?:{_WDS}))*"
RULE = re.compile(r"^(?:\d+[dwmy]|weekday|(?:mon|tue|wed|thu|fri|sat|sun)(?:,(?:mon|tue|wed|thu|fri|sat|sun))*)$")
UNITS = {"day": "d", "night": "d", "week": "w", "fortnight": "w", "month": "m", "year": "y"}


def _find_repeat(s: str) -> tuple[str, tuple[int, int]] | None:
    """The first repeat phrase in `s`: (rule, span)."""
    if m := re.search(r"\b(?:every|each)\s+(?:(other)\s+|(\d+)\s+)?(day|night|week|fortnight|month|year)s?\b",
                      s, re.IGNORECASE):
        n = 2 if m[1] else int(m[2] or 1)
        unit = m[3].lower()
        return f"{n * (2 if unit == 'fortnight' else 1)}{UNITS[unit]}", m.span()
    if m := re.search(r"\b(?:every|each|on)\s+(?:week|work)\s*days?\b|\bweekdays\b", s, re.IGNORECASE):
        return "weekday", m.span()
    if m := re.search(rf"\b(?:every|each)\s+({_WD_LIST})(?![a-z])", s, re.IGNORECASE) \
            or re.search(rf"\b(?:on\s+)?((?:{_WD})s(?:\s*(?:,|and|&)\s*(?:{_WD})s)*)\b", s, re.IGNORECASE):
        days = sorted({_weekday(w) for w in re.findall(r"[a-z]+", m[1].lower())
                       if w not in ("and",)}, key=int)
        return ",".join(WEEKDAYS[d][:3] for d in days), m.span()
    if m := re.search(r"\b(daily|nightly|weekly|fortnightly|biweekly|monthly|yearly|annually)\b",
                      s, re.IGNORECASE):
        return {"daily": "1d", "nightly": "1d", "weekly": "1w", "fortnightly": "2w",
                "biweekly": "2w", "monthly": "1m", "yearly": "1y", "annually": "1y"}[m[1].lower()], m.span()
    # "every morning" / "every evening": a daily repeat; the part of the day is left for the clock
    if m := re.search(rf"\b(?:every|each)\s+(?=(?:{_PART})\b)", s, re.IGNORECASE):
        return "1d", m.span()
    return None


def rule_of(text: str) -> str | None:
    """`@every(...)`'s argument, or any spoken repeat, as a rule; None when it isn't one."""
    t = text.strip().lower()
    if RULE.match(t):
        return t
    found = _find_repeat(t if t.startswith(("every", "each")) else f"every {t}")
    return found[0] if found else None


def _add_months(d: datetime, n: int, day: int) -> datetime:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = ((datetime(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)).day
    return d.replace(year=y, month=m, day=min(day, last))


def next_due(rule: str, prev: int | None, now: datetime | None = None,
             clock: tuple[int, int] | None = None) -> int:
    """The next occurrence after both the previous one and now — done three days late, the
    next one is still ahead of you, not a backlog of three."""
    now = now or datetime.now()
    if prev is not None:
        p = datetime.fromtimestamp(prev)
        clock = (p.hour, p.minute)
    h, mi = clock or (DAY_END, 0)
    base = max(datetime.fromtimestamp(prev), now) if prev is not None else now
    if rule == "weekday" or not rule[0].isdigit():
        days = set(range(5)) if rule == "weekday" else {_weekday(w) for w in rule.split(",")}
        d = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        for _ in range(15):
            if d > base and d.weekday() in days:
                return int(d.timestamp())
            d += timedelta(days=1)
        raise ValueError(rule)
    n, unit = int(rule[:-1]), rule[-1]
    anchor = (datetime.fromtimestamp(prev) if prev is not None
              else now.replace(hour=h, minute=mi, second=0, microsecond=0))
    if prev is None and anchor > now:
        return int(anchor.timestamp())          # first occurrence: later today still counts
    d, k = anchor, 0
    while d <= base:
        k += 1
        d = (anchor + timedelta(days=n * k * (7 if unit == "w" else 1)) if unit in "dw"
             else _add_months(anchor, n * k * (12 if unit == "y" else 1), anchor.day))
    return int(d.timestamp())


def describe_rule(rule: str) -> str:
    if rule == "weekday":
        return "weekdays"
    if not rule[0].isdigit():
        return "every " + ", ".join(w.capitalize() for w in rule.split(","))
    n, unit = int(rule[:-1]), {"d": "day", "w": "week", "m": "month", "y": "year"}[rule[-1]]
    return f"every {unit}" if n == 1 else f"every {n} {unit}s"


def _weekday(name: str) -> int:
    return next(i for i, w in enumerate(WEEKDAYS) if w.startswith(name[:3].lower()))


def _clock(h: str, m: str | None, ampm: str | None) -> tuple[int, int] | None:
    hour, minute = int(h), int(m or 0)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm.lower().startswith("p") else 0)
    elif not m and 1 <= hour <= 7:
        hour += 12      # "at 5" is five in the afternoon, not before dawn
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse(text: str, now: datetime | None = None) -> tuple[str, int | None]:
    """(title, due epoch) — due None when the text names no time at all."""
    title, due, _ = parse_full(text, now)
    return title, due


def parse_full(text: str, now: datetime | None = None) -> tuple[str, int | None, str]:
    """(title, due epoch, repeat rule or "")."""
    now = now or datetime.now()
    s = text
    repeat = ""
    cut_repeat: list[tuple[int, int]] = []
    if found := _find_repeat(s):
        repeat, (a, b) = found
        cut_repeat.append((a, b))
        s = s[:a] + " " * (b - a) + s[b:]    # blanked, not removed: spans below stay aligned
    day: datetime | None = None          # date part, midnight
    clock: tuple[int, int] | None = None
    delta: timedelta | None = None
    cut: list[tuple[int, int]] = list(cut_repeat)

    def take(rx: str) -> re.Match | None:
        m = re.search(rx, s, re.IGNORECASE)
        if m:
            cut.append(m.span())
        return m

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # relative offsets: "in 20 minutes", "in 3 hours", "in 2 days", "in a week"
    if m := take(r"\bin\s+(an?|\d+(?:\.\d+)?|a\s+couple(?:\s+of)?|a\s+few)\s+"
                 r"(min(?:ute)?s?|h(?:ou)?rs?|days?|weeks?|months?)\b"):
        n_raw = m.group(1).lower()
        n = (1.0 if n_raw in ("a", "an") else 2.0 if "couple" in n_raw else 3.0 if "few" in n_raw
             else float(n_raw))
        unit = m.group(2).lower()
        if unit.startswith("min"):
            delta = timedelta(minutes=n)
        elif unit.startswith("h"):
            delta = timedelta(hours=n)
        else:
            days = n * (7 if unit.startswith("w") else 30 if unit.startswith("mo") else 1)
            day = today + timedelta(days=round(days))

    # absolute dates
    if day is None and delta is None:
        if m := take(r"\b(\d{4})-(\d{2})-(\d{2})\b"):
            day = datetime(int(m[1]), int(m[2]), int(m[3]))
        elif m := take(rf"\b(?:on\s+)?(?:the\s+)?{_ORD}\s+(?:of\s+)?({_MON})(?:\s+(\d{{4}}))?"):
            day = _date(int(m[1]), m[2], m[3], today)
        elif m := take(rf"\b(?:on\s+)?({_MON})\s+(?:the\s+)?{_ORD}\b(?:,?\s+(\d{{4}}))?"):
            day = _date(int(m[2]), m[1], m[3], today)
        elif m := take(r"\b(?:on\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"):
            d, mo = int(m[1]), int(m[2])          # day/month: this box is not in the US
            yr = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else None
            if 1 <= mo <= 12 and 1 <= d <= 31:
                day = _date(d, MONTHS[mo - 1], str(yr) if yr else None, today)
        elif m := take(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b"):
            d = int(m[1])
            month = today if d >= today.day else (today.replace(day=1) + timedelta(days=32))
            try:
                day = month.replace(day=d)
            except ValueError:
                day = None

    # named days
    if day is None and delta is None:
        if take(r"\b(?:today|tonight|this\s+(?:morning|afternoon|evening))\b"):
            day = today       # the part of the day, if any, is read with the time below
        elif take(r"\b(?:the\s+)?day\s+after\s+tomorrow\b"):
            day = today + timedelta(days=2)
        elif take(r"\b(?:tomorrow|tmrw?|tmw)\b"):
            day = today + timedelta(days=1)
        elif take(r"\b(?:this\s+)?weekend\b"):
            day = today + timedelta(days=(5 - today.weekday()) % 7)
        elif take(r"\bnext\s+week\b"):
            day = today + timedelta(days=7 - today.weekday())
        elif take(r"\b(?:end\s+of\s+(?:the\s+)?week|eow)\b"):
            day = today + timedelta(days=(4 - today.weekday()) % 7)
        elif m := (take(rf"\b(?:(this|next|coming)\s+)?({_WD})\b")
                   or take(rf"\b(?:on|by|(this|next|coming))\s+({_WD_SHORT})(?![a-z])")):
            ahead = (_weekday(m[2]) - today.weekday()) % 7
            if m[1] and m[1].lower() == "next" and ahead == 0:
                ahead = 7
            elif ahead == 0:
                ahead = 7                     # "friday" said on a Friday is the next one
            day = today + timedelta(days=ahead)

    # time of day: a clock time beats a part of the day ("tonight at 9pm" is 21:00)
    if delta is None:
        if m := take(r"\b(?:at\s+|@\s*|by\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)(?![a-z])"):
            clock = _clock(m[1], m[2], m[3].replace(".", ""))
        elif m := take(r"\b(?:at\s+|@\s*|by\s+)?([01]?\d|2[0-3]):([0-5]\d)\b"):
            clock = _clock(m[1], m[2], None)
        elif m := take(r"\b(?:at|by)\s+(\d{1,2})\b(?!\s*(?:min|h|day|week|%|/|\.\d))"):
            clock = _clock(m[1], None, None)
        if m := take(rf"\b(?:(?:in\s+the|at|by|this|around)\s+)?({_PART})\b"):
            clock = clock or (PARTS[m[1].lower()], 0)

    title = _title(text, cut)
    if repeat and delta is None and day is None:
        return title, next_due(repeat, None, now, clock), repeat
    if delta is not None:
        return title, int((now + delta).timestamp()), repeat
    if day is None and clock is None:
        return title, None, repeat
    if day is None:
        day = today
        if clock and (clock[0], clock[1]) <= (now.hour, now.minute):
            day += timedelta(days=1)         # "at 9am" said at noon is tomorrow's 9am
    h, mi = clock or (DAY_END, 0)
    return title, int(day.replace(hour=h, minute=mi).timestamp()), repeat


def _date(d: int, mon: str, year: str | None, today: datetime) -> datetime | None:
    mo = MONTHS.index(mon[:3].lower()) + 1
    try:
        out = datetime(int(year) if year else today.year, mo, d)
    except ValueError:
        return None
    if not year and out < today:
        out = out.replace(year=out.year + 1)   # "jan 5" in September is next January
    return out


def _title(text: str, cut: list[tuple[int, int]]) -> str:
    s = text
    spans: list[list[int]] = []
    for a, b in sorted(cut):          # "this morning" is cut as a day and again as a time
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    for a, b in reversed(spans):
        # a preposition glued in front of the phrase ("call him on friday") goes with it
        pre = re.search(r"\b(?:on|at|by|due|before|until|til+|for|this|next)\s+$", s[:a], re.IGNORECASE)
        s = s[:pre.start() if pre else a] + " " + s[b:]
    s = LEAD.sub("", s)
    s = TRAIL.sub("", " ".join(s.split()))
    s = s.strip(" ,;:.-")
    return s or text.strip()


def describe(due: int | None, now: datetime | None = None) -> str:
    """How he says it back: 'Fri 17:00', 'tomorrow 09:00', 'today 18:00'."""
    if due is None:
        return "no deadline"
    now = now or datetime.now()
    d = datetime.fromtimestamp(due)
    delta_days = (d.date() - now.date()).days
    day = ("today" if delta_days == 0 else "tomorrow" if delta_days == 1
           else f"{d:%A}" if 1 < delta_days < 7 else f"{d:%a %d %b}")
    return f"{day} {d:%H:%M}"
