# Suggestions, stage 3: judging a pane's next step against the whole goal org

Date: 2026-09-22. Status: BUILT as scoped (decisions 1+2 taken: roles gate yes, flash default). Stages 1–2 shipped (`19e627f`, `0f6ff37`).

## Problem

Stage 2 judges a settled suggestion with what one pane knows: its last answer and whether
siblings are *busy*. In a goal org (`goal-orchestrator` skill: goals-orch → spec agent → impl
agents, `meta.role/parent/goal/project`, reports over the pane message bus, subtasks in the
work queue) that is blind. "Run the integration tests" in impl pane B is right only if impl
pane A's subtask that B depends on is `done`; "commit this" in a spec agent is wrong while an
impl subtask is still leased; "verify against acceptance" is right only once the queue for that
goal is drained. Busy/idle can't say any of that, and an idle pane may be idle because it is
*blocked*, not finished.

## Shape

Keep the two-tier split the rest of hearthsmith uses: a chat model reads and condenses, Jev
decides. Nothing else changes about pressing keys.

```
settled suggestion in pane P (meta.goal=g)
  │
  ├─ gather (no model)        spec agent's spec text (its pane screen / last_answer)
  │                           every sibling with meta.goal=g: role, activity, last_answer[:600]
  │                           bus: P's inbox + parent's inbox (impl reports: progress|done|blocked)
  │                           work queue g: per-subtask state (queued|leased|done|failed)
  │                           P's own last answer, the suggestion, how long it sat
  │
  ├─ condense (MiMo v2.6)     → one paragraph, ≤120 words, fixed frame:
  │                             what the goal is · what is done · what is in flight / blocked ·
  │                             whether the suggestion depends on anything unfinished ·
  │                             whether it is irreversible. No verdict.
  │
  └─ decide (Jev Choice)      accept | wait | dismiss | ask   (unchanged criteria, stage 2)
                              + Score "depends_on_unfinished" 0..1 feeding a hard rule:
                              ≥0.5 → wait regardless of the Choice
```

Why MiMo and not Jev alone: Jev takes a state *string*; the raw gather is 5–20 KB of pane
screens and JSON, and stage 2 already showed Jev goes conservative (`ask` 0.49) when the state
is thin and confident (`accept` 0.89) when it is well framed. The condense step is the framing.
Why not MiMo alone: it would be the thing deciding to press Enter in an agent's terminal; Jev
is the calibrated, logged, swappable decider everywhere else in the smith, and its
probabilities are what `suggestion_accept_min_p` gates on.

## Model

`xiaomi/mimo-v2.6-flash` via OpenRouter (`DSH_OPENROUTER_API_KEY`, same key as decide/compose).
$0.14/M in, $0.28/M out, 1M ctx. A gather is ~6k tokens → ~$0.001 per judged suggestion. `-pro`
($0.44/$0.87) is a config swap (`compose.judge_model`); no reason to start there. Local Ornith
on the Mac is not used here: the condense needs to be reliable on structured input, and the
Mac is asleep half the time.

Config, all under existing sections:

```yaml
compose:
  judge_model: xiaomi/mimo-v2.6-flash     # condenses the org state before Jev; OpenRouter
hyperpanes:
  suggestions: accept
  suggestion_org_aware: true              # stage 3 on; false = stage 2 behaviour
```

## What gets built

1. `Hyperpanes.messages(pane_id)` — `GET /panes/{id}/messages` (exists server-side, unused).
2. `Hyperpanes.queue(name)` — subtask states for goal `g` (`GET /queues/{q}` shape to confirm).
3. `daemon.gather_org(snap, pane)` → dict. Pure, testable with fake panes.
4. `daemon.condense(cfg, org)` → str. One OpenRouter chat call, fixed system prompt, `max_tokens`
   200, temperature 0. On any error returns a mechanical fallback summary (counts of
   done/leased/blocked) so Jev still runs.
5. `_judge` grows `depends_on_unfinished: Score`; hard rule above. State string = condensed
   paragraph + the stage-2 lines.
6. `hearthsmith runs` step lines carry the condensed paragraph (truncated) and the score, so a
   wrong accept is diagnosable after the fact.
7. Tests: gather on a fake 4-pane org; hard rule; condense fallback path. No live model in tests.

Not built: anything that writes to the bus or queue; changing what the goal-org skills stamp;
a UI. The org already emits everything needed.

## Gates (carried from stage 2, unchanged)

Only `meta.owner=hearthsmith` panes are ever pressed. Goal-org panes are spawned by the
orchestrator agent, not the smith, so today they **won't** carry `owner` — stage 3 would judge
them and only ever *report*. Decision needed: extend the gate to `meta.role in {spec, impl}`
(panes an agent org spawned for itself, never a human's) — proposed yes, as
`hyperpanes.suggestion_accept_roles: [impl, spec]`. Goals-orch itself stays report-only: its
suggestions are goal-level ("start goal 3"), a human call.

## Cost / latency

Per settled suggestion in an org pane: one MiMo call (~1–2 s) + one Jev call (~0.4 s). Heartbeat
is on a minutes-scale timer; suggestions already wait 20 s to settle. Non-org panes: unchanged.

## Verification

Replay the stage-2 spike with a real 3-pane org (spec + 2 impl on a toy repo, one impl blocked
on the other): expect `wait` in the dependent pane while the other is leased, `accept` after its
`done` report lands, `ask` on the spec agent's "mark goal complete". Cost of the whole trial
< $0.05.

## Effort

~half a day. Items 1–3 an hour, 4–5 two hours, 6–7 an hour, trial the rest.
