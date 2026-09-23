from datetime import datetime

import pytest

from hearthsmith.when import describe, parse

NOW = datetime(2026, 9, 23, 12, 0)          # a Wednesday, noon


def _p(text):
    title, due = parse(text, NOW)
    return title, datetime.fromtimestamp(due) if due else None


@pytest.mark.parametrize("text,title,due", [
    ("remind me to call the vet friday at 5pm", "call the vet", datetime(2026, 9, 25, 17, 0)),
    ("call the vet on friday", "call the vet", datetime(2026, 9, 25, 18, 0)),
    ("don't let me forget to pay rent tomorrow morning", "pay rent", datetime(2026, 9, 24, 9, 0)),
    ("take the bread out in 20 minutes", "take the bread out", datetime(2026, 9, 23, 12, 20)),
    ("renew passport by oct 3", "renew passport", datetime(2026, 10, 3, 18, 0)),
    ("dentist 14/10 at 9:30", "dentist", datetime(2026, 10, 14, 9, 30)),
    ("water the plants tonight", "water the plants", datetime(2026, 9, 23, 20, 0)),
    ("tonight at 9pm check the kiln", "check the kiln", datetime(2026, 9, 23, 21, 0)),
    ("standup at 9am", "standup", datetime(2026, 9, 24, 9, 0)),        # 9am has passed today
    ("at 5 ring mum", "ring mum", datetime(2026, 9, 23, 17, 0)),
    ("next wednesday demo prep", "demo prep", datetime(2026, 9, 30, 18, 0)),
    ("clean the forge this weekend", "clean the forge", datetime(2026, 9, 26, 18, 0)),
    ("send invoice next week", "send invoice", datetime(2026, 9, 28, 18, 0)),
    ("ship it the day after tomorrow", "ship it", datetime(2026, 9, 25, 18, 0)),
    ("file taxes on the 5th", "file taxes", datetime(2026, 10, 5, 18, 0)),
    ("book flights jan 5", "book flights", datetime(2027, 1, 5, 18, 0)),
    ("add a task to fix the bellows", "fix the bellows", None),
    ("buy sun cream", "buy sun cream", None),                 # not Sunday
    ("i need to order 3 spools", "order 3 spools", None),
    ("backup the nas at end of day", "backup the nas", datetime(2026, 9, 23, 18, 0)),
])
def test_parse(text, title, due):
    assert _p(text) == (title, due)


def test_describe():
    assert describe(int(datetime(2026, 9, 25, 17, 0).timestamp()), NOW) == "Friday 17:00"
    assert describe(int(datetime(2026, 9, 24, 9, 0).timestamp()), NOW) == "tomorrow 09:00"
    assert describe(None, NOW) == "no deadline"


@pytest.mark.parametrize("text,is_reminder", [
    ("remind me to research filament prices friday", True),
    ("don't let me forget the kiln", True),
    ("add a task: renew the domain", True),
    ("put it on the ledger to call Sam", True),
    ("research filament prices", False),
    ("i need to open a terminal", False),
])
def test_reminder(text, is_reminder):
    from hearthsmith.when import REMINDER
    assert bool(REMINDER.match(text)) is is_reminder


def test_bare_date_is_due_by_end_of_day():
    from hearthsmith.when import DAY_END, from_iso
    assert datetime.fromtimestamp(from_iso("2026-09-25")) == datetime(2026, 9, 25, DAY_END)
    assert datetime.fromtimestamp(from_iso("2026-09-25T07:15")) == datetime(2026, 9, 25, 7, 15)
