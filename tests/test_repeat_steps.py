import sqlite3
from datetime import datetime

import pytest

from hearthsmith import when
from hearthsmith.adapters.markdown import parse_line
from hearthsmith.steps import actionable, parse_steps
from hearthsmith.store import Store

NOW = datetime(2026, 9, 23, 12, 0)          # a Wednesday, noon


def _dt(ts):
    return datetime.fromtimestamp(ts)


@pytest.mark.parametrize("text,title,rule,first", [
    ("water the plants every monday", "water the plants", "mon", datetime(2026, 9, 28, 18, 0)),
    ("remind me to take out the bins every tuesday and friday at 7pm", "take out the bins",
     "tue,fri", datetime(2026, 9, 25, 19, 0)),
    ("standup every weekday at 9:30", "standup", "weekday", datetime(2026, 9, 24, 9, 30)),
    ("daily vitamins at 9pm", "vitamins", "1d", datetime(2026, 9, 23, 21, 0)),
    ("stretch every morning", "stretch", "1d", datetime(2026, 9, 24, 9, 0)),
    ("pay rent monthly", "pay rent", "1m", datetime(2026, 9, 23, 18, 0)),
    ("review budget every 2 weeks", "review budget", "2w", datetime(2026, 9, 23, 18, 0)),
    ("clean the forge every other week", "clean the forge", "2w", datetime(2026, 9, 23, 18, 0)),
    ("gym on mondays and thursdays", "gym", "mon,thu", datetime(2026, 9, 24, 18, 0)),
])
def test_parse_repeat(text, title, rule, first):
    t, due, r = when.parse_full(text, NOW)
    assert (t, r, _dt(due)) == (title, rule, first)


def test_no_repeat_for_one_offs():
    assert when.parse_full("call the vet friday", NOW)[2] == ""
    assert when.parse_full("buy sun cream", NOW)[2] == ""


@pytest.mark.parametrize("rule,prev,now,nxt", [
    ("1d", datetime(2026, 9, 23, 18), NOW, datetime(2026, 9, 24, 18)),              # done early
    ("1d", datetime(2026, 9, 20, 18), NOW, datetime(2026, 9, 23, 18)),              # done late: no backlog
    ("mon", datetime(2026, 9, 21, 9), NOW, datetime(2026, 9, 28, 9)),
    ("weekday", datetime(2026, 9, 25, 9), datetime(2026, 9, 25, 10), datetime(2026, 9, 28, 9)),
    ("1m", datetime(2026, 1, 31, 18), datetime(2026, 1, 31, 19), datetime(2026, 2, 28, 18)),
    ("2w", datetime(2026, 9, 9, 18), NOW, datetime(2026, 9, 23, 18)),
    ("2w", datetime(2026, 9, 2, 18), NOW, datetime(2026, 9, 30, 18)),               # skipped one
])
def test_next_due(rule, prev, now, nxt):
    assert _dt(when.next_due(rule, int(prev.timestamp()), now)) == nxt


def test_rule_of_and_describe():
    assert when.rule_of("mon,thu") == "mon,thu"
    assert when.rule_of("2 weeks") == "2w"
    assert when.rule_of("weekday") == "weekday"
    assert when.rule_of("friday") == "fri"
    assert when.rule_of("blue moon") is None
    assert when.describe_rule("mon,thu") == "every Mon, Thu"
    assert when.describe_rule("2w") == "every 2 weeks"
    t = parse_line("water plants @every(mon) +garden")
    assert (t[0], t[2], t[4]) == ("water plants", "garden", "mon") and t[1]
    with pytest.raises(ValueError):
        parse_line("x @every(blue moon)")


def test_done_rolls_a_repeat_once(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("water plants", due=int(datetime(2026, 9, 21, 18).timestamp()), repeat="mon",
              tags="garden")
    s.add("fill the can", parent_id=t.id)
    done = s.set_state(t.id, "done")
    nxt = s.get(done.next_id)
    assert nxt.title == "water plants" and nxt.repeat == "mon" and nxt.tags == "garden"
    assert _dt(nxt.due).weekday() == 0 and nxt.due > t.due
    assert [c.title for c in s.children(nxt.id)] == ["fill the can"]
    assert s.children(nxt.id)[0].state == "open"
    s.set_state(t.id, "open")
    s.set_state(t.id, "done")                     # reopen + done again: no second copy
    assert len([x for x in s.list(None) if x.title == "water plants"]) == 2


def test_steps(tmp_path):
    s = Store(tmp_path / "t.db")
    p = s.add("launch the site", due=int(NOW.timestamp()), project="web")
    a = s.add("write copy", parent_id=p.id)
    s.add("deploy", parent_id=p.id)
    assert a.project == "web"                      # steps live where their task does
    todo = actionable(s, s.list("open"))
    assert [t.title for t in todo] == ["write copy (step of 'launch the site')"]
    assert todo[0].due == p.due and todo[0].id == a.id
    s.set_state(p.id, "done")                      # finishing the task finishes its steps
    assert s.children(p.id, "open") == []
    s.delete(p.id)
    assert s.list(None) == []


def test_parse_steps():
    out = parse_steps("Here:\n1. Draft the copy.\n- **Pick a domain**\n* deploy to fly\nStep 4: tell people\n")
    assert out == ["Draft the copy", "Pick a domain", "deploy to fly", "tell people"]


def test_old_db_gets_new_columns(tmp_path):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL "
              "DEFAULT 'open', due INTEGER, project TEXT, source TEXT NOT NULL DEFAULT 'hearthsmith', "
              "source_id TEXT, tags TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', "
              "snoozed_until INTEGER, nag_count INTEGER NOT NULL DEFAULT 0, last_nag_at INTEGER, "
              "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    c.execute("INSERT INTO tasks (id,title,created_at,updated_at) VALUES ('abc','old one',1,1)")
    c.commit(); c.close()
    s = Store(db)
    t = s.get("abc")
    assert t.title == "old one" and t.repeat == "" and t.parent_id is None


def test_split_only_reuses_a_task_it_names():
    from hearthsmith.route import SPLIT_ASK, _same_thing
    what = SPLIT_ASK.sub("", "break down launching the hearthsmith website").strip()
    assert what == "launching the hearthsmith website"
    assert not _same_thing(what, "research filament prices")
    assert _same_thing(what, "launch the website")
    assert SPLIT_ASK.sub("", "split the move into steps").strip() == "the move"


def test_questions_are_not_steps():
    assert parse_steps("Could you clarify:\n- What platform is it on?\n- Do you have hosting?") == []
