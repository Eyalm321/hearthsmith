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


def test_speculative_heads_scope_targets():
    """Each target head must offer only elements that operation can act on."""
    from forge.desktop.agent import _questions
    from forge.desktop.atspi import Element, Window

    def el(i, role, name, fillable):
        return Element(i, role, name, 0, 0, 10, 10, fillable, "app", "win")

    win = Window("app", "win", 0, 0, 100, 100, True, None, shell_id=1)
    els = [el(0, "push button", "Save", False), el(1, "entry", "Search", True),
           el(2, "link", "Home", False)]
    q = _questions(els, [win], win)
    assert set(q["click_target"].criteria) == {"0", "2"}
    assert set(q["type_target"].criteria) == {"1"}
    assert "window" not in q                      # only one window → no focus head
    assert "focus" not in q["operation"].criteria

    q2 = _questions([el(0, "entry", "Search", True)], [win], win)
    assert "click_target" not in q2               # nothing clickable → no click head
    assert "click" not in q2["operation"].criteria


def test_sensitive_fields_are_never_guessed():
    from forge.ask import sensitive
    assert sensitive("Password") == "secret"
    assert sensitive("Confirm password") == "secret"
    assert sensitive("Verification code") == "secret"
    assert sensitive("Username") == "yours"
    assert sensitive("Email address") == "yours"
    assert sensitive("Where from?") is None
    assert sensitive("Search") is None


def test_desktop_refuses_to_invent_a_password(monkeypatch, tmp_path):
    from forge import ask as asker
    from forge import config as c
    from forge.desktop.agent import _fill_value
    monkeypatch.setattr(asker, "ask", lambda *a, **k: None)      # user cancels
    cfg = c.Config(db_path=tmp_path / "t.db")
    try:
        _fill_value(cfg, "sign up for an account", "entry: Password [text field]")
    except PermissionError as e:
        assert "needs you" in str(e)
    else:
        raise AssertionError("a password must never be generated")


def test_question_routing():
    from forge.answer import is_question, needs_lookup, wants_research
    assert needs_lookup("what does the p2s cost")            # one page holds it
    assert not wants_research("what does the p2s cost")
    assert wants_research("how much do engineers charge on average")
    assert wants_research("compare vikunja and planka")      # not phrased as a question
    assert not needs_lookup("find one-way flights to oslo")  # an errand, not a question
    assert is_question("is proton free?")


def test_answer_refuses_when_the_page_does_not_say(tmp_path):
    from forge import config as c
    from forge.answer import from_page
    assert from_page(c.Config(db_path=tmp_path / "t.db"), "what does it cost", "") is None
