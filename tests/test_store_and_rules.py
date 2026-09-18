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


def test_wire_shapes():
    from typesafe_sdk import Choice, Noul, Score

    from forge.decide import _wire
    assert _wire(Noul(instructions="x")) == {"type": "noul", "instructions": "x"}
    assert _wire(Score(instructions="x", criteria=["a", "b"])) == {
        "type": "score", "instructions": "x", "criteria": ["a", "b"]}
    assert _wire(Choice(instructions="x", criteria={"k": "v"}))["criteria"] == {"k": "v"}


def test_route_falls_back_to_store_when_decider_down(tmp_path, monkeypatch):
    from forge import config as c
    from forge.route import route
    cfg = c.Config(db_path=tmp_path / "t.db", sprite_path=tmp_path / "s.json")
    cfg.nag.markdown_file = None
    cfg.hyperpanes.control_file = tmp_path / "nope.json"
    monkeypatch.delenv(cfg.decide.adapter_key_env, raising=False)
    r = route("buy coal", cfg)
    assert r.intent == "add" and r.task_id and "buy coal" in r.text
