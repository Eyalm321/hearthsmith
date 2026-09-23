from types import SimpleNamespace

from hearthsmith import config
from hearthsmith.daemon import collect_research
from hearthsmith.handoff import brief_for, hand, take_back
from hearthsmith.route import HANDOFF_ASK
from hearthsmith.store import Store


class FakeHP:
    """Just enough hyperpanes: one project, panes it opens, text it was typed."""

    def __init__(self, path):
        self.panes, self.typed, self.opened = [], {}, []
        self.projects = [{"id": "p1", "name": "web", "path": str(path)}]
        self.report = "Wrote the changelog in CHANGELOG.md; left the release date for you."

    def snapshot(self, with_screens=True):
        return SimpleNamespace(panes=self.panes, projects=self.projects,
                               project_for_cwd=lambda cwd: None)

    def new_pane(self, command=None, cwd=None, label=None, meta=None, **_):
        pid = f"pane{len(self.opened)}"
        self.opened.append({"cwd": cwd, "label": label, "meta": meta})
        self.panes.append(SimpleNamespace(id=pid, label=label, cwd=cwd, activity="busy", meta=meta))
        return pid

    def answer_trust(self, pane_id):
        return True

    def wait_ready(self, pane_id):
        return True

    def type_into(self, pane_id, text, user_originated=False):
        self.typed[pane_id] = text
        return True

    def screen(self, pane_id, tail=None):
        return "done"

    def last_answer(self, pane_id, max_chars=1600):
        return self.report


def test_hand_and_report_back(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("write the changelog", project="web", notes="since v0.3")
    s.add("collect merged PRs", parent_id=t.id)
    hp = FakeHP(tmp_path)
    h = hand(config.Config(), s, t, hp)
    assert h.ok and h.pane_id == "pane0"
    assert hp.opened[0]["cwd"] == str(tmp_path) and hp.opened[0]["meta"] == {"task": t.id}
    brief = hp.typed["pane0"]
    assert "write the changelog" in brief and "since v0.3" in brief and "- collect merged PRs" in brief
    assert s.get(t.id).state == "delegated"
    assert not hand(config.Config(), s, s.get(t.id), hp).ok          # already with an agent

    hp.panes[0].activity = "idle"
    found = collect_research(config.Config(), s, hp, settle_s=0)
    assert found[0]["kind"] == "task" and found[0]["task_id"] == t.id
    back = s.get(t.id)
    assert back.state == "open"                                      # reopened, not done
    assert "agent (" in back.notes and "CHANGELOG.md" in back.notes
    assert s.watches() == [] and s.last_run(t.id)["body"] == "agent"


def test_research_watch_still_closes(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("going rate for a farrier")
    hp = FakeHP(tmp_path)
    hp.panes.append(SimpleNamespace(id="r1", label="r", cwd="/", activity="idle", meta={}))
    s.watch("r1", t.title, t.id)
    collect_research(config.Config(), s, hp, settle_s=0)
    assert s.get(t.id).state == "done"


def test_take_back(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("x")
    hand(config.Config(), s, t, FakeHP(tmp_path))
    take_back(s, s.get(t.id))
    assert s.get(t.id).state == "open" and s.watches() == []


def test_brief_names_due_and_asks_for_report(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("send the invoice", due=2_000_000_000)
    b = brief_for(s, t)
    assert b.startswith("Task from my hearthsmith ledger: send the invoice") and "report" in b


def test_handoff_phrases():
    for text, what in (("hand the changelog to an agent", "the changelog"),
                       ("give the invoice off to claude", "the invoice"),
                       ("let an agent do the release notes", "the release notes")):
        m = HANDOFF_ASK.search(text)
        assert m and (m["a"] or m["b"]).strip() == what, text
    assert not HANDOFF_ASK.search("open a new pane with claude")


def test_transcript_answer(tmp_path):
    import json
    import time

    from hearthsmith.handoff import transcript_answer
    cwd = "/home/me/dev/web.site"
    d = tmp_path / "-home-me-dev-web-site"
    d.mkdir()

    def session(name, first, reply):
        rows = [{"type": "user", "message": {"role": "user", "content": first}},
                {"type": "assistant", "message": {"role": "assistant",
                                                  "content": [{"type": "text", "text": reply}]}}]
        (d / name).write_text("\n".join(json.dumps(r) for r in rows))

    session("mine.jsonl", "fix the header", "Fixed the header.")
    session("his.jsonl", "Task from my hearthsmith ledger: write the changelog\n\nNotes…", "Wrote it.")
    since = int(time.time()) - 5
    got = transcript_answer(cwd, "Task from my hearthsmith ledger: write the changelog", since, tmp_path)
    assert got == "Wrote it."
    assert transcript_answer(cwd, "Task from my hearthsmith ledger: other", since, tmp_path) == ""
    assert transcript_answer(cwd, "Task from my hearthsmith ledger: write the changelog",
                             int(time.time()) + 60, tmp_path) == ""       # older than the hand-off
