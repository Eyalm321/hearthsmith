import time

from forge.decide import rules
from forge.store import Store


def test_add_list_done(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("sharpen the axe", due=int(time.time()) - 3600)
    assert t.overdue
    assert [x.id for x in s.list()] == [t.id]
    s.set_state(t.id, "done")
    assert s.list() == []
    assert s.get(t.id[:4]).state == "done"


def test_upsert_external_keeps_nag_state(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.upsert_external("markdown", "abc", "fix the bellows")
    s.mark_nagged(t.id)
    t2 = s.upsert_external("markdown", "abc", "fix the bellows (again)")
    assert t2.id == t.id and t2.title.endswith("(again)") and t2.nag_count == 1


def test_snooze_hides_from_candidates(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("temper the blade")
    s.snooze(t.id, 30)
    assert s.get(t.id).snoozed


def test_rules_respects_gap(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("quench", due=int(time.time()) - 10)
    d = rules([t], last_nag_at=int(time.time()) - 60, min_gap_min=45)
    assert d.should_nag == 0.0 and d.urgency == "now" and d.task_id == t.id
    d = rules([t], last_nag_at=None, min_gap_min=45)
    assert d.should_nag > 0.5
