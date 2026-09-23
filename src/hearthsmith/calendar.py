"""Your calendar, read from iCal feeds: Google's "secret address in iCal format", Outlook's
published ICS link, iCloud's public calendar link, Proton's share link — or a local .ics file.

Feeds come from `calendar.feeds` in config; an entry starting with `$` names an environment
variable (the env file is read at load), which is where a secret URL belongs. The default is
`$HEARTHSMITH_CALENDAR`, space-separated URLs or paths. No feeds, no calendar — nothing else
changes.

Each feed is fetched at most every `refresh_min` and cached on disk; a failed fetch uses the
last copy, so a flaky network doesn't make meetings vanish. Repeating events, exceptions and
timezones are expanded by recurring-ical-events.

What he does with it:
    brief       today's meetings in the morning, tomorrow's in the evening
    heartbeat   the next event in what the decider reads; no nags during a meeting; a
                heads-up `heads_up_min` before each one (once per event)
    ask         "what's on my calendar", "when's my next meeting"
The heartbeat also writes `calendar.json` (the next two days) for the ledger to show.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from hearthsmith.config import STATE_DIR, CalendarCfg

log = logging.getLogger("hearthsmith.calendar")
CACHE = STATE_DIR / "calendar"
SNAPSHOT = STATE_DIR / "calendar.json"


@dataclass
class Event:
    title: str
    start: int                  # epoch; all-day events start at local midnight
    end: int
    all_day: bool = False
    location: str = ""
    uid: str = ""

    @property
    def key(self) -> str:
        """One occurrence of one event — a repeating meeting has a new key each time."""
        return f"{self.uid or self.title}@{self.start}"

    def when(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        d = datetime.fromtimestamp(self.start)
        day = ("today" if d.date() == now.date() else "tomorrow"
               if d.date() == now.date() + timedelta(days=1) else f"{d:%a %d %b}")
        return f"{day} (all day)" if self.all_day else f"{day} {d:%H:%M}"


def feeds(cfg: CalendarCfg) -> list[str]:
    out = []
    for f in cfg.feeds:
        vals = os.environ.get(f[1:], "") if f.startswith("$") else f
        out += vals.split()
    return out


def _fetch(src: str, cfg: CalendarCfg) -> bytes | None:
    """The feed's bytes: a local file directly, a URL through the on-disk cache."""
    if not re.match(r"^(?:https?|webcal)://", src, re.IGNORECASE):
        try:
            return Path(src).expanduser().read_bytes()
        except OSError:
            log.warning("calendar file unreadable: %s", src)
            return None
    import httpx
    url = re.sub(r"^webcal://", "https://", src, flags=re.IGNORECASE)
    cached = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".ics")
    try:
        if time.time() - cached.stat().st_mtime < cfg.refresh_min * 60:
            return cached.read_bytes()
    except OSError:
        pass
    try:
        r = httpx.get(url, timeout=20.0, follow_redirects=True)
        r.raise_for_status()
        if b"BEGIN:VCALENDAR" not in r.content[:2000]:
            raise ValueError("not an iCal feed")
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
        return r.content
    except (httpx.HTTPError, ValueError) as e:
        # never log the URL: a secret iCal address is a password to your calendar
        log.warning("calendar fetch failed (%s); using the last copy", type(e).__name__)
        try:
            return cached.read_bytes()
        except OSError:
            return None


def _epoch(v) -> tuple[int, bool]:
    if isinstance(v, datetime):
        return int(v.timestamp()), False          # naive = floating = local; aware converts
    return int(datetime.combine(v, datetime.min.time()).timestamp()), True


def parse(data: bytes, start: datetime, end: datetime) -> list[Event]:
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(data)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT" and "DTEND" not in ev and "DURATION" not in ev:
            continue
        s, all_day = _epoch(ev["DTSTART"].dt)
        if "DTEND" in ev:
            e, _ = _epoch(ev["DTEND"].dt)
        elif "DURATION" in ev:
            e = s + int(ev["DURATION"].dt.total_seconds())
        else:
            e = s + (86400 if all_day else 0)
        out.append(Event(title=str(ev.get("SUMMARY", "(busy)")).strip() or "(busy)", start=s, end=e,
                         all_day=all_day, location=str(ev.get("LOCATION", "")).strip(),
                         uid=str(ev.get("UID", ""))))
    return out


