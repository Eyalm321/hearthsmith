from datetime import datetime, timedelta

from hearthsmith import brief
from hearthsmith.config import BriefCfg
from hearthsmith.store import Store

NOW = datetime(2026, 9, 23, 9, 0)          # Wednesday morning


def _ts(d):
    return int(d.timestamp())


def _store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.add("temper the blade", due=_ts(NOW - timedelta(hours=3)))
    s.add("call the vet", due=_ts(NOW.replace(hour=17)))
    s.add("send invoice", due=_ts(NOW + timedelta(days=1, hours=2)))
    s.add("buy coal")
    b = s.add("fix the bellows")
    s.set_state(b.id, "blocked")
    s.record_run("what do people charge for PLA", "research", True, ["collected"],
                 started_at=_ts(NOW - timedelta(hours=5)))
    return s


def test_morning_facts(tmp_path):
    f = brief.facts(_store(tmp_path), "morning", NOW)
    assert f["overdue"] == ["temper the blade"]
    assert f["due_today"] == ["call the vet (17:00)"]
    assert f["blocked"] == ["fix the bellows"]
    assert f["agents_finished"] == ["what do people charge for PLA"]
    assert f["due_this_week"] == 1 and f["someday"] == 1
    t = brief.template(f)
    assert "'temper the blade'" in t and "'call the vet (17:00)'" in t and "Stuck" in t


def test_evening_facts_and_quiet(tmp_path):
    s = _store(tmp_path)
    eve = NOW.replace(hour=19)
    f = brief.facts(s, "evening", eve)
    assert f["due_tomorrow"] == ["send invoice"]
    assert not brief.quiet(f)
    empty = brief.facts(Store(tmp_path / "e.db"), "evening", eve)
    assert brief.quiet(empty)


def test_due_now_once_a_day_and_waits_for_you(tmp_path):
    s = Store(tmp_path / "t.db")
    cfg = BriefCfg()
    assert brief.due_now(cfg, s, NOW.replace(hour=8, minute=0)) is None       # before 08:30
    assert brief.due_now(cfg, s, NOW, idle=600_000) is None                   # away from desk
    assert brief.due_now(cfg, s, NOW, idle=5_000) == "morning"
    assert brief.due_now(cfg, s, NOW, idle=None) == "morning"                 # no idle monitor
    brief.mark(s, "morning", NOW)
    assert brief.due_now(cfg, s, NOW) is None
    assert brief.due_now(cfg, s, NOW.replace(hour=13)) is None                # past morning_until
    assert brief.due_now(cfg, s, NOW.replace(hour=19)) == "evening"
    brief.mark(s, "evening", NOW.replace(hour=19))
    assert brief.due_now(cfg, s, NOW.replace(hour=20)) is None
    assert brief.due_now(cfg, s, NOW + timedelta(days=1)) == "morning"


def test_brief_ask_phrases():
    from hearthsmith.route import BRIEF_ASK
    for t in ("brief me", "what's on my plate", "how did the day go", "what did i get done today",
              "give me my morning briefing"):
        assert BRIEF_ASK.search(t), t
    for t in ("brief the agent on the bug", "what's the P2S price"):
        assert not BRIEF_ASK.search(t), t
