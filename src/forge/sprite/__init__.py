"""The avatar. A separate process from forged — it needs a display, the daemon doesn't.

Layer model (all customization is data, no code):
  pack/manifest.json   {"grid": [w, h], "states": {"idle": [frames], ...}, "layers": [...]}
  pack/<layer>.png     one spritesheet per layer, same grid; rows = states in manifest order
  ~/.config/forge/avatar.yaml   which layer variant + palette per slot

Until a real pack exists, `procedural.py` draws a placeholder blacksmith from the same layer
list so the renderer, state machine and palette-swap path are exercised end to end.
"""
