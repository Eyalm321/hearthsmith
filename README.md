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

While either body works, the avatar narrates it — "clicking One way", "typing Where from? =
'Zurich'" — so a task running in his Chrome is still visible on your desktop. Progress lines skip
the typewriter and expire in seconds; a nag still types out and stays.

Work aimed at an agent goes to the right one: panes **already in that project** are candidates,
each described by what it is actually doing (its last few lines, not its label), and the question
is whether the assignment continues that work or is a separate concern deserving its own agent. A
busy agent is only interrupted when it really is the same thread of work.

## Two bodies, one brain

Jev decides; where the hands are depends on the job.

| errand | body |
|---|---|
| anything in a web page | **his Chrome** — [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT) over CDP |
| everything else | **your desktop** — AT-SPI + uinput, quiet by default |

Inside a page a DOM snapshot wins and it isn't close: the page owns its state machine and
ignores anything that isn't a real DOM event, so a site like Google Flights beats synthetic
input. Two upstream lines are wrapped rather than forked — decisions go to the OpenRouter
Decisions router (the Jev access this machine has), and the tab opens in the foreground so you
can watch. He gets his own Chrome profile because Chrome 136+ refuses remote debugging on the
default one; `forge web "<goal>"` runs it directly, and the `browse` intent routes there.

## Computer use

He works in **your** apps, on screen — no headless browser, no remote-debugging ports, no
separate profile. **Quiet by default: he never touches your mouse or keyboard**, so he can work
while you work. Widgets are activated through AT-SPI actions (`switch` a tab, `activate` a
button), fields filled through EditableText, web addresses opened straight in the browser.
`--hands` lets him drive the shared cursor for the widgets that expose no action — that one is
exclusive, and he stops the moment the pointer wanders off where he left it. Observe = AT-SPI2 accessibility tree (every GTK/Qt/
Electron app and Firefox/Chromium page content), decide = Jev (typed Choice over the visible
elements, ~0.5s), act = AT-SPI actions first, `/dev/uinput` only under `--hands`.

Setup (once):
- `gsettings set org.gnome.desktop.interface toolkit-accessibility true` (install.sh does it)
- the `forge-windows` GNOME Shell extension (`contrib/gnome-extension`, install.sh copies it;
  enable + log out/in once). Wayland hides window positions from clients; the extension exposes
  frame rects over the session bus, read-only.
- user in the `input` group (for `/dev/uinput`).

One Jev request per cycle carries *speculative heads* — the operation plus a target for each
operation that needs one, each head offering only compatible elements — so whichever operation
wins already has its target. Before acting he re-reads the element (gone, hidden or moved ⇒
re-observe instead of clicking blind), and after acting he waits for the tree to actually change
rather than sleeping a fixed amount. Those three ideas come from
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT), which does the
same thing for Chrome over CDP; forge keeps AT-SPI + uinput so it works in every app, in the
windows you can see.

Before reporting success he takes **one look**: a screenshot of the window goes to the local
vision model with the goal, and "YES / NO + why" decides whether the run is really done. That
catches what the accessibility tree can't express — a form that is filled in but still has its
date picker open over it reads as plausible text and looks obviously unfinished. Once per task,
never per step; if the extension or the model is missing it says so and keeps the original
verdict. `desktop.verify: false` turns it off, `forge-look "<goal>"` runs it by hand.

`forge do "in Firefox, search for 'lw-pla filament'"` · `forge windows` shows what he sees.
Custom-drawn surfaces (games, terminal grids) are invisible to AT-SPI — screenshot+vision is the
fallback there, not built yet.