def events(cfg: CalendarCfg, start: datetime, end: datetime) -> list[Event]:
    """Every event overlapping [start, end) across all feeds, in start order, de-duplicated
    (the same meeting on a shared and a personal calendar shows once)."""
    seen, out = set(), []
    for src in feeds(cfg):
        data = _fetch(src, cfg)
        if not data:
            continue
        try:
            evs = parse(data, start, end)
        except Exception:
            log.exception("calendar feed didn't parse")
            continue
        for e in evs:
            k = (e.title.lower(), e.start)
            if k not in seen:
                seen.add(k)
                out.append(e)
    return sorted(out, key=lambda e: (e.start, not e.all_day))


def day(cfg: CalendarCfg, d: date) -> list[Event]:
    s = datetime.combine(d, datetime.min.time())
    return events(cfg, s, s + timedelta(days=1))


def upcoming(cfg: CalendarCfg, now: datetime | None = None, hours: int = 48) -> list[Event]:
    now = now or datetime.now()
    return [e for e in events(cfg, now, now + timedelta(hours=hours)) if e.end > now.timestamp()]


def current(evs: list[Event], now: float | None = None) -> Event | None:
    """A timed event happening right now (all-day ones don't make you busy)."""
    now = now or time.time()
    return next((e for e in evs if not e.all_day and e.start <= now < e.end), None)


def next_timed(evs: list[Event], now: float | None = None) -> Event | None:
    now = now or time.time()
    return next((e for e in evs if not e.all_day and e.start > now), None)


def line(e: Event, now: datetime | None = None) -> str:
    now = now or datetime.now()
    mins = (e.start - now.timestamp()) / 60
    soon = f" (in {mins:.0f} min)" if 0 < mins < 120 and not e.all_day else ""
    return f"{e.title} — {e.when(now)}{soon}" + (f" @ {e.location}" if e.location else "")


def state_lines(evs: list[Event], now: datetime | None = None) -> list[str]:
    """For the paragraph the decider reads."""
    now = now or datetime.now()
    out = []
    if cur := current(evs, now.timestamp()):
        out.append(f"The user is IN A MEETING right now: {cur.title}, until "
                   f"{datetime.fromtimestamp(cur.end):%H:%M}.")
    if nxt := next_timed(evs, now.timestamp()):
        out.append(f"Next on the calendar: {line(nxt, now)}.")
    return out


# "what's on my calendar", "when's my next meeting", "do I have meetings tomorrow", "am I free"
ASK = re.compile(r"\b(?:calendar|schedule|agenda|meetings?|appointments?|events?)\b.*\?*$"
                 r"|\bam\s+i\s+(?:free|busy)\b", re.IGNORECASE)
ASKING = re.compile(r"^\s*(?:what|when|whens|when'?s|do|does|have|is|am|any|how|where|show|tell|list|"
                    r"read|check|what'?s)\b", re.IGNORECASE)


def wants(text: str) -> bool:
    return bool(ASK.search(text) and ASKING.match(text))


def answer(cfg: CalendarCfg, text: str, now: datetime | None = None) -> str:
    """Read straight from the feeds — no model between you and your calendar."""
    now = now or datetime.now()
    if not feeds(cfg):
        return ("I can't see your calendar. Put its private iCal link in ~/.config/hearthsmith/env "
                "as HEARTHSMITH_CALENDAR=… and I will.")
    low = text.lower()
    if re.search(r"\bnext\b", low):
        nxt = next_timed(upcoming(cfg, now, hours=24 * 14), now.timestamp())
        return f"Next: {line(nxt, now)}." if nxt else "Nothing on the calendar for two weeks."
    d = now.date() + timedelta(days=1) if "tomorrow" in low else now.date()
    evs = [e for e in day(cfg, d) if d > now.date() or e.end > now.timestamp()]
    label = "Tomorrow" if d > now.date() else "Rest of today"
    if not evs:
        return f"{label}: nothing on the calendar."
    if cur := current(evs, now.timestamp()):
        head = f"You're in {cur.title} until {datetime.fromtimestamp(cur.end):%H:%M}. "
        evs = [e for e in evs if e is not cur]
    else:
        head = ""
    return head + (f"{label}: " + "; ".join(
        f"{e.title} {'(all day)' if e.all_day else datetime.fromtimestamp(e.start).strftime('%H:%M')}"
        for e in evs[:8]) + "." if evs else "Nothing after that.")


def write_snapshot(evs: list[Event], path: Path = SNAPSHOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": int(time.time()), "events": [asdict(e) for e in evs]}))


def read_snapshot(path: Path = SNAPSHOT) -> list[Event]:
    try:
        return [Event(**e) for e in json.loads(path.read_text())["events"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []
