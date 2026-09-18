"""His voice. Zero-shot clone: AuK hears the reference clip and says the text in that voice.
Nothing is trained, nothing is stored but wavs — the clip in assets/voice IS the voice.

synth(text) -> [wav paths]  (cached by text+clip, so repeated lines cost nothing)
speak(text) -> bool         (synth, then play through PipeWire; never raises)

Two engines, same clip: AuK on the HF space is the voice we want; the free ZeroGPU quota runs
out after a handful of lines, so the local Chatterbox server (~/dev/voice-clone) takes over
until it comes back. Both are zero-shot from the same wav, so he stays the same dwarf.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from forge.config import VoiceCfg

log = logging.getLogger("forge.voice")

# the space's zero-shot TTS template; other tasks (edit, denoise) use other phrasings
INSTRUCTION = 'Say the following with the same voice: "{text}"'
API = "/run_generate_with_pe"
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
        self._client = None
        self._fb_client = None
        self.engine = "auk"  # what actually spoke last; "chatterbox" once AuK refused

    def available(self) -> bool:
        return self.cfg.enabled and self.cfg.ref.exists()

    def _key(self, text: str, engine: str = "auk") -> str:
        h = hashlib.sha1()
        h.update(self.cfg.ref.read_bytes())
        tag = self.cfg.variant if engine == "auk" else engine
        h.update(f"|{tag}|{self.cfg.seed}|{text}".encode())
        return h.hexdigest()[:20]

    def _fallback(self):
        if self._fb_client is None and self.cfg.fallback_url:
            from gradio_client import Client

            self._fb_client = Client(self.cfg.fallback_url, verbose=False,
                                     download_files=str(self.cfg.cache_dir), analytics_enabled=False)
        return self._fb_client

    def _connect(self):
        if self._client is None:
            from gradio_client import Client  # slow import; only when he actually speaks
            from huggingface_hub import get_token

            # env first, then `hf auth login`'s cache: anonymous ZeroGPU runs out after ~3 lines
            token = os.environ.get(self.cfg.hf_token_env) or get_token() or None
            self._client = Client(self.cfg.space, token=token, verbose=False,
                                  download_files=str(self.cfg.cache_dir), analytics_enabled=False)
        return self._client

    def synth(self, text: str) -> list[Path]:
        """One wav per chunk, in order. Empty list = could not synthesize."""
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        wavs = []
        for part in chunk(text, self.cfg.words_per_second, self.cfg.max_chunk_seconds):
            out = self.cfg.cache_dir / f"{self._key(part)}.wav"
            if out.exists():
                wavs.append(out)
                continue
            if self.engine == "auk":
                try:
                    self._store(self._auk(part), out)
                    wavs.append(out)
                    continue
                except Exception as e:  # quota, queue, space asleep: all mean "not now"
                    if not self.cfg.fallback_url:
                        raise
                    log.warning("voice: AuK refused (%s); falling back to chatterbox", str(e)[:120])
                    self.engine = "chatterbox"
            out = self.cfg.cache_dir / f"{self._key(part, 'chatterbox')}.wav"
            if not out.exists():
                self._store(self._chatterbox(part), out)
            wavs.append(out)
        return wavs

    def _auk(self, part: str) -> str:
        from gradio_client import handle_file

        return self._connect().predict(
            use_pe=False,
            variant=self.cfg.variant,
            audio=handle_file(str(self.cfg.ref)),
            instruction=INSTRUCTION.format(text=part),
            gen_seconds=seconds_for(part, self.cfg.words_per_second, self.cfg.max_chunk_seconds),
            nfe=32, cfg=2.0, seed=self.cfg.seed,
            api_name=API,
        )

    def _chatterbox(self, part: str) -> str:
        from gradio_client import handle_file

        return self._fallback().predict(
            text=part, ref_override=handle_file(str(self.cfg.ref)),
            exaggeration=self.cfg.fallback_exaggeration, cfg_weight=self.cfg.fallback_cfg_weight,
            api_name="/ui_synth",
        )

    @staticmethod
    def _store(got: str, out: Path) -> None:
        shutil.move(got, out)
        shutil.rmtree(Path(got).parent, ignore_errors=True)  # gradio's per-call dir

    def speak(self, text: str) -> bool:
        if not self.available():
            return False
        try:
            wavs = self.synth(text)
        except Exception as e:  # noqa: BLE001 — the space is down or queued out; nag goes on without sound
            log.warning("voice: synth failed: %s", e)
            return False
        return bool(wavs) and all(play(w, self.cfg.timeout_seconds, self.cfg.sink) for w in wavs)


def play(wav: Path, timeout: int, sink: str = "") -> bool:
    """Blocking on purpose: forged is a oneshot unit, a backgrounded player dies with it."""
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
