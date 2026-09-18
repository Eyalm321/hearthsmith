"""His voice. Zero-shot clone: AuK hears the reference clip and says the text in that voice.
Nothing is trained, nothing is stored but wavs — the clip in assets/voice IS the voice.

synth(text) -> [wav paths]  (cached by text+clip, so repeated lines cost nothing)
speak(text) -> bool         (synth, then play through PipeWire; never raises)
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

    def available(self) -> bool:
        return self.cfg.enabled and self.cfg.ref.exists()

    def _key(self, text: str) -> str:
        h = hashlib.sha1()
        h.update(self.cfg.ref.read_bytes())
        h.update(f"|{self.cfg.variant}|{self.cfg.seed}|{text}".encode())
        return h.hexdigest()[:20]

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
        from gradio_client import handle_file

        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        wavs = []
        for part in chunk(text, self.cfg.words_per_second, self.cfg.max_chunk_seconds):
            out = self.cfg.cache_dir / f"{self._key(part)}.wav"
            if not out.exists():
                got = self._connect().predict(
                    use_pe=False,
                    variant=self.cfg.variant,
                    audio=handle_file(str(self.cfg.ref)),
                    instruction=INSTRUCTION.format(text=part),
                    gen_seconds=seconds_for(part, self.cfg.words_per_second,
                                            self.cfg.max_chunk_seconds),
                    nfe=32, cfg=2.0, seed=self.cfg.seed,
                    api_name=API,
                )
                shutil.move(got, out)
                shutil.rmtree(Path(got).parent, ignore_errors=True)  # gradio's per-call dir
            wavs.append(out)
        return wavs

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
