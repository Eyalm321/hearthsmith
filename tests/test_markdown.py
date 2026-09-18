from hearthsmith.adapters import markdown
from hearthsmith.store import Store


def test_markdown_import(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text("# todo\n- [ ] forge a horseshoe @due(2030-01-02) +stable #metal\n- [x] buy coal\n")
    s = Store(tmp_path / "t.db")
    assert markdown.sync(f, s) == 2
    open_ = s.list()
    assert [t.title for t in open_] == ["forge a horseshoe"]
    assert open_[0].project == "stable" and open_[0].tags == "metal" and open_[0].due
    assert s.list("done")[0].title == "buy coal"
    # idempotent
    assert markdown.sync(f, s) == 2 and len(s.list(None)) == 2
