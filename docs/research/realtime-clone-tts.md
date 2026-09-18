# Realtime zero-shot voice-cloning TTS for a 2080 Ti (fp32-only) or CPU

Date: 2026-09-18. Target: a desktop assistant speaking 1–3 English sentences in a voice cloned
from one 16 s reference clip (transcript available). Box: Ryzen-class x86, Linux/PipeWire, RTX
2080 Ti (Turing, 11 GiB, ~6 GiB free; no bf16, fp16 NaN-prone, fp32 works).

Every number below was fetched on the date above from the owner's README, model card, paper, or
issue tracker; the URL next to the claim is the citation. "n/a" means the primary source has no
number. Nothing here is a benchmark run on this machine except the three facts carried over from
the caller's context (Qwen3-TTS fp32 RTF ≈ 1.5; Chatterbox 3–6 s/line; AuK needs the HF Space).

## TL;DR

- **Pick Pocket TTS (Kyutai, 100M, MIT code / CC-BY-4.0 weights).** It is the only candidate whose
  owner documents CPU-first design, ~200 ms time-to-first-chunk, a real streaming generator
  (`generate_audio_stream`), and clone-from-a-plain-wav with no transcript
  (https://github.com/kyutai-labs/pocket-tts). Measured by the authors: "~6x real-time on a CPU of
  MacBook Air M4", "RTF ~2.3-2.5x on CPU vs. ~6.28x on GPU" on a 4-vCPU x86 VM + Tesla T4. It is
  fp32 by construction, so Turing's bf16/fp16 problem does not exist. Two costs: the cloning weights
  are a gated HF repo (accept terms), and no owner-published MOS/WER exists.
- **Same model, zero-Python path:** sherpa-onnx ships an int8 ONNX Pocket TTS bundle with a
  per-chunk callback; the maintainer's PR logs show RTF 0.15–0.27 with `num_threads=2`
  (https://github.com/k2-fsa/sherpa-onnx/pull/3083). sherpa-onnx caps the reference at the first
  10 s — your 16 s clip is fine, the tail is ignored.
- **GPU runner-up: Chatterbox-Turbo (350M, MIT).** Single-step mel decoder, default code path is
  fp32 (no `float16`/`bfloat16` anywhere in `tts_turbo.py`), clone via `audio_prompt_path`. No
  official streaming (PR #528 open since 2026-06-15) and no owner RTF — the caller's 3–6 s/line was
  the *original* 500M model, Turbo should be faster but that is unmeasured
  (https://github.com/resemble-ai/chatterbox).
- **NAR flow-matching alternative: ZipVoice-Distill (123M, Apache-2.0)**, paper RTF 0.0125 on H20
  and 1.22 on one Xeon thread (4 NFE) (https://arxiv.org/html/2506.13053v3); sherpa-onnx int8 bundle
  RTF 0.205 at 4 threads (https://github.com/k2-fsa/sherpa-onnx/pull/3332). Needs the transcript.
  Whole-utterance, so TTFA ≈ RTF × line length (~0.6 s for a 3 s line).
- **CPU fallback ranking:** Pocket TTS (pip or sherpa-onnx) → ZipVoice-Distill int8 (sherpa-onnx) →
  NeuTTS-Nano Q4 GGUF (llama.cpp, streaming, needs transcript) → Chatterbox-Nano ("3x realtime on
  8 cores", no streaming).
- **Rule out immediately:** AuK/AuK-Flash (16.75 GiB even with offload, bf16), Fish S2-Pro (24 GB),
  Kyutai TTS-1.6B (cloning locked to shipped embeddings), VibeVoice-Realtime (tokenizer withheld),
  Zonos (bf16 hard-coded), Dia (no CPU, fp32 = 1.0x on a 4090), Orpheus (3B + vLLM, cloning "not
  explicitly trained"), Kokoro/Kitten/Supertonic/Piper (no cloning).
- **Qwen3-TTS stays out** for this box: upstream package is offline/batch only, streaming is
  delegated to vLLM-Omni (bf16/fp16), the maintainer's own advice for non-bf16 GPUs is fp32
  (https://github.com/QwenLM/Qwen3-TTS/issues/43), and the two community fast paths are 5090/cu130 or
  Windows-only.

## Comparison table

RTF = wall / audio (lower is faster). "TTFA" = time to first audio. Numbers are the owner's unless
marked †(community, same repo's issue tracker) or ‡(derived here, not a measurement).

| Model | Params | License | Clone input | CPU RTF (hardware) | GPU RTF (hardware) | Streaming / TTFA | dtype on Turing | 11 GiB fp32 fits? | Last push |
|---|---|---|---|---|---|---|---|---|---|
| Pocket TTS | 100M | MIT code, CC-BY-4.0 weights (gated) | wav only | ~0.17 (M4 Air, "~6x"); ~0.40–0.43 (4-vCPU x86 VM, "2.3–2.5x") | ~0.16 (T4, "6.28x") | yes, generator; "~200ms" first chunk | fp32 native | yes (~0.4 GB) | 2026-09-18 |
| Pocket TTS via sherpa-onnx int8 | 100M | Apache-2.0 runtime | wav only, first 10 s used | 0.15–0.27 @2 threads (dev Mac, CPU unspecified) | n/a | callback per chunk | int8 CPU | n/a | 2026-09-17 |
| Chatterbox-Turbo | 350M | MIT | wav (~10 s hint) | n/a | n/a | none official (PR #528 open) | fp32 default | yes | 2026-07-21 |
| Chatterbox-Nano | 110M | MIT | wav | "3x faster than realtime on 8 CPU cores" (≈0.33‡) | n/a | none | fp32 default | yes | 2026-07-21 |
| ZipVoice-Distill (4 NFE) | 123M | Apache-2.0 | wav + transcript, "<3 s" prompt advised | 1.22 (1 thread, Xeon 8457C, PyTorch) | 0.0125 (H20) | none (chunk by sentence) | fp32 | yes | 2025-12-02 |
| ZipVoice-Distill via sherpa-onnx int8 | 123M | Apache-2.0 | wav + transcript | 0.205 @4 threads (dev Mac) | n/a | callback per sentence | int8 | n/a | 2026-09-17 |
| LuxTTS (ZipVoice distill, 48 kHz) | ~123M‡ | Apache-2.0 | wav only, ≥3 s | "faster then realtime on CPU's" (no number) | "150x realtime on a single GPU" (unspecified) | none | fp32 ("Float16 should be significantly faster") | "Fits within 1gb vram" | 2026-06-05 |
| NeuTTS-Nano / Air (GGUF) | 120M / 360M active | NeuTTS Open License 1.0 / Apache-2.0 (gated) | wav + transcript, 3–15 s | LM only: 221 / 119 tok/s on Ryzen 9 HX 370 (50 Hz codec → ≈4.4x / 2.4x‡) | 19268 / 16194 tok/s (4090, vLLM) | yes, GGUF only | Q4/Q8 GGUF | yes | 2026-07-30 |
| F5-TTS v1 Base | 336M | MIT code, CC-BY-NC weights | wav + transcript (or ASR) | n/a (paper table: 37.3 single Xeon thread, 32 NFE) | 0.1467 PyTorch / 0.0402 TRT-LLM (L20) | chunk stream via socket_server | auto-fp16 on cc≥7 (Turing=7.5); pass `dtype` | yes | 2026-07-23 |
| CosyVoice2 / Fun-CosyVoice3-0.5B | 0.5B | Apache-2.0 | wav + transcript | n/a | n/a ("4x" vs HF with TRT-LLM) | `stream=True`; "latency as low as 150ms" (no hardware) | `fp16=False` default → fp32 | unverified | 2026-05-25 |
| VoxCPM1.5 / VoxCPM2 | 0.6B / 2B | Apache-2.0 | wav + transcript | n/a (llama.cpp-omni GGUF: 1.76 on M4 Pro Metal) | ~0.15 / ~0.30 (4090, bf16); ~0.08 / ~0.13 Nano-vLLM | `generate_streaming` | config `dtype: bfloat16`; maintainer: edit to float16 | ~6 / ~8 GB in bf16 → fp32 ≈ 2× (‡, likely no) | 2026-09-02 |
| XTTS-v2 (coqui-tts fork) | n/a on page | MPL-2.0 code, CPML weights (non-commercial) | wav ("3-second") | n/a | n/a | `inference_stream`; "< 200ms latency" (no hardware) | fp32 (no dtype in xtts.py) | yes | 2026-06-10 |
| Qwen3-TTS-0.6B-Base | 0.6B | Apache-2.0 | wav + transcript (or x-vector only) | †1.2–1.4 (Ryzen 8745HS) | caller: 1.5 fp32 on 2080 Ti; †5 s/1 s on 3060 | arch yes; package no; "97ms" (no hardware) | fp16 NaN (#43), fp32 recommended | yes | 2026-03-17 |
| Spark-TTS-0.5B | 0.5B | CC-BY-NC-SA weights | wav + transcript | n/a | 0.1362 (L20, TRT-LLM, c=1, 876 ms latency) | none | n/a | unverified | 2025-04-09 |
| IndexTTS-2.5 | 0.8B | bilibili Model Use License | wav | n/a | 0.2060 fp32 / 0.2065 bf16 (4090) | none | `use_bf16=True` default; fp32 works | †"12G不够" for v2 | 2026-08-18 |
| GPT-SoVITS v2ProPlus | n/a | MIT | wav (5 s zero-shot) | 0.526 (M4 CPU) | 0.028 (4060 Ti), 0.014 (4090) | not on README | `is_half` optional | yes | 2026-08-18 |
| OpenVoice V2 + MeloTTS | n/a | MIT | wav (tone colour only) | MeloTTS "CPU real-time" (no number) | n/a | none | fp32 | yes | 2025-04-19 |
| Kyutai TTS-1.6B (DSM) | 1.8B | CC-BY-4.0 | precomputed embeddings only | n/a | "75x" throughput (no hardware) | yes | n/a | n/a | 2026-01-26 |
| VibeVoice-Realtime-0.5B | 1.0B total | MIT | none ("Removed acoustic tokenizer") | n/a | n/a | "~300 ms (hardware dependent)" | BF16 weights | n/a | 2025-12 |
| Zonos-v0.1 | 1.6B | Apache-2.0 | wav (10–30 s) | "a lot slower" | "~2x" realtime (4090) → RTF 0.5 | none | `.to(device, torch.bfloat16)` hard-coded | 6 GB+ bf16 | 2025-03-05 |
| Dia-1.6B | 1.6B | Apache-2.0 | wav 5–10 s + transcript | "to be added soon" | fp32: 1.0x (4090, RTF 1.0); bf16: 2.1x | none | fp32 7.9 GB | yes, but RTF ≥1 | 2025-11-19 |
| Orpheus-3B | 3B | Apache-2.0 | text-speech pairs, "not explicitly trained" | llama.cpp doc | vLLM; "~200ms streaming latency" (no hardware) | yes | fp16/fp8 | no (3B) | 2025-12-05 |
| Sesame CSM-1B | 1B + decoder | Apache-2.0 | context Segments | n/a | n/a | none | n/a | unverified | 2025-05-27 |
| OuteTTS-1.0-1B | 1B | Apache-2.0 code | wav via `create_speaker` | n/a | L40S batched (image only) | none | GGUF FP16/quant | yes (GGUF) | 2026-03-23 |
| AuK / AuK-Flash | 1.5B + Qwen2.5-Omni-3B | MIT | wav | n/a | n/a ("4-step") | none | bf16 only documented; 16.75 GiB with offload (A800) | no | 2026-09-16 |
| Fish S2-Pro / OpenAudio S1-mini | 4B / 0.5B | Fish Audio Research License / CC-BY-NC-SA | wav 10–30 s | n/a | S2: 0.195, TTFA ~100 ms (H200); S1.5: "1:5 on 4060 laptop" | SGLang | `--half` for non-bf16 GPUs | S2: "at least 24GB" | 2026-09-16 |
| Kokoro-82M | 82M | Apache-2.0 | none | fast ONNX (no owner number) | n/a | none | fp32/ONNX | yes | 2025-08-06 |
| Kitten TTS | 15–80M | Apache-2.0 | none | qualitative only | n/a | none | ONNX | yes | 2026-08-19 |
| Supertonic 3 | ~99M | MIT code, OpenRAIL-M weights | none ("does not include an official voice-cloning pipeline") | 0.3 (Onyx Boox Go 6 e-reader) | n/a | none | ONNX | yes | archived 2026-09-09 |

## Per-candidate notes

### Pocket TTS (Kyutai)

- Repo https://github.com/kyutai-labs/pocket-tts; weights https://huggingface.co/kyutai/pocket-tts
  (gated: "You need to agree to share your contact information"; conditions forbid "voice
  impersonation or cloning without explicit and lawful consent"); a non-gated
  `kyutai/pocket-tts-without-voice-cloning` exists. Weights CC-BY-4.0, code MIT. Paper: CALM,
  https://arxiv.org/abs/2509.06926.
- "Small model size, 100M parameters", "Runs on CPU", "Uses only 2 CPU cores", "Low latency, ~200ms
  to get the first audio chunk", "Faster than real-time, ~6x real-time on a CPU of MacBook Air M4",
  "Audio streaming", "Can handle infinitely long text inputs" (README, HF card).
- x86 + GPU data point from the README "Running on GPU" section: "measured on a cloud x86 VM (4
  vCPUs) with a Tesla T4, moving the model to GPU gave a consistent ~2.6x speedup over CPU (RTF
  ~2.3-2.5x on CPU vs. ~6.28x on GPU, for both short and long input text)". Kyutai's "RTF" here is
  speed-up (audio/wall), i.e. RTF ≈ 0.40–0.43 CPU, ≈ 0.16 T4. GPU is "not officially supported
  (there is no `device` argument on `TTSModel.load_model()`)" — you `.to("cuda")` the module
  yourself. A T4 is Turing like the 2080 Ti, so that is the closest primary GPU number in this
  whole survey. PR #214 (closed, not merged) added H20 CPU 2.95x / H20 CUDA 7.79x
  (https://github.com/kyutai-labs/pocket-tts/pull/214).
- Clone: `get_state_for_audio_prompt("./my_reference.wav")` (path, `hf://`, or `.safetensors`
  export); `truncate: Whether to truncate long audio prompts to 30 seconds` (tts_model.py). No
  transcript. `export_model_state` caches the voice because `get_state_for_audio_prompt` "is
  relatively slow".
- Streaming API: `generate_audio_stream(model_state, text, ...)` → generator of 1-D tensors
  (https://kyutai-labs.github.io/pocket-tts/API%20Reference/python-api/). `sample_rate` "typically
  24000 Hz". `quantize=True` int8 "only works on CPU".
- Install: `pip install pocket-tts --extra-index-url https://download.pytorch.org/whl/cpu` (avoids
  ~3 GB CUDA wheels). Python 3.10–3.14, torch 2.5+. PyPI 3.1.0 on 2026-09-03; last push 2026-09-18.
- Quality: no WER/SIM/MOS on card, README, or in the fetched blog (page body is JS-only). Not on the
  TTS Arena V2 open-model rows fetched today (https://tts-agi-tts-arena-v2.hf.space/api/leaderboard).
  Known issues: "First word is garbled, smeared, or a tad deranged" (#91, open); no pauses via
  silence (#6); "torchao backend slower than torch.ao on x86 CPU" (#177, closed).
- Community fast paths (not owner numbers): audio.cpp GGML "3.22x faster than Python on CUDA", RTF
  0.021 (#203, hardware unnamed); sherpa-onnx below.

### Pocket TTS and ZipVoice inside sherpa-onnx (k2-fsa)

- Runtime https://github.com/k2-fsa/sherpa-onnx (Apache-2.0, PyPI 1.13.8 2026-09-10). Bundles at
  https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models:
  `sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2` 98 MB / fp32 168 MB;
  `sherpa-onnx-zipvoice-distill-int8-zh-en-emilia.tar.bz2` 109 MB / fp32 477 MB; vocoder
  `vocos_24khz.onnx` from the `vocoder-models` tag.
- Pocket: PR #3083 "Add C++ runtime and Python support PocketTTS for streaming voice cloning on
  CPU", merged 2026-01-26; logs `--num-threads=2`: "RTF: 1.088/4.028 = 0.270", "0.622/4.107 =
  0.151", "1.222/4.633 = 0.264"; "max_reference_audio_len is 10.000 seconds ... Only the first
  10.000 seconds are used" (developer's Mac, CPU model not stated). Docs: "PocketTTS does **not**
  require a reference transcript" (https://k2-fsa.github.io/sherpa/onnx/tts/pocket.html).
- ZipVoice: PR #2487 merged 2025-08-27 (https://github.com/k2-fsa/sherpa-onnx/pull/2487); callback
  support PR #3332 merged 2026-03-17 with log "Real-time factor (RTF): 1.614/7.861 = 0.205" at
  `--num-steps=4 --num-threads=4`. Docs: "ZipVoice requires both `--reference-audio` and
  `--reference-text`", and "If they do not match, the synthesized voice quality can degrade
  noticeably" (https://k2-fsa.github.io/sherpa/onnx/tts/zipvoice.html).
- Python examples with live playback via `sounddevice` and a `generated_audio_callback(samples,
  progress)`: `python-api-examples/pocket-tts-play.py`, `zipvoice-tts-play.py`; the example prints
  "Time in seconds to receive the first message" so TTFA is measurable out of the box.

### ZipVoice / ZipVoice-Distill (k2-fsa) and LuxTTS

- https://github.com/k2-fsa/ZipVoice, Apache-2.0, "only 123M parameters"; prompt advice "less than
  3 seconds for single-speaker speech generation"; `--num-steps` "as low as 4 (8 by default)" for
  Distill; ONNX CPU path `zipvoice.bin.infer_zipvoice_onnx --onnx-int8 True`, "Don't use ONNX on
  GPU". Last push 2025-12-02; PyPI `zipvoice` 0.1.0 2026-01-16.
- Paper Table II (https://arxiv.org/html/2506.13053v3): RTF GPU/CPU — F5-TTS (32 NFE, 336M)
  0.2958 / 37.284; ZipVoice (16 NFE) 0.0557 / 9.5529; ZipVoice-Distill (8 NFE) 0.0233 / 2.4177;
  ZipVoice-Distill (4 NFE) 0.0125 / 1.2202; "NVIDIA H20 GPU" and "a single thread of an Intel(R)
  Xeon(R) Platinum 8457C CPU", "3s prompt speech to generate 10s speech", Vocos vocoder.
- Paper Table I, Seed-TTS test-en (SIM-o / WER): ZipVoice 16 NFE 0.697 / 1.70; Distill 4 NFE
  0.679 / 1.64; F5-TTS 0.664 / 1.85; CosyVoice 2 0.652 / 2.57; Spark-TTS 0.584 / 1.98.
- LuxTTS: https://huggingface.co/YatharthS/LuxTTS (Apache-2.0, 2026-01-22) "Based on ZipVoice,
  distilled to 4steps", "48khz vocoder", "150x realtime on a single GPU and faster then realtime on
  CPU's as well", "Fits within 1gb vram". Repo https://github.com/ysharma3501/LuxTTS: `LuxTTS(...,
  device='cuda')` or `device='cpu', threads=2`; `encode_prompt(prompt_audio)` takes audio only (no
  transcript); "use at minimum a 3 second audio file"; currently float32, "Float16 should be
  significantly faster(almost 2x)" (unreleased); no streaming; no hardware named for any speed claim.

### Chatterbox family (Resemble)

- https://github.com/resemble-ai/chatterbox, MIT, PyPI `chatterbox-tts` 0.1.7 (2026-03-26), last
  push 2026-07-21. Table: Chatterbox 500M; Multilingual V3 500M/23 langs; **Turbo 350M** "Lower
  Compute and VRAM", decoder "reducing generation from 10 steps to just **one**"; **Nano 110M**
  "Runs on CPU (3x realtime on 8 cores)". The "sub 200ms" figure on the page is Resemble's paid
  API, not the open model.
- Clone: `audio_prompt_path="your_10s_ref_clip.wav"`; minimum length not stated. Perth watermark on
  every output.
- dtype: `src/chatterbox/tts_turbo.py` contains zero occurrences of `float16`/`bfloat16`/`half()`
  (grep today) → fp32 by default, which is exactly what Turing wants.
- Streaming: not in README; issues #34, #63, #314 open since 2025; PR #528 "Add Turbo streaming
  API" (2026-06-15, open) proposes `ChatterboxTurboTTS.stream()` "so that the user's GPU can remain
  free" with "live playback via PipeWire, PulseAudio, or ffplay"
  (https://github.com/resemble-ai/chatterbox/pull/528).
- Quality: TTS Arena V2 today lists "Chatterbox" at Elo 1479 ±19 (1797 votes), rank 28, flagged
  open=False on that board; Kokoro v1.0 1477 ±25
  (https://tts-agi-tts-arena-v2.hf.space/api/leaderboard). Owner eval is Podonos side-by-side
  images, no numbers in text.

### NeuTTS (Neuphonic)

- https://github.com/neuphonic/neutts; PyPI `neutts` 1.4.1 (2026-07-22). Air ~360M active (Apache
  2.0), Nano ~120M active (NeuTTS Open License 1.0); NeuCodec "50hz neural audio codec"; context
  "2048 tokens, enough for processing ~30 seconds of audio (including prompt duration)". HF repo
  `neuphonic/neutts-air` is gated.
- Clone needs "A reference audio sample (`.wav` file)" **and** its text; "3–15 seconds in length".
- CPU numbers (llama-bench, Q4_0, LM only, "do not include the Codec"): "AMD Ryzen 9 HX 370 (CPU
  only) | 119 tokens/s | 221 tokens/s" (Air | Nano), "14 threads for prefill and 16 threads for
  decode", "500 prefill tokens and generating 250 output tokens". ‡At 50 tokens/s of audio that is
  ≈2.4x (Air) / ≈4.4x (Nano) realtime before codec cost — derived here, not the owner's figure.
- Streaming: `tts.infer_stream(text, ref_codes, ref_text)` in `examples/basic_streaming_example.py`;
  "for streaming the model must be in GGUF format"; latency tips: GGUF backbone, pre-encode the
  reference (`encode_reference` → `.pt`), `neuphonic/neucodec-onnx-decoder`. Install `pip install
  neutts[all]`, Linux BLAS build `CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" pip install
  "neutts[llama]"`.

### F5-TTS (SWivid) and F5-TTS-ONNX

- https://github.com/SWivid/F5-TTS: code MIT, "pre-trained models are licensed under the CC-BY-NC
  license due to the training data Emilia"; `pip install f5-tts` (1.1.22, 2026-07-23). Benchmarks
  "on a single L20 GPU ... 16 NFE": Offline PyTorch batch 1 RTF 0.1467; TRT-LLM 0.0402; client-server
  253 ms / 0.0394.
- API: `F5TTS(model="F5TTS_v1_Base", device=None)`; `.infer(ref_file, ref_text, gen_text,
  nfe_step=32, ...)` (api.py). `utils_infer.load_checkpoint` picks `torch.float16` when `"cuda" in
  device and ... major >= 7` — Turing is sm_75, so **F5 will default to fp16 on the 2080 Ti**; pass
  `dtype=torch.float32` if you hit NaNs (no F5 fp16 NaN issue found for Turing in the tracker).
  Reference "will be **clip short to ~12s**"; 30 s max per generation.
- Streaming: `src/f5_tts/socket_server.py` uses `infer_batch_process(..., streaming=True,
  chunk_size=2048)` and defaults `--dtype float32`. Issue #1225: user saw 2 s first-packet with a
  custom FastAPI wrapper; maintainer's answer was to use TRT-LLM.
- ONNX port https://github.com/DakeQQ/F5-TTS-ONNX (Apache-2.0, "2026/7/27 Refactor"): CPU
  onnxruntime/OpenVINO, fp16 export flag, **no RTF numbers**, no streaming.

### CosyVoice2 / Fun-CosyVoice3 (FunAudioLLM → now served from github.com/QwenAudio/CosyVoice)

- `gh api repos/FunAudioLLM/CosyVoice` redirects to `QwenAudio/CosyVoice`, last push 2026-05-25,
  Apache-2.0. "We strongly recommend using `Fun-CosyVoice3-0.5B`". Feature line: "Bi-Streaming:
  Support both text-in streaming and audio-out streaming, and achieves latency as low as 150ms"
  (no hardware). No RTF anywhere; TRT-LLM "could give 4x acceleration comparing with huggingface
  transformers implementation". Eval (self-reported) test-en WER: Fun-CosyVoice3-0.5B_RL 1.68,
  CosyVoice2 2.57.
- API (`cosyvoice/cli/cosyvoice.py`): `AutoModel(model_dir, load_jit=False, load_trt=False,
  load_vllm=False, fp16=False)`; `inference_zero_shot(tts_text, prompt_text, prompt_wav,
  zero_shot_spk_id='', stream=False, speed=1.0)`; bistream by passing a text generator. fp16 is
  opt-in (fp32 default), so Turing-safe. CosyVoice2 paper gives only a latency *formula*
  (L_TTS = M·d_lm + M·d_fm + M·d_voc), no measured ms (https://arxiv.org/html/2412.10117).
- Install is conda + `requirements.txt` + optional sox/ttsfrd; no wheel.

### VoxCPM (OpenBMB)

- https://github.com/OpenBMB/VoxCPM, Apache-2.0, PyPI `voxcpm` 2.0.3 (2026-05-11), push 2026-09-02.
  VoxCPM2 2B (2026.04), VoxCPM1.5 0.6B (2025.12), VoxCPM-0.5B (2025.09). RTX 4090 table: RTF
  "~0.30 / ~0.15 / ~0.17" (2 / 1.5 / 0.5B), Nano-vLLM "~0.13 / ~0.08 / ~0.10", VRAM "~8 GB / ~6 GB /
  ~5 GB". Seed-TTS test-en WER/SIM: VoxCPM2 1.84/75.3, 0.5B 1.85/72.9, 1.5 2.12/71.4.
- Clone: `generate(text, prompt_wav_path, prompt_text, reference_wav_path=...)`;
  `generate_streaming(...)` yields chunks. `optimize=True` = torch.compile by default.
- dtype: `config.dtype: str = "bfloat16"`; `pick_runtime_dtype` forces float32 only on MPS, "CUDA
  and CPU keep whatever the checkpoint was trained with" (model/utils.py). Issue #112 "fp16 support
  for older hardware" (GTX 1650, open): maintainer "change it from `bfloat16` to `float16`" in
  `config.json`; reporter saw no speedup. Issue #223: 5060 Ti user reports RTF ≈1.2 for VoxCPM2,
  another 5070 Ti user 0.16 — high variance. ‡fp32 VoxCPM1.5 would need roughly 2× the ~6 GB bf16
  figure, over the 6 GiB free budget. GGUF via llama.cpp-omni: "RTF ~1.76 (Q8_0) on Apple M4 Pro /
  Metal" — not realtime.

### XTTS-v2 (idiap/coqui-ai-TTS)

- https://github.com/idiap/coqui-ai-TTS (MPL-2.0 code; PyPI `coqui-tts` 0.27.5, 2026-01-26; push
  2026-06-10). "XTTS can stream with <200ms latency" (no hardware). Model weights under CPML
  ("Coqui Public Model License", non-commercial) per
  https://coqui-tts.readthedocs.io/en/latest/models/xtts.html, which also shows
  `get_conditioning_latents(audio_path=[...])` → `inference_stream(text, "en", gpt_cond_latent,
  speaker_embedding)` (`stream_chunk_size=20` default in xtts.py) and "cloning ... using just a
  quick 3-second audio clip". No dtype code in `TTS/tts/models/xtts.py` → fp32. Docs: "Streaming
  inference is typically slower than regular inference, but it allows to get a first chunk of audio
  faster." No RTF anywhere. CPU Docker image exists; no CPU speed claim.

### Qwen3-TTS (0.6B / 1.7B Base)

- https://github.com/QwenLM/Qwen3-TTS (Apache-2.0; PyPI `qwen-tts` 0.1.1 2026-02-06; push
  2026-03-17). README: "end-to-end synthesis latency as low as 97ms" (no hardware), all snippets
  `dtype=torch.bfloat16, attn_implementation="flash_attention_2"`; vLLM-Omni "Now only offline
  inference is supported"; no sglang/MLX/ONNX.
- Turing: issue #43 "CUDA device-side assert triggered on Turing GPUs (RTX 2060) during FP16
  inference" — maintainer: "the code predictor model—being a small-parameter AR module—is extremely
  sensitive to precision, we strongly recommend using `float32` for inference on machines that do
  not support `bfloat16`". PR #355 (open) casts logits to fp32 to fix the FP16 overflow.
- Speed: issue #89 "Very slow inference on 5090" — "GPU usage ... remains ~4-5%"; 3060: "1 sec of
  audio takes 5 seconds"; issue #171 comment: Ryzen 8745HS CPU "1.2-1.4 RTF". Streaming: issue #10
  — maintainer says streaming "will be mainly driven by the vLLM-Omni community". Community forks:
  https://github.com/dffdeeq/Qwen3-TTS-streaming ("~6x", benchmarked only on RTX 5090, installs
  cu130 + flash-attn wheels — not a Turing path); https://github.com/HaujetZhao/Qwen3-TTS-GGUF
  (llama.cpp + ONNX, "RTX 5050: RTF 0.35", "CPU: RTF 1.3", first audio "~300ms", Windows binaries
  only, no license file).

### IndexTTS-2 / 2.5 (bilibili)

- https://github.com/index-tts/index-tts, "bilibili Model Use License Agreement", 0.8B, push
  2026-08-18. RTX 4090 `kv_cache=True`: 2.5 bf16 0.2065 / fp32 0.2060, 2.0 fp16 0.3257 / fp32
  0.3748 — fp32 costs nothing here, good for Turing. `IndexTTS2(..., use_bf16=True)` default. No
  streaming (issues #85, #339, #402 open), CUDA 12.8+. Issue #288 "12G显存已淘汰" (12 GB not enough
  for v2, closed) with a comment that fp16 "能压到9G"; #585 3060: 228 s for 17 s audio before
  `--fp16`. Eval on CV3-Eval only (en WER 5.12 / SS 68.06 for 2.5).

### Spark-TTS (SparkAudio)

- https://github.com/SparkAudio/Spark-TTS Apache-2.0 code; weights "updated from Apache 2.0 to CC
  BY-NC-SA" (https://huggingface.co/SparkAudio/Spark-TTS-0.5B). L20 TRT-LLM concurrency 1: "876.24
  ms" latency, RTF 0.1362. No streaming, no dtype note, no CPU note, push 2025-04-09, training code
  never released. Seed-TTS test-en (ZipVoice paper Table I): SIM 0.584 / WER 1.98.

### GPT-SoVITS (RVC-Boss)

- https://github.com/RVC-Boss/GPT-SoVITS MIT, push 2026-08-18. "Zero-shot TTS: Input a 5-second
  vocal sample"; "RTF(inference speed) of GPT-SoVITS v2 ProPlus: 0.028 tested in 4060Ti, 0.014
  tested in 4090 ... 0.526 in M4 CPU". `is_half` optional (fp32 works); `install.sh --device CPU`.
  Streaming not documented on the README (`api_v2.py` exists, unverified). Heavy pipeline (GPT +
  SoVITS + speaker-verification model), WebUI-centric; viable but not a two-line integration.

### OpenVoice V2 + MeloTTS (MyShell)

- https://github.com/myshell-ai/OpenVoice MIT ("Free for both commercial and research use"), push
  2025-04-19; QA: "OpenVoice only clones the tone color of the reference speaker. It does NOT clone
  the accent or emotion." Base TTS is MeloTTS (`pip install
  git+https://github.com/myshell-ai/MeloTTS.git`, MIT, "Fast enough for CPU real-time inference",
  last commit 2024-12-24). Two-stage, no streaming, no numbers. Cloning fidelity is by design lower
  than the LM/flow models above.

### Ruled-out set (one line each, with the owning source)

- **AuK / AuK-Flash** (https://github.com/Tencent-Hunyuan/AuK, MIT, 2026-09-09): 1.5B DiT + Qwen2.5-
  Omni-3B encoder; "one NVIDIA A800-SXM4-80GB with bf16 inference" table: 24.77 GiB, 16.75 GiB with
  `--cpu_offload`; no RTF; only `--dtype bf16` documented. Does not fit 11 GiB, let alone 6.
- **Fish S2-Pro** (https://github.com/fishaudio/fish-speech): "requires a large amount of VRAM. We
  recommend using a GPU with at least 24GB"; H200 RTF 0.195 / TTFA ~100 ms; Fish Audio Research
  License. **OpenAudio S1-mini** (0.5B, CC-BY-NC-SA, gated, WER 0.011 on its card) is superseded;
  the v1.5.x README's "1:5 on an Nvidia RTX 4060 laptop" claim is for the older 1.5 model and
  needs `--half` "For GPUs that do not support bf16".
- **Kyutai TTS-1.6B / Unmute / DSM** (https://huggingface.co/kyutai/tts-1.6b-en_fr): "we prefered to
  restrict the voice cloning ability to the use of pre-computed voice embeddings" — no local clone.
- **VibeVoice-Realtime-0.5B** (https://huggingface.co/microsoft/VibeVoice-Realtime-0.5B): "Removed
  acoustic tokenizer to avoid users creating embedding on their own"; BF16 weights.
- **Zonos-v0.1** (https://github.com/Zyphra/Zonos): `model.py` line 79 `.to(device, torch.bfloat16)`
  hard-coded; hybrid "requires a 3000-series or newer"; "~2x" on 4090 → below realtime on Turing;
  last push 2025-03-05.
- **Dia-1.6B** (https://github.com/nari-labs/dia): "CPU support is to be added soon"; 4090 fp32 RTF
  1.0 (x1 realtime), so a 2080 Ti in fp32 is > 1.
- **Orpheus-3B** (https://github.com/canopyai/Orpheus-TTS): 3B on vLLM; cloning "hasn't been
  explicitly trained"; "~200ms streaming latency" without hardware.
- **Sesame CSM-1B** (https://github.com/SesameAILabs/csm): "A CUDA-compatible GPU", no latency
  claims, cloning only via context Segments, "has not been fine-tuned on any specific voice".
- **OuteTTS-1.0** (https://github.com/edwko/OuteTTS): no streaming, benchmarks only as an image
  ("NVIDIA L40S"), PyPI 0.4.4 last 2025-05-23.
- **Kokoro-82M** (https://huggingface.co/hexgrad/Kokoro-82M), **Kitten TTS**
  (https://github.com/KittenML/KittenTTS), **Supertonic** (https://github.com/supertone-inc/supertonic,
  "does not include an official voice-cloning pipeline", archived 2026-09-09), **Piper**: no
  zero-shot cloning; baselines only. (Piper not fetched this pass; excluded on the caller's
  premise.)
- **StyleTTS2** (https://github.com/yl4579/StyleTTS2, MIT, last push 2024-08-10): LibriTTS
  checkpoint does zero-shot, but the repo is notebook-only, unmaintained, no streaming, no RTF.

## Conclusion

### 1. GPU ranking for this box (2080 Ti, fp32 only, ~6 GiB free), lowest TTFA with acceptable clone

1. **Pocket TTS** — either on CPU (leaves the GPU free) or `.to("cuda")` manually; the T4 datapoint
   ("6.28x") is the only Turing-class number any owner published. TTFA "~200ms", true streaming,
   clone from wav, fp32 native, 100M. Risk: quality is unmeasured by anyone with a table; the
   "first word garbled" issue is open.
2. **Chatterbox-Turbo** — fp32 default, single-step decoder, MIT, best-attested quality among the
   small open models (original Chatterbox Elo 1479 on TTS Arena V2). No streaming until PR #528
   lands, so TTFA = full-line generation time; expect it to be under the caller's 3–6 s
   (original model) but nobody has published Turbo RTF.
3. **ZipVoice-Distill (sherpa-onnx int8, CPU) or LuxTTS (PyTorch fp32, GPU, 48 kHz)** — NAR flow
   matching, 4 steps, Seed-TTS SIM 0.679 / WER 1.64 in the paper. TTFA ≈ RTF × utterance (≈0.6 s
   for a 3 s line at RTF 0.2) with no chunk streaming. Needs the transcript (ZipVoice) / not
   (LuxTTS). LuxTTS has no owner benchmark hardware named.
4. Honourable mention: **XTTS-v2** (`inference_stream`, fp32, "<200ms") if CPML non-commercial is
   acceptable; **CosyVoice3-0.5B** (`stream=True`, `fp16=False`, "150ms") if you accept a conda
   install and unknown fp32 VRAM.

### 2. CPU-only ranking (RTF < 1 on a modern 8–16 core x86)

1. **Pocket TTS** — `pip install pocket-tts` (RTF ≈0.4 on a 4-vCPU cloud VM; ≈0.17 on M4) or
   sherpa-onnx int8 (RTF 0.15–0.27 at 2 threads). Streaming in both.
2. **ZipVoice-Distill int8 via sherpa-onnx** — RTF 0.205 at 4 threads (maintainer log); paper 1.22
   single-thread PyTorch at 4 NFE. Sentence-callback, transcript required.
3. **NeuTTS-Nano Q4 GGUF** — 221 tok/s LM-only on a Ryzen 9 HX 370 (≈4.4x realtime before codec,
   derived), llama.cpp streaming, ONNX decoder; gated HF, NeuTTS Open License 1.0, transcript
   required.
4. **Chatterbox-Nano** — "3x faster than realtime on 8 CPU cores", MIT, no streaming.

### 3. Install + minimal clone-and-stream snippets (from the owners' docs)

**Pocket TTS (PyTorch, CPU; add `.to("cuda")` per README's unofficial GPU note)**

```bash
pip install pocket-tts --extra-index-url https://download.pytorch.org/whl/cpu   # README
pip install sounddevice                                                          # playback
# one-time: accept terms at https://huggingface.co/kyutai/pocket-tts, then `hf auth login`
```

```python
# from https://kyutai-labs.github.io/pocket-tts/API%20Reference/python-api/ + README
import sounddevice as sd
from pocket_tts import TTSModel, export_model_state

model = TTSModel.load_model()                                    # CPU; optional: model.to("cuda")
voice = model.get_state_for_audio_prompt("./ref16s.wav")         # wav only, no transcript
export_model_state(voice, "./ref16s.safetensors")                # cache; reload is "very fast"

with sd.OutputStream(samplerate=model.sample_rate, channels=1, dtype="float32") as out:
    for chunk in model.generate_audio_stream(voice, "Hello. Your build finished, no errors."):
        out.write(chunk.numpy().reshape(-1, 1))                  # generator of 1-D tensors
```

**Pocket TTS or ZipVoice via sherpa-onnx (CPU, int8, callback streaming)**

```bash
pip install sherpa-onnx soundfile sounddevice                    # PyPI 1.13.8
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2
tar xvf sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2
# ZipVoice instead:
# wget .../tts-models/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia.tar.bz2 && tar xvf ...
# wget https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx
```

```python
# condensed from python-api-examples/pocket-tts-play.py (Xiaomi, Apache-2.0)
import sherpa_onnx, soundfile as sf
d = "./sherpa-onnx-pocket-tts-int8-2026-01-26"
tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
    model=sherpa_onnx.OfflineTtsModelConfig(
        pocket=sherpa_onnx.OfflineTtsPocketModelConfig(
            lm_flow=f"{d}/lm_flow.int8.onnx", lm_main=f"{d}/lm_main.int8.onnx",
            encoder=f"{d}/encoder.onnx", decoder=f"{d}/decoder.int8.onnx",
            text_conditioner=f"{d}/text_conditioner.onnx",
            vocab_json=f"{d}/vocab.json", token_scores_json=f"{d}/token_scores.json"),
        num_threads=2, provider="cpu")))
ref, ref_sr = sf.read("./ref16s.wav", dtype="float32")          # only first 10 s are used
g = sherpa_onnx.GenerationConfig()
g.reference_audio, g.reference_sample_rate, g.num_steps = ref, ref_sr, 5
# ZipVoice additionally needs: g.reference_text = "<exact transcript>"; g.num_steps = 4
def on_chunk(samples, progress):                                 # push to PipeWire here
    return 1                                                     # 0 aborts
audio = tts.generate("Hello. Your build finished, no errors.", g, callback=on_chunk)
```

**Chatterbox-Turbo (GPU fp32, no streaming yet)**

```bash
pip install chatterbox-tts                                       # README; tested on Python 3.11
```

```python
# README snippet, https://github.com/resemble-ai/chatterbox
import torchaudio as ta
from chatterbox.tts_turbo import ChatterboxTurboTTS
model = ChatterboxTurboTTS.from_pretrained(device="cuda")        # nano=True for the 110M CPU model
wav = model.generate("Hello [chuckle], got a minute?", audio_prompt_path="./ref16s.wav")
ta.save("out.wav", wav, model.sr)                                # or play wav[0].numpy() at model.sr
```

**ZipVoice-Distill / LuxTTS in PyTorch (fp32, GPU or CPU), if you want 48 kHz**

```bash
git clone https://github.com/ysharma3501/LuxTTS.git && cd LuxTTS && pip install -r requirements.txt
```

```python
# README, https://github.com/ysharma3501/LuxTTS
import soundfile as sf
from zipvoice.luxvoice import LuxTTS
lux = LuxTTS("YatharthS/LuxTTS", device="cuda")                  # or device="cpu", threads=2
prompt = lux.encode_prompt("./ref16s.wav", rms=0.01)             # audio only
wav = lux.generate_speech("Hello. Your build finished, no errors.", prompt, num_steps=4)
sf.write("out.wav", wav.numpy().squeeze(), 48000)
```

### 4. Rule out immediately

- AuK / AuK-Flash: 16.75 GiB with offload, bf16 only.
- Fish S2-Pro: 24 GB minimum; S1-mini: NC licence, gated, no perf numbers, superseded.
- Kyutai TTS-1.6B / Unmute: cloning deliberately limited to shipped embeddings.
- VibeVoice-Realtime: acoustic tokenizer withheld, so no reference encoding.
- Zonos: `torch.bfloat16` hard-coded; below realtime even on a 4090.
- Dia: no CPU, fp32 RTF 1.0 on a 4090.
- Orpheus-3B, CSM-1B, OuteTTS-1B: too large or no cloning objective / no streaming / stale.
- Qwen3-TTS on this GPU: fp32 RTF 1.5 already measured; every faster path needs bf16/fp16 or
  Windows.
- Kokoro, Kitten, Supertonic, Piper: no zero-shot cloning.
