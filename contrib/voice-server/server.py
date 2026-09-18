"""Warm Qwen3-TTS voice-clone server for forge. HTTP on localhost:
  POST /api/tts  {"text": "...", "ref": "/abs/clip.wav", "ref_text": "transcript", "language": "English"}
    -> audio/wav
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
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

MODEL = os.environ.get("FORGE_VOICE_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")
IDLE_SEC = int(os.environ.get("FORGE_VOICE_IDLE", "900"))
PORT = int(os.environ.get("FORGE_VOICE_PORT", "7861"))

_lock = threading.Lock()
_state = {"model": None, "last": 0.0}


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
                torch.cuda.empty_cache()
                print("[voice] unloaded (idle)", flush=True)


class TTSRequest(BaseModel):
    text: str
    ref: str
    ref_text: str
    language: str = "English"


app = FastAPI(title="forge voice (Qwen3-TTS clone)")


@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL, "loaded": _state["model"] is not None}


@app.post("/api/tts")
def tts(req: TTSRequest):
    if not req.text.strip():
        return JSONResponse(status_code=400, content={"error": "text is required"})
    if not os.path.exists(req.ref):
        return JSONResponse(status_code=400, content={"error": f"ref not found: {req.ref}"})
    model = ensure()
    with _lock:  # one GPU, one job at a time
        t = time.time()
        wavs, sr = model.generate_voice_clone(text=req.text, language=req.language,
                                              ref_audio=req.ref, ref_text=req.ref_text)
        print(f"[voice] {len(wavs[0]) / sr:.1f}s audio in {time.time() - t:.1f}s: {req.text[:60]!r}",
              flush=True)
    buf = io.BytesIO()
    sf.write(buf, wavs[0], sr, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    threading.Thread(target=unloader, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
