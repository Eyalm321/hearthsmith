import queue
import threading
import time

import numpy as np

from hearthsmith import convo, ear
from hearthsmith.route import RESCHEDULE, _reschedule
from hearthsmith.store import Store

FRAME = ear.FRAME_BYTES // 2          # samples per frame


class FakeMic:
    """Frames from a script: ('q', n) quiet, ('s', n) speech, then the mic ends."""

    def __init__(self, script, floor=50.0):
        self.q = queue.Queue()
        rng = np.random.default_rng(0)
        for kind, n in script:
            amp = 3000 if kind == "s" else 40
            for _ in range(n):
                self.q.put((rng.standard_normal(FRAME) * amp).astype(np.int16).tobytes())
        self.floor, self.min_rms, self.noise_mult = floor, 250, 3.0
        self.proc = type("P", (), {"poll": lambda self: 0 if mic_done[0] else None})()

    def frame(self, timeout=1.0):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            mic_done[0] = True
            return None

    threshold = ear.Mic.threshold


mic_done = [False]


def test_utterance_ends_on_pause():
    mic_done[0] = False
    m = FakeMic([("q", 20), ("s", 50), ("q", 60)])      # 0.4s quiet, 1s speech, 1.2s quiet
    pcm = ear.utterance(m, wait_s=5, end_silence_s=0.8, cancel=threading.Event())
    secs = len(pcm) / 2 / ear.RATE
    assert 1.8 < secs < 2.4                               # preroll + speech + the closing pause


def test_utterance_gives_up_and_ignores_blips():
    mic_done[0] = False
    assert ear.utterance(FakeMic([("q", 200)]), 1.0, 0.8, threading.Event()) is None
    mic_done[0] = False
    assert ear.utterance(FakeMic([("q", 10), ("s", 6), ("q", 60)]), 5, 0.8, threading.Event()) is None


def test_ending_phrases():
    for t in ("that's all", "Thanks, that's it.", "bye", "OK thank you, goodbye!", "stop listening"):
        assert ear.ENDING.match(t), t
    for t in ("remind me to say bye to mum", "that's all the tasks?"):
        assert not ear.ENDING.match(t), t


def test_send_without_listener(tmp_path, monkeypatch):
    monkeypatch.setattr(ear, "SOCK", tmp_path / "nope.sock")
    assert "error" in ear.send("status", timeout=0.2)


def test_follow_up_it(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("call the vet", due=int(time.time()) + 3600)
    convo.log(s, "remind me to call the vet", "Noted.", "add", t.id)
    assert convo.last_task(s) == t.id
    assert RESCHEDULE.match("move it to friday at 5pm")
    r = _reschedule("move it to friday at 5pm", s, s.list(), convo.last_task(s))
    assert r and r.task_id == t.id and "17:00" in r.text
    other = s.add("buy coal")
    r = _reschedule("push buy coal to tomorrow", s, s.list(), t.id)
    assert r.task_id == other.id
    assert _reschedule("move it to the other folder", s, s.list(), t.id) is None
    old = time.time() + convo.WINDOW_S + 5
    assert convo.last_task(s, now=old) is None
