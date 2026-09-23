"""hearthsmith-ear: talk to him out loud.

    hearthsmith-ear serve       the listener (hearthsmith-ear.service): keeps Whisper warm on the GPU
    hearthsmith-ear listen      one turn — say something, he answers (Super+J). Pressed while he's
                                talking: he stops and listens. Pressed while listening: cancel.
    hearthsmith-ear talk        conversation mode on/off: he keeps listening after each answer until
                                you say "that's all" or go quiet for `ear.conversation_idle_s`
    hearthsmith-ear stop        stop talking, stop listening
    hearthsmith-ear status

The mic (`ear.source`, a PipeWire source name; "" = default) is only open during a turn or a
conversation, never otherwise. Speech is found by level against the room's noise floor, measured
when the mic opens; a pause of `ear.end_silence_s` ends what you said. Whisper (faster-whisper,
`ear.model`) runs locally — audio never leaves the machine. What you said goes through `route`
exactly like typed text, with the conversation so far (convo.py), and the reply is shown on the
sprite and spoken. Start talking while he's answering and he stops to listen (`ear.barge_in`).

The client half (listen/talk/stop/status) is stdlib only, so the GTK sprite can use `send`.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import queue
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from hearthsmith.config import STATE_DIR

SOCK = STATE_DIR / "ear.sock"
RATE = 16000
FRAME_MS = 20
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2           # s16le mono
log = logging.getLogger("hearthsmith.ear")

# said to end a conversation
ENDING = re.compile(r"^\W*(?:ok(?:ay)?[,\s]+|thanks?[,\s]+|thank\s+you[,\s]+)*(?:that'?s\s+(?:all|it|everything)"
                    r"|(?:good)?bye|see\s+you|stop\s+listening|we'?re\s+done|i'?m\s+done|nothing\s+else"
                    r"|never\s*mind|go\s+to\s+sleep)\W*$", re.IGNORECASE)
# what Whisper says to silence and breath
HALLUCINATED = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching",
                "bye", "okay", "ok", "hmm", "uh", "um", "so", "the"}


# -- client ------------------------------------------------------------------------------------

def send(cmd: str, timeout: float = 3.0) -> dict:
    """One command to the listener; {"error": …} when it isn't running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(SOCK))
            s.sendall(cmd.encode() + b"\n")
            return json.loads(s.makefile().readline() or "{}")
    except (OSError, ValueError) as e:
        return {"error": f"listener not running ({type(e).__name__})"}


# -- audio -------------------------------------------------------------------------------------

class Mic:
    """parecord → 20 ms frames on a queue, with an adaptive speech threshold."""

    def __init__(self, source: str, min_rms: float = 250, noise_mult: float = 3.0):
        cmd = ["parecord", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1",
               "--latency-msec=40", *([f"--device={source}"] if source else [])]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.q: queue.Queue[bytes | None] = queue.Queue()
        self.min_rms, self.noise_mult = min_rms, noise_mult
        self.floor: float | None = None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        out = self.proc.stdout
        while True:
            buf = b""
            while len(buf) < FRAME_BYTES:
                chunk = out.read(FRAME_BYTES - len(buf))
                if not chunk:
                    self.q.put(None)
                    return
                buf += chunk
            self.q.put(buf)

    def frame(self, timeout: float = 1.0) -> bytes | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()

    def calibrate(self, seconds: float = 0.4) -> None:
        levels = []
        for _ in range(int(seconds * 1000 / FRAME_MS)):
            if (f := self.frame()) is None:
                break
            levels.append(rms(f))
        if levels:
            self.floor = sorted(levels)[len(levels) // 2]
        log.info("noise floor %.0f → threshold %.0f", self.floor or 0, self.threshold())

    def threshold(self, strict: float = 1.0) -> float:
        return max((self.floor or 0) * self.noise_mult, self.min_rms) * strict

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


def rms(frame: bytes) -> float:
    import numpy as np
    a = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def utterance(mic: Mic, wait_s: float, end_silence_s: float, cancel: threading.Event,
              seed: list[bytes] | None = None, max_s: float = 30.0) -> bytes | None:
    """Wait up to `wait_s` for speech, then record until a pause of `end_silence_s`. `seed` is
    audio already recognised as the start of speech (a barge-in)."""
    n = lambda s: max(1, int(s * 1000 / FRAME_MS))
    pre: collections.deque[bytes] = collections.deque(maxlen=n(0.3))
    buf: list[bytes] = list(seed or [])
    speaking, loud, quiet, waited = bool(seed), 0, 0, 0
    voiced = n(0.3) if seed else 0          # loud frames heard; the lead-in doesn't count
    thresh = mic.threshold()
    while not cancel.is_set():
        f = mic.frame()
        if f is None:
            if mic.proc.poll() is not None:
                return None
            continue
        is_loud = rms(f) > thresh
        if not speaking:
            pre.append(f)
            waited += 1
            loud = loud + 1 if is_loud else 0
            if loud >= n(0.08):
                speaking, buf = True, list(pre)
                quiet, voiced = 0, loud
            elif waited >= n(wait_s):
                return None
            continue
        buf.append(f)
        quiet = 0 if is_loud else quiet + 1
        voiced += is_loud
        if quiet >= n(end_silence_s) or len(buf) >= n(max_s):
            return b"".join(buf) if voiced >= n(0.25) else None   # a cough or a click isn't speech
    return None


def chime(name: str) -> None:
    f = Path(f"/usr/share/sounds/freedesktop/stereo/{name}.oga")
    if f.exists():
        subprocess.Popen(["pw-play", "--volume", "0.5", str(f)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


# -- speech → text -----------------------------------------------------------------------------

def _preload_cuda() -> None:
    """The pip wheels' cuBLAS/cuDNN aren't on the loader path; ctranslate2 needs them global."""
    import ctypes
    import glob
    try:
        import nvidia
    except ImportError:
        return
    for pat in ("cublas/lib/libcublasLt.so.*", "cublas/lib/libcublas.so.*", "cudnn/lib/libcudnn*.so.*"):
        for f in sorted(glob.glob(os.path.join(nvidia.__path__[0], pat))):
            try:
                ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


class Ears:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.used = time.time()
        self.lock = threading.Lock()

    def load(self):
        with self.lock:
            if self.model is None:
                _preload_cuda()
                from faster_whisper import WhisperModel
                t = time.time()
                self.model = WhisperModel(self.cfg.model, device=self.cfg.device,
                                          compute_type=self.cfg.compute_type)
                log.info("%s loaded in %.1fs", self.cfg.model, time.time() - t)
        return self.model

    def text(self, pcm: bytes) -> str:
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.used = time.time()
        t = time.time()
        segs, _ = self.load().transcribe(audio, language=self.cfg.language or None, beam_size=1,
                                         vad_filter=False, condition_on_previous_text=False)
        segs = [s for s in segs if s.no_speech_prob < 0.6]
        out = " ".join(s.text.strip() for s in segs).strip()
        log.info("heard %.1fs in %.2fs: %r", len(pcm) / 2 / RATE, time.time() - t, out)
        return "" if out.lower().strip(" .!?,") in HALLUCINATED else out

    def maybe_unload(self) -> None:
        idle = self.cfg.idle_unload_min * 60
        if self.model is not None and idle and time.time() - self.used > idle:
            with self.lock:
                self.model = None
            import gc
            gc.collect()
            log.info("model unloaded after %d idle min", self.cfg.idle_unload_min)


# -- the listener ------------------------------------------------------------------------------

class Listener:
    def __init__(self, cfg):
        from hearthsmith.sinks import SpriteSink
        from hearthsmith.voice import Voice
        self.full = cfg
        self.cfg = cfg.ear
        self.ears = Ears(cfg.ear)
        self.voice = Voice(cfg.voice)
        self.sprite = SpriteSink(cfg.sprite_path)
        self.state = "idle"               # idle | listening | thinking | speaking
        self.conversation = False
        self.cancel = threading.Event()
        self.session: threading.Thread | None = None

    # commands, from the socket
    def command(self, cmd: str) -> dict:
        if cmd == "listen":
            if self.state == "speaking":
                self.voice.interrupt()          # the session loop listens next
            elif self.state == "listening" and not self.conversation:
                self.cancel.set()
            elif not self._busy():
                self._start(conversation=False)
        elif cmd == "talk":
            if self.conversation:
                self._end()
            else:
                self.conversation = True
                if not self._busy():
                    self._start(conversation=True)
        elif cmd in ("stop", "off"):
            self._end()
        return {"state": self.state, "conversation": self.conversation,
                "model_loaded": self.ears.model is not None}

    def _busy(self) -> bool:
        return self.session is not None and self.session.is_alive()

    def _start(self, conversation: bool) -> None:
        self.conversation = conversation
        self.cancel.clear()
        self.session = threading.Thread(target=self._session, daemon=True)
        self.session.start()

    def _end(self) -> None:
        self.conversation = False
        self.cancel.set()
        self.voice.interrupt()

    def _show(self, text: str, state: str = "idle", instant: bool = True) -> None:
        self.sprite.write(state, text, "ignorable", instant=instant)

    def _session(self) -> None:
        mic = None
        try:
            self.ears.load()
            mic = Mic(self.cfg.source)
            chime("message")
            mic.calibrate()
            seed = None
            first = True
            while not self.cancel.is_set():
                self.state = "listening"
                self._show("( listening… )" if first or not self.conversation else "( go on… )")
                wait = self.cfg.wait_s if first or not self.conversation else self.cfg.conversation_idle_s
                pcm = utterance(mic, wait, self.cfg.end_silence_s, self.cancel, seed)
                seed, first = None, False
                if pcm is None:
                    break
                self.state = "thinking"
                self._show("…", "forge")
                said = self.ears.text(pcm)
                if not said:
                    if self.conversation:
                        continue
                    break
                if ENDING.match(said):
                    self._show("Aye.")
                    self.voice.speak("Aye.")
                    break
                self._show(f"“{said}”")
                reply = self._route(said)
                self.state = "speaking"
                self._show(reply.text, "alert" if reply.intent == "nag" else "idle", instant=False)
                mic.drain()
                seed = self._speak(reply.text, mic)
                if seed is None and not self.conversation:
                    break                      # one turn, unless you talked over him
        except Exception:
            log.exception("listening failed")
            self._show("My ears are ringing — couldn't hear you.")
        finally:
            if mic:
                mic.close()
            self.state = "idle"
            self.conversation = False
            if not self.cancel.is_set():
                chime("dialog-information")
            self.cancel.clear()

    def _route(self, said: str):
        from hearthsmith.route import Reply, route
        try:
            return route(said, self.full, quick=self.cfg.quick_replies, ack=self._ack)
        except Exception as e:
            log.exception("route failed")
            return Reply("error", f"Couldn't do that: {type(e).__name__}.")

    def _ack(self, line: str) -> None:
        """A short "on it" while the real work runs; the answer waits for it to finish."""
        self._show(line, "forge")
        self._acking = threading.Thread(target=self.voice.speak, args=(line,), daemon=True)
        self._acking.start()

    def _speak(self, text: str, mic: Mic) -> list[bytes] | None:
        """Speak, watching the mic: loud, sustained speech over him cuts him off and becomes
        the start of your next turn (returned). A headset leaks little, but the bar is higher
        than for a normal turn so his own voice and a cough don't count."""
        if (a := getattr(self, "_acking", None)) is not None:
            a.join(timeout=10)
            self._acking = None
        done = threading.Event()

        def say():
            try:
                self.voice.speak(text)
            except Exception:
                log.exception("speak failed")
            finally:
                done.set()
        threading.Thread(target=say, daemon=True).start()
        if not self.cfg.barge_in:
            done.wait()
            return None
        need = int(self.cfg.barge_in_ms / FRAME_MS)
        pre: collections.deque[bytes] = collections.deque(maxlen=need + 15)
        loud = 0
        thresh = mic.threshold(strict=self.cfg.barge_in_strict)
        while not done.is_set() and not self.cancel.is_set():
            f = mic.frame(timeout=0.2)
            if f is None:
                continue
            pre.append(f)
            loud = loud + 1 if rms(f) > thresh else 0
            if loud >= need:
                log.info("barge-in")
                self.voice.interrupt()
                done.wait(2)
                return list(pre)
        return None

    def housekeeping(self) -> None:
        while True:
            time.sleep(60)
            if not self._busy():
                self.ears.maybe_unload()


def serve() -> None:
    from hearthsmith import config
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.load_env()
    cfg = config.load()
    lst = Listener(cfg)
    if cfg.ear.preload:
        threading.Thread(target=lst.ears.load, daemon=True).start()
    threading.Thread(target=lst.housekeeping, daemon=True).start()
    SOCK.parent.mkdir(parents=True, exist_ok=True)
    SOCK.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    os.chmod(SOCK, 0o600)
    srv.listen(4)
    log.info("listening for commands on %s (mic source: %s)", SOCK, cfg.ear.source or "default")
    while True:
        conn, _ = srv.accept()
        with conn:
            try:
                cmd = conn.makefile().readline().strip()
                conn.sendall((json.dumps(lst.command(cmd)) + "\n").encode())
            except OSError:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(prog="hearthsmith-ear")
    ap.add_argument("cmd", choices=["serve", "listen", "talk", "stop", "status"])
    a = ap.parse_args()
    if a.cmd == "serve":
        serve()
        return
    r = send(a.cmd)
    print(json.dumps(r))
    raise SystemExit(1 if "error" in r else 0)


if __name__ == "__main__":
    main()
