import base64, json, os, sys, time, httpx
out = sys.argv[1]; seed_note = sys.argv[2] if len(sys.argv) > 2 else ""
PROMPT = """Pixel-art character sprite sheet: chunky 32-bit-era pixel art, bold black outlines, warm saturated palette, strong cel shading, NO anti-aliasing, NO gradients, NO blur, NO background.

Character (identical in every cell): huge muscular blacksmith, bare arms, red bandana with two tails, thick white beard, brown leather apron with belt and pouches, dark trousers, boots, a blacksmith hammer in the right hand, iron tongs in the left.

Layout: 4 columns x 2 rows of equal cells, generous gap between cells, transparent background, no labels, no text, no anvil, no ground shadow. Full body in every cell, same scale, same 3/4 front view.
Row 1: idle A (relaxed); idle B (chest slightly higher, breathing); hammer raised high overhead; hammer struck down with orange sparks and a small flame.
Row 2: alert A (hammer raised, mouth open shouting); alert B (same, white exclamation mark above head); sleep A (eyes closed, leaning on hammer); sleep B (same, two small Z's floating).
""" + seed_note
key = os.environ["OPENROUTER_API_KEY"]
t = time.time()
r = httpx.post("https://openrouter.ai/api/v1/images", timeout=300,
    headers={"Authorization": f"Bearer {key}"},
    json={"model": "openai/gpt-image-2.5-sunburst", "prompt": PROMPT, "aspect_ratio": "16:9",
          "background": "transparent", "output_format": "png", "quality": "high", "n": 1})
d = r.json()
if r.status_code != 200 or not d.get("data"):
    print("ERR", r.status_code, json.dumps(d)[:500]); sys.exit(1)
open(out, "wb").write(base64.b64decode(d["data"][0]["b64_json"]))
u = d.get("usage", {})
print(f"{time.time()-t:.0f}s cost=${u.get('cost')} img_tokens={(u.get('completion_tokens_details') or {}).get('image_tokens')} -> {out}")
