import json
from datetime import datetime

import pytest

from hearthsmith.config import NagCfg
from hearthsmith.daemon import in_quiet_hours
from hearthsmith.memory import Memory, confirm, is_memory, noticed, rule_for
from hearthsmith.store import Store

NOW = datetime(2026, 9, 23, 12, 0)          # Wednesday


@pytest.mark.parametrize("text,rule", [
    ("don't nag me before 10", {"quiet_end": 10}),
    ("no nagging me before 9:30am", {"quiet_end": 10}),
    ("never bother me after 9pm", {"quiet_start": 21}),
    ("I don't want nags before 10", {"quiet_end": 10}),
    ("no pings after 21:00", {"quiet_start": 21}),
    ("I don't want any reminders on weekends", {"weekends": "off"}),
    ("don't ping me on weekends", {"weekends": "off"}),
    ("weekends are mine", {"weekends": "off"}),
    ("stop nagging me about email", {"mute": "email"}),
    ("don't remind me about the taxes stuff", {"mute": "taxes"}),
    ("focus on the web launch this week", {"focus": "web launch"}),
    ("the site launch matters most today", {"focus": "site launch"}),
    ("I work best in the evenings", {}),
])
def test_rule_for(text, rule):
    assert rule_for(text, NOW)[0] == rule


def test_focus_expiry():
    _, exp = rule_for("focus on the web launch this week", NOW)
    assert datetime.fromtimestamp(exp) == datetime(2026, 9, 28)          # through Sunday
    _, exp = rule_for("the site launch matters most today", NOW)
    assert datetime.fromtimestamp(exp) == datetime(2026, 9, 24)


@pytest.mark.parametrize("text,yes", [
    ("remember that I work best in the evenings", True),
    ("I hate being nagged about email", True),
    ("don't nag me before 10", True),
    ("my mornings are for deep work", True),
    ("remind me to call mum friday", False),
    ("remember to buy coal", False),
    ("call the vet", False),
])
def test_is_memory(text, yes):
    assert is_memory(text) is yes


def test_rules_shape_the_nag_loop(tmp_path):
    s = Store(tmp_path / "t.db")
    m = Memory(s)
    a = s.add("answer email backlog")
    b = s.add("write landing copy", project="web")
    c = s.add("buy coal")
    m.add("stop nagging me about email", NOW)
    m.add("focus on web this week", NOW)
    assert [t.id for t in m.shape([a, c, b])] == [b.id, c.id]
    m.add("don't nag me before 10", NOW)
    m.add("don't nag me before 11", NOW)                    # replaces, doesn't stack
    assert len([x for x in m.items() if "quiet_end" in json.loads(x["rule"])]) == 1
    cfg = NagCfg()
    assert in_quiet_hours(cfg, s, NOW.replace(hour=10, minute=30))
    assert not in_quiet_hours(cfg, s, NOW)
    m.add("weekends are off", NOW)
    assert in_quiet_hours(cfg, s, datetime(2026, 9, 26, 14))          # Saturday afternoon
    assert m.forget("email")[0]["text"] == "stop nagging me about email"
    assert [t.id for t in m.shape([a, c])] == [a.id, c.id]
    assert "No nags before 11:00" in confirm(m.add("remember: don't nag me before 11", NOW))


def test_noticed(tmp_path):
    s = Store(tmp_path / "t.db")
    for i in range(9):
        t = s.add(f"t{i}")
        s.set_state(t.id, "done")
        s.db.execute("UPDATE tasks SET updated_at=? WHERE id=?",
                     (int(NOW.replace(hour=10 + i % 2).timestamp()), t.id))
    stuck = s.add("do the taxes")
    for _ in range(5):
        s.mark_nagged(stuck.id)
    seen = noticed(s, NOW)
    assert any("09:00–12:00" in x for x in seen)
    assert any("'do the taxes' has been nagged 5 times" in x for x in seen)
