import time
from types import SimpleNamespace

from hearthsmith import config, offer
from hearthsmith.store import Store


def _stuck_task(s, n=5, **kw):
    t = s.add("do the taxes", **kw)
    for _ in range(n):
        s.mark_nagged(t.id)
    return s.get(t.id)


def test_stuck_and_offer_once(tmp_path):
    cfg, s = config.Config(), Store(tmp_path / "t.db")
    assert not offer.stuck(cfg, s, _stuck_task(s, n=2))
    t = _stuck_task(s)
    assert offer.stuck(cfg, s, t)
    text = offer.make(s, t, None)
    assert "5 nags" in text and "split it into steps" in text
    assert not offer.stuck(cfg, s, t)                                  # not again for 3 days
    assert offer.stuck(cfg, s, t, now=time.time() + offer.RETRY_S + 1)
    s.add("gather receipts", parent_id=t.id)
    assert not offer.stuck(cfg, s, t)                                  # has steps now


def test_recommend_hand_when_project_has_a_folder(tmp_path):
    s = Store(tmp_path / "t.db")
    snap = SimpleNamespace(projects=[{"id": "p", "name": "web", "path": "/w"}])
    assert offer.recommend(_stuck_task(s, project="web"), snap) == "hand"
    assert offer.recommend(_stuck_task(s), snap) == "split"


def test_answers(tmp_path, monkeypatch):
    cfg, s = config.Config(), Store(tmp_path / "t.db")
    t = _stuck_task(s)
    done = []
    monkeypatch.setattr(offer, "do", lambda c, st, task, how: done.append(how) or how)
    assert offer.answer(cfg, s, "split it") is None                   # nothing offered yet
    offer.make(s, t, None)
    assert offer.answer(cfg, s, "split the move into smaller steps please") is None  # its own ask
    assert offer.answer(cfg, s, "hand it off") == "hand"
    assert offer.answer(cfg, s, "yes") == "split"                     # the recommendation
    assert offer.answer(cfg, s, "yes", now=time.time() + offer.BARE_S + 1) is None
    assert offer.answer(cfg, s, "what's the weather") is None


def test_no_stops_the_nagging_until_the_date_moves(tmp_path):
    cfg, s = config.Config(), Store(tmp_path / "t.db")
    t = _stuck_task(s, due=2_000_000_000)
    offer.make(s, t, None)
    assert "won't nag" in offer.answer(cfg, s, "no, leave it")
    assert offer.declined(s, s.get(t.id)) and not offer.stuck(cfg, s, s.get(t.id))
    assert offer.pending(s) is None
    s.edit(t.id, due=2_000_100_000)
    assert not offer.declined(s, s.get(t.id))


def test_heartbeat_skips_declined(tmp_path):
    from hearthsmith.daemon import build_state
    cfg, s = config.Config(), Store(tmp_path / "t.db")
    cfg.nag.markdown_file = None
    t = _stuck_task(s)
    s.add("buy coal")
    offer.decline(s, t)
    hp = SimpleNamespace(snapshot=lambda **_: None)
    _, tasks = build_state(s, hp, cfg)
    assert [x.title for x in tasks] == ["buy coal"]
