# hearthsmith

A pixel-art blacksmith who lives on your desktop, nags you about your tasks, watches your terminal panes, and hands work to your coding agents.

*Formerly `forge`. Renamed because nobody can find a project called forge.*

Harness-agnostic: hearthsmith **owns the task store** (SQLite) and exposes it over MCP, so Claude Code,
dsh, Codex, OpenClaw or a shell are all just clients. [hyperpanes](https://github.com/Eyalm321/hyperpanes)
is first-class: the pet reads what you're doing from your panes, nags you *in* the relevant pane,
and can hand a task to a worker pane instead of nagging.

```
timer ─▶ hearthsmithd heartbeat
          ├─ T0  sense    hyperpanes /state + /projects + pane screens, tasks.md, the store   (no model)
          ├─ T1  decide   Jev typed decision: nag? which task? urgency? channel?              (~400ms / ~$0.00002)
          ├─ T2  compose  Ornith-1.5-9B on a local Ollama writes the blacksmith's line        (free, private)
          ├─ T3  fallback OpenRouter chat model when the local box is asleep
          └─ deliver      notify-send | hyperpanes pane message | delegate to a worker queue
                          + writes sprite.json for the avatar renderer
                          + says it out loud in his own voice (AuK clone, see Voice)
```

Every stage degrades: no hyperpanes → store-only state; decider down → rule heuristics; Ollama
down → OpenRouter → template. The loop never goes silent because a dependency did.

## Install

```sh
uv sync && uv pip install -e .
hearthsmith add "temper the blade" --due 2026-09-20T18:00 --project hearthsmith
hearthsmith state            # the exact paragraph the decider sees
hearthsmithd --once --dry    # decide + compose, deliver nothing
```

Tell him a reminder the way you'd say it — "remind me to call the vet friday at 5pm", "don't let
me forget rent tomorrow morning", "in 20 minutes take the bread out" — and it lands on the ledger
as *call the vet*, due Fri 17:00, and he says the time back. Dates are read locally
(`src/hearthsmith/when.py`, no model); a day with no time is due by 18:00 that day. Only when you
name no time at all does the decider guess one. The ledger's add box reads the same words.

Tasks can **repeat** — "water the plants every monday", "standup every weekday at 9:30",
"daily vitamins at 9pm", "pay rent monthly", or `@every(mon,thu)` / `@every(2 weeks)` in tasks.md
and the ledger. Ticking one off, however it's ticked (ledger, CLI, MCP, "that's done"), puts the
next occurrence on the ledger; done three days late, the next one is still ahead of you rather
than a backlog of three. And tasks can have **steps**: ledger → *Add step…*, `hearthsmith add
--parent <id>`, or let the compose model cut it up — "break down the site launch", ledger →
*Split into steps*, `hearthsmith split <id>`. A task with open steps is nagged about by its next
step ("write the copy (step of 'the site launch')"), carrying the task's due date; finishing the
task finishes its steps.

Any task can be **handed to an agent**: ledger → *Hand to agent*, "give the changelog to an
agent", `hearthsmith hand <id>`, or `hearthsmith_tasks_hand` over MCP. It goes to a Claude pane
already on that work in the task's project if there is one, else a new pane in the project's
folder, briefed with the title, notes, open steps and an ask to finish with a short report. The
task moves to the ledger's **Agents** tab. When the agent goes quiet, the heartbeat reads its
last reply from Claude Code's session transcript (the screen scrape is the fallback), appends it
to the task's notes, puts the task back in Open marked *agent reported — check it*, and tells
you. He doesn't tick it off for you. *Take back* (or `hearthsmith hand --back <id>`) stops
waiting on the agent.

He **remembers you**. Tell him how you work — "I don't want nags before 10", "don't nag me
about email", "the site launch matters most this week", "weekends are mine", "I work best in
the evenings" — and he keeps it. The ones with a plain meaning become rules he obeys: quiet
hours tightened (the morning brief waits too), whole weekends off, a topic never nagged about
(it stays on the ledger), a focus whose tasks come first until it expires. Everything else goes
into what the decider and his voice read. He also notices, from the store: the hours you actually
finish things, and tasks nagged about again and again without moving — for those he suggests
splitting or handing off instead of repeating himself. "What do you know about me", "forget
that…", the ledger's **Memory** tab (× to forget), `hearthsmith memory [add|forget]`, and
`hearthsmith_memory_list / _add / _forget` over MCP.

When nagging isn't working he **offers to do something about it**. A task nagged 4 times
(`nag.stuck_after`) with no steps and no movement gets an offer instead of a fifth nag: "'do the
taxes' has sat through 5 nags… want me to split it into steps? Or hand it to an agent, or leave it
be." He recommends a hand-off when the task belongs to a project with a folder, a split
otherwise, and does neither until you answer — "split it", "hand it off", "yes" (his pick) or
"no" — to him or from the ledger, where a stuck task is marked and its ⋯ menu has *Leave it be*.
"No" stops the offers and the nags for that task until its date moves or it gets a step. An
unanswered offer can come back after three days.

Twice a day he tells you where things stand. The **morning brief** (from 08:30): what landed
overnight — answers, agents that finished or didn't — what's overdue, what's due today, what's
stuck, which panes are waiting on a yes. The **evening wrap** (from 18:30): what you struck off,
what's still open, what's due tomorrow; an evening with nothing to say is skipped. Each comes
once a day, on the sprite and in his voice, and waits until you've touched the keyboard or mouse
in the last two minutes (GNOME's idle monitor) so it greets you rather than an empty room. The
facts come from the store with no model; the compose model only phrases them, a template when
it's down. On demand: "brief me" / "what's on my plate", **Brief me** in his menu,
`hearthsmith brief [morning|evening]`, or `hearthsmith_brief` over MCP. Times in `brief:` config.

Ask him a question and he answers it rather than reporting that a page was opened. One page
holds it ("what does the P2S cost") → he opens the page and reads it, a couple of seconds, and
says so plainly when the page doesn't actually contain it. It needs comparing sources ("what do
people charge on average", "compare X and Y") → he puts an agent on it in its own pane and brings
the answer back on the next heartbeat, attached to the task.

Everything he is asked to do is recorded: the goal, which body ran it, whether it worked, the
steps he took with their confidence and decision latency, and what the verifier saw. `hearthsmith runs`
lists them, `hearthsmith runs <id>` shows one. Values you typed into his dialog are stored as
"(from you)" — a credential never reaches the history.

Config: `~/.config/hearthsmith/config.yaml` (every key optional, see `src/hearthsmith/config.py`).
State: `~/.local/state/hearthsmith/` (`hearthsmith.db`, `sprite.json`).

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
claude mcp add hearthsmith -- $(pwd)/.venv/bin/hearthsmith-mcp
```

Tools: `hearthsmith_tasks_list / _get / _add / _edit / _done / _reopen / _block / _snooze / _delete / _split / _hand`, `hearthsmith_memory_list / _add / _forget`, `hearthsmith_brief`, `hearthsmith_nags_recent`.

## tasks.md

Zero-dep importer. `- [ ] title @due(2026-09-20) @every(mon) +project #tag`. One-way: file → store; tick the box
to mark done. hearthsmith never writes the file.

## hyperpanes

Reads `~/.local/state/hyperpanes/control.json` for port + token. Nags go through
`POST /panes/{id}/messages` (out-of-band, to the pane's agent). `POST /panes/{id}/input` is
arbitrary command execution and stays off unless `hyperpanes.allow_pane_input: true`.
Delegation enqueues to `hyperpanes.delegate_queue` (default `hearthsmith`); drain it with
`hyperpanes worker --queue hearthsmith -- <cmd>`.

## Avatar

An always-on-top, click-through GTK window (`hearthsmith-sprite.service`) that polls
`sprite.json` (`state ∈ idle | forge | alert | sleep`, `text`, `urgency`) and plays the matching
frames from a sprite pack sliced out of `assets/sheets/`. Left click talks to him, drag moves him,
middle click opens his ledger, right click is the menu (ledger, size, corner, sheet, nag now, hide).

The **ledger** is the task list as a window, read straight from the store — a task an agent adds
over MCP appears within a couple of seconds. The add box takes the tasks.md syntax
(`quench @due(2026-09-25T18:00) +forge #hot`); tick = done, click a row = its notes and the runs
he made for it, `⋯` = edit / snooze / block / delete. Open / Blocked / Done tabs, Ctrl+N to add,
Esc to close. `python3 -m hearthsmith.sprite.ledger` opens it without the avatar. Position and size persist in
`~/.config/hearthsmith/avatar.yaml`. If the frame clock stalls he remaps himself, then restarts.

While either body works, the avatar narrates it — "clicking One way", "typing Where from? =
'Zurich'" — so a task running in his Chrome is still visible on your desktop. Progress lines skip
the typewriter and expire in seconds; a nag still types out and stays.

Work aimed at an agent goes to the right one: panes **already in that project** are candidates,
each described by what it is actually doing (its last few lines, not its label), and the question
is whether the assignment continues that work or is a separate concern deserving its own agent. A
busy agent is only interrupted when it really is the same thread of work.

## Voice

He sounds like a dwarf. `assets/voice/dwarf.wav` is 16s of WoW dwarf NPC lines (`dwarf.txt` its
transcript); a zero-shot cloner says each line in that voice, so the clip *is* the voice — drop
in another wav (`voice.ref` + `voice.ref_text`) and he is someone else. Engines, tried in order
(`voice.engines`), same clip into all of them:

- **pocket** — [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (Kyutai, 100M, MIT code /
  CC-BY-4.0 weights). 2 CPU cores, streams PCM straight into `pw-play`: **first sound ~0.2s**,
  RTF ~0.6 here. Clip only, no transcript. Weights are gated — accept terms once at
  hf.co/kyutai/pocket-tts. Default.
- **auk** — [AuK](https://github.com/Tencent-Hunyuan/AuK) (Tencent, MIT) on the
  [HF space](https://huggingface.co/spaces/tencent/AuK). Best clone; ~17 GiB so it can't run
  here, ~8 free lines/day, ~20s a line.
- **qwen** — Qwen3-TTS-0.6B-Base (Apache-2.0) on the GPU, fp32 (fp16 NaNs on Turing), ~RTF 1.5.
  Kept for A/B.

pocket and qwen live in one warm server: `contrib/voice-server` (`hearthsmith-voice.service`,
:7861); Qwen unloads after 15 min idle, Pocket stays. Wavs are cached by text+clip+engine under
`~/.local/state/hearthsmith/voice/`. Playback is `pw-play` to the default sink, blocking, because
`hearthsmithd` is a oneshot unit. Research behind the pick: `docs/research/realtime-clone-tts.md`.

```sh
forge speak "Oi. That ledger's got rust on it."   # hear him; --no-play prints the wav paths
```

## Suggestions

After a turn, a Claude pane can offer its own next prompt as ghost text in its input box
(Claude Code "prompt suggestions"; staged rollout, so it appears sometimes — force with
`CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=true`; never on the first turn, in plan mode, or while a
permission is pending). He watches idle panes for one, and once it has sat unchanged for
`hyperpanes.suggestion_settle_s` (20s — the screen is plain text, so ghost text and a line you
are typing look the same) he tells you once: *"'canora-sync' wants to: run the tests. Yes or
no?"*, with how many sibling panes in that project are still busy. `hearthsmith suggestions`
lists them. That is `hyperpanes.suggestions: observe` (or `off`).

`accept` goes further, in panes **he** spawned only (`meta.owner=hearthsmith` — yours are only
ever reported): Jev judges each settled suggestion *accept / wait / dismiss / ask*, with the
pane's last answer and its siblings' state (same `meta.goal` when the goals org stamps one, else
same directory). Accept = Tab, Enter — named keys, no text can go in that way — pressed only if
the input line still reads what was judged (the pane regenerates suggestions while they sit).
Busy siblings mean wait, no call made. Anything irreversible comes back as dismiss or ask; ask
is the fallback when the decider is down. Every verdict is a run (`hearthsmith runs`).

Panes an agent org spawned for itself (`goal-orchestrator` skill: `meta.role=spec|impl`,
`meta.goal`) count as his too (`suggestion_accept_roles`); the goals orchestrator itself is
report-only, its suggestions are goal-level. For those, one more step before Jev: he gathers
what the org already emits — the spec agent's text, siblings' last words, reports on the pane
message bus, subtask states in the goal's work queue — and `compose.judge_model` (MiMo v2.6
flash on OpenRouter, ~$0.001, ~3s) condenses it into one paragraph Jev reads, plus a *depends on
unfinished work* score that forces wait over accept. Observed on a real queue: "run the tests"
waits at 0.90 while the sibling's subtask is claimed, accepts at 0.92 once it is done; "git push"
dismissed at 0.85. Design: `docs/suggestions-stage3-scope.md`.

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
default one; `hearthsmith web "<goal>"` runs it directly, and the `browse` intent routes there.

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
- the `hearthsmith-windows` GNOME Shell extension (`contrib/gnome-extension`, install.sh copies it;
  enable + log out/in once). Wayland hides window positions from clients; the extension exposes
  frame rects over the session bus, read-only.
- user in the `input` group (for `/dev/uinput`).

One Jev request per cycle carries *speculative heads* — the operation plus a target for each
operation that needs one, each head offering only compatible elements — so whichever operation
wins already has its target. Before acting he re-reads the element (gone, hidden or moved ⇒
re-observe instead of clicking blind), and after acting he waits for the tree to actually change
rather than sleeping a fixed amount. Those three ideas come from
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT), which does the
same thing for Chrome over CDP; hearthsmith keeps AT-SPI + uinput so it works in every app, in the
windows you can see.

Before reporting success he takes **one look**: a screenshot of the window goes to the local
vision model with the goal, and "YES / NO + why" decides whether the run is really done. That
catches what the accessibility tree can't express — a form that is filled in but still has its
date picker open over it reads as plausible text and looks obviously unfinished. Once per task,
never per step; if the extension or the model is missing it says so and keeps the original
verdict. `desktop.verify: false` turns it off, `hearthsmith-look "<goal>"` runs it by hand.

`hearthsmith do "in Firefox, search for 'lw-pla filament'"` · `hearthsmith windows` shows what he sees.
Custom-drawn surfaces (games, terminal grids) are invisible to AT-SPI — screenshot+vision is the
fallback there, not built yet.
