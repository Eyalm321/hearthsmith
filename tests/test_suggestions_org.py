"""Stage 3: judging a pane's suggestion against its goal org. No live models."""
from hearthsmith import config
from hearthsmith.adapters.hyperpanes import Pane
from hearthsmith.daemon import condense, gather_org


class FakeHP:
    def __init__(self, msgs, tasks):
        self.msgs, self.tasks = msgs, tasks

    def messages(self, pid, limit=20):
        return self.msgs.get(pid, [])

    def queue_tasks(self, q):
        return self.tasks.get(q, [])

    def last_answer(self, pid, n=1600):
        return f"last words of {pid}"[:n]


def org():
    spec = Pane("s1", "spec", "/x/proj", "running", "idle", "t",
                meta={"role": "spec", "goal": "g1", "project": "/x/proj"})
    a = Pane("a1", "impl-a", "/x/proj", "running", "busy", "t",
             meta={"role": "impl", "goal": "g1", "parent": "s1", "project": "/x/proj"})
    b = Pane("b1", "impl-b", "/x/proj", "running", "idle", "t",
             meta={"role": "impl", "goal": "g1", "parent": "s1", "project": "/x/proj"})
    return spec, a, b


def test_gather_reads_spec_siblings_bus_and_queue():
    spec, a, b = org()
    hp = FakeHP({"s1": [{"from": "a1", "body": "progress: half done"}]},
                {"proj-g1": [{"title": "T1", "state": "claimed", "claimedBy": "a1", "error": None, "updatedAt": 0},
                             {"title": "T2", "state": "done", "claimedBy": "b1", "error": None, "updatedAt": 0}]})
    o = gather_org(hp, b, [spec, a])
    assert o["goal"] == "g1" and o["project"] == "proj" and o["queue"]["name"] == "proj-g1"
    assert o["spec_text"] == "last words of s1"
    assert [x["role"] for x in o["siblings"]] == ["spec", "impl"]
    assert o["reports"][0]["body"] == "progress: half done"
    assert o["queue"]["counts"]["claimed"] == 1 and o["queue"]["counts"]["done"] == 1


def test_condense_falls_back_to_counts_without_a_model(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    spec, a, b = org()
    hp = FakeHP({}, {"proj-g1": [{"title": "T1 wire the api", "state": "claimed", "claimedBy": "a1",
                                  "error": None, "updatedAt": 0}]})
    txt = condense(config.Config(), gather_org(hp, b, [spec, a]), "run the integration tests")
    assert "1 claimed" in txt and "T1 wire the api" in txt and "impl-a" in txt
    assert "count-only" in txt


def test_pressable_gate():
    spec, a, _ = org()
    orch = Pane("o", "orch", "/x", "running", "idle", "t", meta={"role": "goals-orch", "project": "/x"})
    yours = Pane("y", "canora", "/x", "running", "idle", "t")
    roles = ["impl", "spec"]
    assert a.pressable(roles) and spec.pressable(roles)
    assert not orch.pressable(roles) and not yours.pressable(roles)
