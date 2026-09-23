from datetime import datetime, timedelta

from hearthsmith import brief, review
from hearthsmith.calendar import Event
from hearthsmith.config import BriefCfg
from hearthsmith.memory import Memory, rule_for
from hearthsmith.store import Store

FRI = datetime(2026, 9, 25, 16, 30)


def _ts(d):
    return int(d.timestamp())


def _store(tmp_path):
    s = Store(tmp_path / "t.db")
    for i, (title, due_off, done_off) in enumerate([
            ("ship the changelog", -1, -2),        # done on time
            ("pay rent", -3, -1),                  # done late
            ("write landing copy", None, -1)]):
        t = s.add(title, due=_ts(FRI + timedelta(days=due_off)) if due_off is not None else None,
                  project="web" if i != 1 else None)
        s.set_state(t.id, "done")
        s.db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (_ts(FRI + timedelta(days=done_off)), t.id))
    old = s.add("do the taxes")
    s.db.execute("UPDATE tasks SET created_at=? WHERE id=?", (_ts(FRI - timedelta(days=20)), old.id))
    for _ in range(5):
        s.mark_nagged(old.id)
    s.add("renew passport", due=_ts(FRI - timedelta(days=2)))
    s.add("send invoice", due=_ts(FRI + timedelta(days=3)))
    return s


def test_weekly_facts(tmp_path):
    s = _store(tmp_path)
    Memory(s).add("focus on web this week", FRI - timedelta(days=4))
    evs = [Event("Standup", _ts(FRI + timedelta(days=3, hours=-6)), _ts(FRI + timedelta(days=3, hours=-5)))]
    f = review.facts(s, FRI, events=evs)
    assert f["done_count"] == 3 and f["done_late"] == ["pay rent"]
    assert f["by_project"] == {"web": 2}
    assert f["overdue"] == ["renew passport (2d)"]
    assert f["stuck"] == ["do the taxes"]
    assert f["due_next_week"] == ["send invoice (Mon)"]
    assert f["focus"] == "web" and f["focus_done"] == 2
    assert f["meetings_next_week"] == 1
    t = review.template(f)
    assert "struck off 3, 1 of them late" in t and "'do the taxes'" in t
    assert t.endswith("What's the focus next week?")


def test_weekly_once_per_week(tmp_path):
    s, cfg = Store(tmp_path / "t.db"), BriefCfg()
    assert brief.due_now(cfg, s, FRI.replace(hour=15)) != "weekly"
    assert brief.due_now(cfg, s, FRI) == "weekly"
    brief.mark(s, "weekly", FRI)
    assert brief.due_now(cfg, s, FRI.replace(hour=19)) == "evening"   # the daily wrap still comes
    assert brief.due_now(cfg, s, FRI + timedelta(days=7)) == "weekly"
    assert s.kv_get("brief_last_at") in (None, "")                    # weekly doesn't move "overnight"


def test_next_week_focus_runs_through_next_sunday():
    rule, exp = rule_for("focus on the launch next week", FRI)
    assert rule == {"focus": "launch"}
    assert datetime.fromtimestamp(exp) == datetime(2026, 10, 5)


def test_weekly_ask():
    from hearthsmith.route import BRIEF_ASK
    for t in ("weekly review", "how did my week go", "review the week", "give me the week's recap"):
        assert BRIEF_ASK.search(t), t
