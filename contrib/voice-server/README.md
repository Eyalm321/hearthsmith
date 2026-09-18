# hearthsmith voice server

Warm Qwen3-TTS-0.6B-Base voice-clone server on `127.0.0.1:7861`. hearthsmith's `qwen` engine
(`voice.engines`) posts `{text, ref, ref_text}` and gets a wav back.

```sh
mkdir -p ~/dev/hearthsmith-voice && cd ~/dev/hearthsmith-voice
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python qwen-tts soundfile pocket-tts
cp ~/dev/hearthsmith/contrib/voice-server/hearthsmith-voice.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now hearthsmith-voice.service
curl -s localhost:7861/api/health
```

fp32 (`FORGE_VOICE_MODEL`, default 0.6B-Base): the 2080 Ti has no bf16 and fp16 produces NaN
in the code predictor. ~4 GiB resident, ~5.3 GiB peak, ~6s a line; unloads after
`FORGE_VOICE_IDLE` seconds (900) so the GPU is free between nags. Weights are fetched into the
HF cache on first load (~2.4 GB).
