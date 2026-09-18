# forge

A pixel-art blacksmith that nags you about your tasks.

Harness-agnostic: forge **owns the task store** (SQLite) and exposes it over MCP, so Claude Code,
dsh, Codex, OpenClaw or a shell are all just clients. [hyperpanes](https://github.com/Eyalm321/hyperpanes)
is first-class: the pet reads what you're doing from your panes, nags you *in* the relevant pane,
and can hand a task to a worker pane instead of nagging.

```
timer ─▶ forged heartbeat
          ├─ T0  sense    hyperpanes /state + /projects + pane screens, tasks.md, the store   (no model)
          ├─ T1  decide   Jev typed decision: nag? which task? urgency? channel?              (~400ms / ~$0.00002)
          ├─ T2  compose  Ornith-1.5-9B on a local Ollama writes the blacksmith's line        (free, private)
          ├─ T3  fallback OpenRouter chat model when the local box is asleep
          └─ deliver      notify-send | hyperpanes pane message | delegate to a worker queue
                          + writes sprite.json for the avatar renderer
```

Every stage degrades: no hyperpanes → store-only state; decider down → rule heuristics; Ollama
down → OpenRouter → template. The loop never goes silent because a dependency did.

## Install

```sh
uv sync && uv pip install -e .
forge add "temper the blade" --due 2026-09-20T18:00 --project forge
forge state            # the exact paragraph the decider sees
forged --once --dry    # decide + compose, deliver nothing
```

Config: `~/.config/forge/config.yaml` (every key optional, see `src/forge/config.py`).
State: `~/.local/state/forge/` (`forge.db`, `sprite.json`).

### Decide backends (`decide.backend`)

| key | what | status |
|---|---|---|
| `openrouter` | [TypeSafe Jev](https://typesafe.ai) via OpenRouter's Decisions router `POST /api/alpha/decisions` (model `typesafe/jev-1.13`) — ~400ms, ~$0.00002/call | **default** |
| `typesafe` | Jev direct at `api.typesafe.ai`, `TYPESAFE_API_KEY` | needs early-access key |
| `adapter` | MIT [system-one-adapter](https://github.com/typesafe-ai/system-one-adapter-python) over any OpenAI-compatible chat model (~20s) | last resort |
| `rules` | overdue + gap heuristics, no model | always-on fallback |

Same `Noul` / `Score` / `Choice` questions in every backend — swapping is a config key.

## MCP

```sh
claude mcp add forge -- $(pwd)/.venv/bin/forge-mcp
```

Tools: `forge_tasks_list / _add / _done / _block / _snooze`, `forge_nags_recent`.

## tasks.md

Zero-dep importer. `- [ ] title @due(2026-09-20) +project #tag`. One-way: file → store; tick the box
to mark done. forge never writes the file.

## hyperpanes

Reads `~/.local/state/hyperpanes/control.json` for port + token. Nags go through
`POST /panes/{id}/messages` (out-of-band, to the pane's agent). `POST /panes/{id}/input` is
arbitrary command execution and stays off unless `hyperpanes.allow_pane_input: true`.
Delegation enqueues to `hyperpanes.delegate_queue` (default `forge`); drain it with
`hyperpanes worker --queue forge -- <cmd>`.

## Avatar

Not built yet. The daemon writes `sprite.json` (`state ∈ idle | forge | alert | sleep`, `text`,
`urgency`); the renderer is a separate process. Plan: layered spritesheets + palette LUT so the
blacksmith is customizable without new art.

## Browser

He drives **your** Firefox — a normal tab in the window you already have open, over WebDriver
BiDi (`--remote-debugging-port 9222`, loopback only; `contrib/firefox.desktop` adds the flag to
every launch). You watch, you can take over, your logins are already there. No headless
browser, no separate profile. Loop: Firefox lists actionable elements → Jev picks action +
target (typed decision, ~0.5s) → Firefox clicks/types. Without the port he can still open the
URL for you.
