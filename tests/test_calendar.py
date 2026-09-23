from datetime import UTC, date, datetime

import pytest

from hearthsmith import calendar
from hearthsmith.config import CalendarCfg, Config
from hearthsmith.store import Store

NOW = datetime(2026, 9, 23, 9, 50)          # Wednesday
UTC_18 = datetime(2026, 9, 23, 18, 0, tzinfo=UTC).strftime("%Y%m%dT%H%M%SZ")

FEED = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:standup
SUMMARY:Standup
DTSTART:20260902T100000
DTEND:20260902T101500
RRULE:FREQ=WEEKLY;BYDAY=WE
EXDATE:20260930T100000
END:VEVENT
BEGIN:VEVENT
UID:standup
RECURRENCE-ID:20260923T100000
SUMMARY:Standup (moved)
DTSTART:20260923T103000
DTEND:20260923T104500
END:VEVENT
BEGIN:VEVENT
UID:dentist
SUMMARY:Dentist
LOCATION:Main St 4
DTSTART:{UTC_18}
DURATION:PT1H
END:VEVENT
BEGIN:VEVENT
UID:gone
SUMMARY:Cancelled thing
STATUS:CANCELLED
DTSTART:20260923T160000
DTEND:20260923T170000
END:VEVENT
BEGIN:VEVENT
UID:trip
SUMMARY:Trip
DTSTART;VALUE=DATE:20260924
DTEND;VALUE=DATE:20260926
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "cal.ics"
    f.write_text(FEED.replace("\n", "\r\n"))
    monkeypatch.setenv("HEARTHSMITH_CALENDAR", str(f))
    return CalendarCfg()


def _hm(e):
    return datetime.fromtimestamp(e.start).strftime("%H:%M")


def test_day_expands_repeats_and_exceptions(cfg):
    evs = calendar.day(cfg, date(2026, 9, 23))
    local_18 = datetime.fromtimestamp(datetime(2026, 9, 23, 18, tzinfo=UTC).timestamp())
    assert [(e.title, _hm(e)) for e in evs] == [("Standup (moved)", "10:30"),
                                                ("Dentist", local_18.strftime("%H:%M"))]
    assert evs[1].location == "Main St 4" and evs[1].end - evs[1].start == 3600
    assert calendar.day(cfg, date(2026, 9, 30)) == []                    # EXDATE
    assert [e.title for e in calendar.day(cfg, date(2026, 10, 7))] == ["Standup"]
    trip = calendar.day(cfg, date(2026, 9, 25))
    assert trip[0].title == "Trip" and trip[0].all_day


def test_current_next_and_state(cfg):
    evs = calendar.events(cfg, NOW, datetime(2026, 9, 25))
    assert calendar.current(evs, NOW.timestamp()) is None
    nxt = calendar.next_timed(evs, NOW.timestamp())
    assert nxt.title == "Standup (moved)"
    assert "in 40 min" in calendar.state_lines(evs, NOW)[0]
    in_it = NOW.replace(hour=10, minute=35).timestamp()
    assert calendar.current(evs, in_it).title == "Standup (moved)"
    assert calendar.current([e for e in evs if e.all_day], datetime(2026, 9, 24, 12).timestamp()) is None


def test_heads_up_once(cfg, tmp_path):
    from hearthsmith.daemon import heads_up
    c, s = Config(), Store(tmp_path / "t.db")
    evs = calendar.events(cfg, NOW, datetime(2026, 9, 24))
    t = NOW.replace(hour=10, minute=22).timestamp()
    assert heads_up(c, s, evs, NOW.timestamp()) is None                 # 40 min out
    ev = heads_up(c, s, evs, t)
    assert ev.title == "Standup (moved)"
    s.kv_set(f"cal_warned:{ev.key}", "1")
    assert heads_up(c, s, evs, t) is None


def test_answer(cfg):
    assert calendar.wants("what's on my calendar tomorrow?")
    assert calendar.wants("when's my next meeting")
    assert not calendar.wants("add a meeting prep task")
    out = calendar.answer(cfg, "what's on my calendar", NOW)
    assert out.startswith("Rest of today: Standup (moved) 10:30; Dentist")
    assert calendar.answer(cfg, "anything tomorrow on my calendar?", NOW) == "Tomorrow: Trip (all day)."
    assert calendar.answer(cfg, "when is my next meeting", NOW).startswith("Next: Standup (moved)")


def test_brief_includes_calendar(cfg, tmp_path):
    from hearthsmith import brief
    s = Store(tmp_path / "t.db")
    evs = calendar.events(cfg, NOW, datetime(2026, 9, 25))
    f = brief.facts(s, "morning", NOW, events=evs)
    assert [x.split(" — ")[0] for x in f["calendar"]] == ["Standup (moved)", "Dentist"]
    assert "On the calendar: 'Standup (moved)" in brief.template(f)
    eve = brief.facts(s, "evening", NOW.replace(hour=19), events=evs)
    assert eve["calendar"][0].startswith("Trip") and not brief.quiet(eve)


def test_no_feed_is_no_calendar(monkeypatch):
    monkeypatch.delenv("HEARTHSMITH_CALENDAR", raising=False)
    assert calendar.feeds(CalendarCfg()) == []
    assert "iCal link" in calendar.answer(CalendarCfg(), "what's on my calendar")
