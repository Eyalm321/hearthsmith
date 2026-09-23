import json

import pytest

from hearthsmith import mcp_server
from hearthsmith.store import Store


@pytest.fixture
def s(tmp_path, monkeypatch):
    st = Store(tmp_path / "t.db")
    monkeypatch.setattr(mcp_server, "_store", st)
    return st


def test_edit_only_what_is_given(s):
    t = s.add("hone", project="axe", notes="left side")
    out = json.loads(mcp_server.hearthsmith_tasks_edit(t.id[:6], title="hone the edge",
                                                       due="2030-01-02", append_notes="then right"))
    assert out["title"] == "hone the edge" and out["project"] == "axe"
    assert out["notes"] == "left side\nthen right" and out["due"]
    out = json.loads(mcp_server.hearthsmith_tasks_edit(t.id, due="", repeat="mon"))
    assert out["due"] is None and out["repeat"] == "mon"
    assert "error" in json.loads(mcp_server.hearthsmith_tasks_edit(t.id, repeat="blue moon"))
    assert "error" in json.loads(mcp_server.hearthsmith_tasks_edit(t.id, parent_id=t.id))


def test_get_reopen_delete(s):
    t = s.add("launch")
    s.add("deploy", parent_id=t.id)
    got = json.loads(mcp_server.hearthsmith_tasks_get(t.id))
    assert [c["title"] for c in got["steps"]] == ["deploy"]
    s.set_state(t.id, "done")
    assert json.loads(mcp_server.hearthsmith_tasks_reopen(t.id))["state"] == "open"
    gone = json.loads(mcp_server.hearthsmith_tasks_delete(t.id))["deleted"]
    assert gone["title"] == "launch" and gone["steps"][0]["title"] == "deploy"
    assert s.list(None) == []
    assert "error" in json.loads(mcp_server.hearthsmith_tasks_delete("nope"))
