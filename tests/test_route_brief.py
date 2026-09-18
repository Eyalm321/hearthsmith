import pytest

from forge.route import _brief, _native_terminal, _wants_agent


@pytest.mark.parametrize("text", [
    "can you open a terminal",
    "open a terminal",
    "open a new terminal",
    "Open a terminal please",
    "could you open up a shell for me?",
    "open a new pane with claude",
    "spawn an agent in forge",
    "open a terminal in ~/dev/forge",
    "on hyperpanes, open a pane",
])
def test_only_plumbing_has_no_brief(text):
    assert _brief(text) == ""


@pytest.mark.parametrize("text,brief", [
    ("open a new pane with claude and ask it to research ollama pricing", "research ollama pricing"),
    ("can you open a terminal and tell it to run the tests", "run the tests"),
    ("open a new terminal and fix the bellows", "fix the bellows"),
    ("look up the going rate for a farrier", "look up the going rate for a farrier"),
])
def test_assignment_survives_plumbing(text, brief):
    assert _brief(text) == brief


def test_shell_unless_claude_named_or_work_given():
    assert not _wants_agent("can you open a terminal")
    assert not _wants_agent("open a shell in forge")
    assert _wants_agent("open a pane with claude")
    assert _wants_agent("open an agent")
    assert _wants_agent("open a terminal and ask it to run the tests")


@pytest.mark.parametrize("text", [
    "id like you to open a new terminal in linux native terminal",
    "I'd like you to open a new terminal in linux native terminal",
    "open a terminal outside hyperpanes",
    "open a native terminal",
    "open ptyxis",
    "can you open a real terminal window, not in hyperpanes",
])
def test_native_terminal_is_a_desktop_window(text):
    assert _brief(text) == ""
    assert _native_terminal(text)


@pytest.mark.parametrize("text", [
    "can you open a terminal",
    "open a new pane with claude",
    "open a native terminal and ask it to run the tests",
    "open a terminal in forge",
])
def test_pane_requests_stay_in_hyperpanes(text):
    assert not _native_terminal(text)
