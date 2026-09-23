"""When: pull the date out of how people say a reminder. No model — "call the vet friday 5pm"
has one right reading, and a decider bucketing it into "within this week" loses the Friday.

    parse("remind me to call the vet friday at 5pm")  →  ("call the vet", <Fri 17:00>)

Understands today / tonight / tomorrow, weekdays (this/next), "in 3 hours", "in 2 weeks",
"next week", "this weekend", "end of day", dates ("sep 25", "25/9", "2026-09-25", "the 25th"),
and times ("5pm", "17:30", "at 5", "noon", "morning" / "afternoon" / "evening"). A day with no
time means that day at 18:00: due by the end of it, and still due — not overdue — all day.
What it matched is cut out of the title, along with "remind me to" and its relatives.
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
    now = now or datetime.now()
    s = text
    day: datetime | None = None          # date part, midnight
    clock: tuple[int, int] | None = None
    delta: timedelta | None = None
    cut: list[tuple[int, int]] = []

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
    if delta is not None:
        return title, int((now + delta).timestamp())
    if day is None and clock is None:
        return title, None
    if day is None:
        day = today
        if clock and (clock[0], clock[1]) <= (now.hour, now.minute):
            day += timedelta(days=1)         # "at 9am" said at noon is tomorrow's 9am
    h, mi = clock or (DAY_END, 0)
    return title, int(day.replace(hour=h, minute=mi).timestamp())


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
