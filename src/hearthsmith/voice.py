"""His voice. Zero-shot clone: the engine hears the reference clip and says the text in that
voice. Nothing is trained, nothing is stored but wavs — the clip in assets/voice IS the voice.

synth(text) -> [wav paths]  (cached by text+clip+engine, so repeated lines cost nothing)
speak(text) -> bool         (synth, then play through PipeWire; never raises)

Engines (see VoiceCfg.engines): AuK on the HF space when its quota allows, the local Qwen3-TTS
server otherwise. Same clip into both, so he stays the same dwarf either way.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from hearthsmith.config import VoiceCfg

log = logging.getLogger("hearthsmith.voice")

# the space's zero-shot TTS template; other tasks (edit, denoise) use other phrasings
AUK_INSTRUCTION = 'Say the following with the same voice: "{text}"'
AUK_API = "/run_generate_with_pe"
_SENT = re.compile(r"(?<=[.!?…])\s+")


def chunk(text: str, wps: float, max_s: float) -> list[str]:
    """Sentences packed into chunks the model can say in one breath. A 40-word nag squeezed
    into 14s comes out rushed; two 7s halves come out like him."""
    budget = max(1, int(max_s * wps))
    out, cur, n = [], [], 0
    for s in _SENT.split(text.strip()):
        w = len(s.split())
        if cur and n + w > budget:
            out.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        out.append(" ".join(cur))
    return [c for c in out if c]


def seconds_for(text: str, wps: float, max_s: float) -> float:
    return round(min(max_s, max(1.5, len(text.split()) / wps + 0.6)), 1)


class Voice:
    def __init__(self, cfg: VoiceCfg):
        self.cfg = cfg
        self._auk_client = None
        self.engines = list(cfg.engines)  # shrinks as engines refuse; empty = he's mute this run

    def available(self) -> bool:
        return self.cfg.enabled and self.cfg.ref.exists() and bool(self.engines)

    def _key(self, text: str, engine: str) -> str:
        h = hashlib.sha1()
        h.update(self.cfg.ref.read_bytes())
        tag = f"auk:{self.cfg.variant}:{self.cfg.seed}" if engine == "auk" else engine
        h.update(f"|{tag}|{text}".encode())
        return h.hexdigest()[:20]

    # -- engines -------------------------------------------------------------------------------

    def _auk(self, part: str, out: Path) -> None:
        from gradio_client import Client, handle_file
        from huggingface_hub import get_token

        if self._auk_client is None:
            # env first, then `hf auth login`'s cache: anonymous ZeroGPU runs out after ~3 lines
            token = os.environ.get(self.cfg.hf_token_env) or get_token() or None
            self._auk_client = Client(self.cfg.space, token=token, verbose=False,
                                      download_files=str(self.cfg.cache_dir),
                                      analytics_enabled=False)
        got = self._auk_client.predict(
            use_pe=False,
            variant=self.cfg.variant,
            audio=handle_file(str(self.cfg.ref)),
            instruction=AUK_INSTRUCTION.format(text=part),
            gen_seconds=seconds_for(part, self.cfg.words_per_second, self.cfg.max_chunk_seconds),
            nfe=32, cfg=2.0, seed=self.cfg.seed,
            api_name=AUK_API,
        )
        shutil.move(got, out)
        shutil.rmtree(Path(got).parent, ignore_errors=True)  # gradio's per-call dir

    def _pocket_stream(self, text: str) -> bool:
        """The realtime path: PCM chunks go from the server straight into the player as they
        are made (first sound ~0.2s), and are tee'd into the cache so a repeat is instant."""
        out = self.cfg.cache_dir / f"{self._key(text, 'pocket')}.wav"
        if out.exists():
            return play(out, self.cfg.timeout_seconds, self.cfg.sink)
        player = raw_player(self.cfg.sink)
        if player is None:
            return False
        pcm = bytearray()
        with httpx.stream("POST", f"{self.cfg.qwen_url}/api/stream", timeout=self.cfg.timeout_seconds,
                          json={"text": text, "ref": str(self.cfg.ref)}) as r:
            r.raise_for_status()
            with subprocess.Popen(player, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) as proc:
                try:
                    for b in r.iter_bytes():
                        proc.stdin.write(b)
                        pcm += b
                finally:
                    proc.stdin.close()
                    proc.wait(timeout=self.cfg.timeout_seconds)
        write_wav(out, bytes(pcm), 24000)
        return proc.returncode == 0

    def _pocket(self, part: str, out: Path) -> None:
        """Non-streaming variant for `synth` (--no-play): same endpoint, whole body."""
        r = httpx.post(f"{self.cfg.qwen_url}/api/stream", timeout=self.cfg.timeout_seconds,
                       json={"text": part, "ref": str(self.cfg.ref)})
        r.raise_for_status()
        write_wav(out, r.content, 24000)

    def _qwen(self, part: str, out: Path) -> None:
        r = httpx.post(f"{self.cfg.qwen_url}/api/tts", timeout=self.cfg.timeout_seconds,
                       json={"text": part, "ref": str(self.cfg.ref),
                             "ref_text": self.cfg.ref_text.read_text().strip(),
                             "language": self.cfg.qwen_language})
        r.raise_for_status()
        out.write_bytes(r.content)

    # -- pipeline ------------------------------------------------------------------------------

    def synth(self, text: str) -> list[Path]:
        """One wav per chunk, in order. Raises when every engine refused."""
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        return [self._one(part) for part in
                chunk(text, self.cfg.words_per_second, self.cfg.max_chunk_seconds)]

    def _one(self, part: str) -> Path:
        # a line any engine already said is good enough; don't spend a call to say it again
        for eng in self.engines:
            out = self.cfg.cache_dir / f"{self._key(part, eng)}.wav"
            if out.exists():
                return out
        last: Exception | None = None
        while self.engines:
            eng = self.engines[0]
            out = self.cfg.cache_dir / f"{self._key(part, eng)}.wav"
            try:
                getattr(self, f"_{eng}")(part, out)
                return out
            except Exception as e:  # noqa: BLE001 — quota, queue, server down: "not this one, not now"
                last = e
                log.warning("voice: %s refused (%s)", eng, str(e)[:120])
                self.engines.pop(0)
        raise RuntimeError(f"no engine could speak: {last}")

    def speak(self, text: str) -> bool:
        if not self.available():
            return False
        # pocket streams the whole line itself; no chunking, no pipeline needed
        if self.engines[0] == "pocket":
            try:
                return self._pocket_stream(text)
            except Exception as e:  # noqa: BLE001 — server down: fall through to the next engine
                log.warning("voice: pocket refused (%s)", str(e)[:120])
                self.engines.pop(0)
                if not self.engines:
                    return False
        return self._speak_chunked(text)

    def _chunk_seconds(self) -> float:
        # AuK costs ~20s a call regardless of length, so pack sentences; the local engine is
        # per-token, so short chunks mean the first one is playing while the rest is made
        return self.cfg.max_chunk_seconds if self.engines[:1] == ["auk"] else self.cfg.stream_chunk_seconds

    def _speak_chunked(self, text: str) -> bool:
        """Pipelined: chunk N+1 is synthesized while chunk N plays. Time to first sound is one
        short sentence, not the whole line."""
        parts = chunk(text, self.cfg.words_per_second, self._chunk_seconds())
        if not parts:
            return False
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        ok = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self._one, parts[0])
            for nxt in [*parts[1:], None]:
                try:
                    wav = pending.result()
                except Exception as e:  # noqa: BLE001 — nag goes on without sound
                    log.warning("voice: synth failed: %s", e)
                    return False
                if nxt is not None:
                    pending = pool.submit(self._one, nxt)
                ok = play(wav, self.cfg.timeout_seconds, self.cfg.sink) and ok
        return ok


def write_wav(out: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def raw_player(sink: str = "") -> list[str] | None:
    """Command that plays s16le mono 24 kHz from stdin."""
    if shutil.which("pw-play"):
        return ["pw-play", *(["--target", sink] if sink else []),
                "--raw", "--rate", "24000", "--channels", "1", "--format", "s16", "-"]
    if shutil.which("paplay"):
        return ["paplay", *(["--device", sink] if sink else []),
                "--raw", "--rate=24000", "--channels=1", "--format=s16le"]
    return None


def play(wav: Path, timeout: int, sink: str = "") -> bool:
    """Blocking on purpose: hearthsmithd is a oneshot unit, a backgrounded player dies with it."""
    players = [(["pw-play"], ["--target", sink]), (["paplay"], ["--device", sink]),
               (["aplay", "-q"], [])]
    for cmd, target in players:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run([*cmd, *(target if sink else []), str(wav)], check=True, timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("voice: %s failed: %s", cmd[0], e)
    return False
