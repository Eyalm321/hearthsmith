"""Warm voice-clone server for hearthsmith. HTTP on localhost:
  POST /api/stream {"text": "...", "ref": "/abs/clip.wav"}
    -> chunked raw PCM s16le mono 24 kHz, first chunk ~0.2s in. Pocket TTS (Kyutai, 100M) on
       2 CPU cores, faster than realtime; the reference clip is all it needs.
  POST /api/tts  {"text": "...", "ref": "/abs/clip.wav", "ref_text": "transcript", "language": "English"}
    -> audio/wav. Qwen3-TTS-0.6B on the GPU, whole utterance, ~RTF 1.5 here. Kept for A/B.
  GET  /api/health

fp32 on purpose: the 2080 Ti has no bf16 and fp16 overflows to NaN in the code predictor.
~4 GiB resident, ~5.3 GiB peak. Model lazy-loads on first request (~3s) and unloads after
IDLE_SEC idle so the GPU is free for other things between nags."""
import io
import os
import threading
import time

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

MODEL = os.environ.get("FORGE_VOICE_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")
IDLE_SEC = int(os.environ.get("FORGE_VOICE_IDLE", "900"))
PORT = int(os.environ.get("FORGE_VOICE_PORT", "7861"))
POCKET_THREADS = int(os.environ.get("FORGE_VOICE_POCKET_THREADS", "2"))

_lock = threading.Lock()
_state = {"model": None, "last": 0.0}
# encoded reference clips: (ref path, mtime, ref_text) -> voice_clone_prompt. Encoding the 16s
# clip is ~1.7s of every call otherwise, and the clip never changes between nags.
_prompts: dict[tuple, object] = {}

# pocket: tiny, CPU, stays loaded; its reference state (~3s to compute) cached per clip
_pocket_lock = threading.Lock()
_pocket = {"model": None, "states": {}}


def ensure_pocket():
    with _pocket_lock:
        if _pocket["model"] is None:
            from pocket_tts import TTSModel
            torch.set_num_threads(POCKET_THREADS)
            t = time.time()
            _pocket["model"] = TTSModel.load_model()
            print(f"[voice] loaded pocket-tts in {time.time() - t:.1f}s", flush=True)
        return _pocket["model"]


def pocket_state(model, ref: str):
    key = (ref, os.path.getmtime(ref))
    if key not in _pocket["states"]:
        t = time.time()
        _pocket["states"][key] = model.get_state_for_audio_prompt(ref)
        print(f"[voice] encoded {ref} in {time.time() - t:.1f}s", flush=True)
    return _pocket["states"][key]


def ensure():
    with _lock:
        if _state["model"] is None:
            from qwen_tts import Qwen3TTSModel
            t = time.time()
            _state["model"] = Qwen3TTSModel.from_pretrained(
                MODEL, device_map="cuda:0", dtype=torch.float32, attn_implementation="sdpa")
            print(f"[voice] loaded {MODEL} in {time.time() - t:.1f}s", flush=True)
        _state["last"] = time.time()
        return _state["model"]


def unloader():
    while True:
        time.sleep(30)
        with _lock:
            if _state["model"] is not None and time.time() - _state["last"] > IDLE_SEC:
                _state["model"] = None
                _prompts.clear()
                torch.cuda.empty_cache()
                print("[voice] unloaded (idle)", flush=True)


class TTSRequest(BaseModel):
    text: str
    ref: str
    ref_text: str
    language: str = "English"


class StreamRequest(BaseModel):
    text: str
    ref: str


app = FastAPI(title="hearthsmith voice (Qwen3-TTS clone)")


@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL, "loaded": _state["model"] is not None,
            "pocket_loaded": _pocket["model"] is not None}


@app.post("/api/stream")
def stream(req: StreamRequest):
    if not req.text.strip():
        return JSONResponse(status_code=400, content={"error": "text is required"})
    if not os.path.exists(req.ref):
        return JSONResponse(status_code=400, content={"error": f"ref not found: {req.ref}"})
    model = ensure_pocket()
    state = pocket_state(model, req.ref)

    def pcm():
        with _pocket_lock:  # 2 threads; two lines at once would both stutter
            t, n = time.time(), 0
            for chunk in model.generate_audio_stream(state, req.text):
                n += chunk.numel()
                yield (chunk.clamp(-1, 1) * 32767).to(torch.int16).numpy().tobytes()
            print(f"[voice] pocket {n / model.sample_rate:.1f}s audio in {time.time() - t:.1f}s: "
                  f"{req.text[:60]!r}", flush=True)

    return StreamingResponse(pcm(), media_type="audio/L16; rate=24000; channels=1",
                             headers={"X-Sample-Rate": str(model.sample_rate)})


@app.post("/api/tts")
def tts(req: TTSRequest):
    if not req.text.strip():
        return JSONResponse(status_code=400, content={"error": "text is required"})
    if not os.path.exists(req.ref):
        return JSONResponse(status_code=400, content={"error": f"ref not found: {req.ref}"})
    model = ensure()
    with _lock:  # one GPU, one job at a time
        t = time.time()
        key = (req.ref, os.path.getmtime(req.ref), req.ref_text)
        if key not in _prompts:
            _prompts[key] = model.create_voice_clone_prompt(ref_audio=req.ref, ref_text=req.ref_text)
        wavs, sr = model.generate_voice_clone(text=req.text, language=req.language,
                                              voice_clone_prompt=_prompts[key])
        print(f"[voice] {len(wavs[0]) / sr:.1f}s audio in {time.time() - t:.1f}s: {req.text[:60]!r}",
              flush=True)
    buf = io.BytesIO()
    sf.write(buf, wavs[0], sr, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    threading.Thread(target=unloader, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
