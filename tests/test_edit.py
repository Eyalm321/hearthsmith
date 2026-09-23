import pytest

from hearthsmith.route import _edit, _named_project
from hearthsmith.store import Store

PROJECTS = ["sunsations-website", "hearthsmith", "web"]


@pytest.fixture
def s(tmp_path):
    st = Store(tmp_path / "t.db")
    st.add("get quote out to coffee parts website", project="sunsations-website")
    st.add("create facebook messenger ai automations for private replies")
    return st


def _coffee(s):
    return next(t for t in s.list() if "coffee" in t.title)


@pytest.mark.parametrize("text", [
    "change task for coffee parts website to have no project",
    "remove the project from the coffee parts task",
    "the coffee quote shouldn't have a project",
    "take the coffee task out of the project",
])
def test_clear_project(s, text):
    r = _edit(text, s, s.list(), None, PROJECTS)
    assert r.intent == "edit" and _coffee(s).project is None
    assert len(s.list()) == 2                                  # nothing added


def test_set_project_and_rename(s):
    r = _edit("move the coffee task to project web", s, s.list(), None, PROJECTS)
    assert _coffee(s).project == "web" and "web" in r.text
    _edit("put it under +hearthsmith", s, s.list(), _coffee(s).id, PROJECTS)
    assert _coffee(s).project == "hearthsmith"
    _edit("rename the coffee task to get espresso part quotes", s, s.list(), None, PROJECTS)
    assert any(t.title == "get espresso part quotes" for t in s.list())


def test_unknown_task_says_so_and_adds_nothing(s):
    r = _edit("change the dentist task to have no project", s, s.list(), None, PROJECTS)
    assert "Couldn't find" in r.text and len(s.list()) == 2
    assert _edit("remind me to call mum", s, s.list(), None, PROJECTS) is None


def test_project_only_when_named():
    assert _named_project("get quote out to coffee parts website", PROJECTS) is None
    assert _named_project("fix the header on the sunsations website", PROJECTS) == "sunsations-website"
    assert _named_project("add dark mode +web", PROJECTS) == "web"
    assert _named_project("improve hearthsmith voice", PROJECTS) == "hearthsmith"
