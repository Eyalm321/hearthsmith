"""T1: the decision. State in, typed answers out. One interface, four backends:

  typesafe    TypeSafeClient → api.typesafe.ai (Jev proper; needs TYPESAFE_API_KEY)
  openrouter  TypeSafeClient pointed at openrouter.ai, model typesafe/jev-1.13
              (registered but every call 500s as of 2026-09-17 — kept for when it wakes up)
  adapter     MIT system-one-adapter over any OpenAI-compatible chat model (default today)
  rules       no model at all; overdue + gap heuristics. Also the fallback when a backend errors.

The questions are the same Noul/Score/Choice objects in every case, so swapping backends is a
config key, not a code change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from typesafe_sdk import Choice, Noul, Score

from forge.config import DecideCfg
from forge.store import Task

URGENCY = ["ignorable", "soon", "now"]
CHANNELS = {
    "hyperpanes": "the user is at the terminal; say it in the relevant pane",
    "notify": "a desktop notification is enough",
    "delegate": "the task is mechanical and an agent could just do it in a worker pane",
}


@dataclass
class Decision:
    should_nag: float          # P(interrupt now)
    task_id: str | None
    urgency: str               # ignorable | soon | now
    channel: str
    needs_llm: float           # P(a templated line won't do)
    backend: str
    raw: dict = field(default_factory=dict)

    def as_json(self) -> str:
        return json.dumps({"should_nag": self.should_nag, "task_id": self.task_id,
                           "urgency": self.urgency, "channel": self.channel,
                           "needs_llm": self.needs_llm, "backend": self.backend, **self.raw})


def questions(candidates: list[Task]) -> dict:
    q = {
        "should_nag": Noul(instructions="The user should be interrupted about a task right now, "
                           "given what they are doing, the time, and how recently they were nagged."),
        "urgency": Score(instructions="How urgent is the most pressing open task?",
                         criteria=["nothing is due soon; leave the user alone",
                                   "something is due today or slightly overdue",
                                   "something is clearly overdue or blocking other work"]),
        "channel": Choice(instructions="Best way to reach the user right now.", criteria=CHANNELS),
        "needs_llm": Noul(instructions="A short templated sentence would feel wrong here; the "
                          "situation deserves a written, specific remark."),
    }
    if candidates:
        q["task"] = Choice(instructions="Which task is most worth raising right now?",
                           criteria={t.id: f"{t.title}" + (" (OVERDUE)" if t.overdue else "")
                                     for t in candidates[:40]})
    return q


def _from_answers(ans, candidates: list[Task], backend: str) -> Decision:
    task_id = ans["task"].choice if "task" in ans else (candidates[0].id if candidates else None)
    return Decision(
        should_nag=float(ans["should_nag"].noul),
        task_id=task_id,
        urgency=URGENCY[min(int(ans["urgency"].score), 2)],
        channel=ans["channel"].choice,
        needs_llm=float(ans["needs_llm"].noul),
        backend=backend,
        raw={"probabilities": {k: getattr(v, "probabilities", None) for k, v in ans.items()
                               if getattr(v, "probabilities", None) is not None}},
    )


# -- backends ------------------------------------------------------------------------------

def _typesafe(cfg: DecideCfg, state: str, cands: list[Task], via_openrouter: bool) -> Decision:
    from typesafe_sdk import TypeSafeClient
    if via_openrouter:
        client = TypeSafeClient(api_key=os.environ[cfg.adapter_key_env],
                                base_url=cfg.adapter_base_url, model=cfg.openrouter_slug)
    else:
        client = TypeSafeClient()  # TYPESAFE_API_KEY from env
    with client:
        r = client.system_one(state=state, questions=questions(cands))
    return _from_answers(r.answers, cands, "openrouter" if via_openrouter else "typesafe")


def _adapter(cfg: DecideCfg, state: str, cands: list[Task]) -> Decision:
    from system_one_adapter import SystemOneAdapterClient
    from system_one_adapter.providers.openai import OpenAIProvider
    provider = OpenAIProvider(cfg.adapter_model, base_url=cfg.adapter_base_url,
                              api_key=os.environ[cfg.adapter_key_env], api="chat_completions")
    with SystemOneAdapterClient(structured_outputs=False, llm_answer_mode="probabilities",
                                normalize_probabilities=True, n_retry_malformed_structure=1) as c:
        r = c.system_one(state=state, questions=questions(cands), model=provider)
    return _from_answers(r.answers, cands, "adapter")


def rules(cands: list[Task], last_nag_at: int | None, min_gap_min: int) -> Decision:
    """No-model fallback. Deterministic, deliberately conservative."""
    now = time.time()
    gap_ok = last_nag_at is None or now - last_nag_at > min_gap_min * 60
    overdue = [t for t in cands if t.overdue]
    soon = [t for t in cands if t.due and 0 < t.due - now < 86400]
    if overdue:
        pick, urg, p = overdue[0], "now", 0.9
    elif soon:
        pick, urg, p = soon[0], "soon", 0.6
    elif cands:
        pick, urg, p = cands[0], "ignorable", 0.15
    else:
        pick, urg, p = None, "ignorable", 0.0
    return Decision(should_nag=p if gap_ok else 0.0, task_id=pick.id if pick else None,
                    urgency=urg, channel="notify", needs_llm=0.0, backend="rules")


def decide(cfg: DecideCfg, state: str, cands: list[Task], last_nag_at: int | None,
           min_gap_min: int) -> Decision:
    try:
        if cfg.backend == "typesafe":
            return _typesafe(cfg, state, cands, via_openrouter=False)
        if cfg.backend == "openrouter":
            return _typesafe(cfg, state, cands, via_openrouter=True)
        if cfg.backend == "adapter":
            return _adapter(cfg, state, cands)
    except Exception as e:  # noqa: BLE001 — any backend failure degrades to rules, never to silence
        d = rules(cands, last_nag_at, min_gap_min)
        d.raw["backend_error"] = f"{cfg.backend}: {type(e).__name__}: {e}"[:300]
        return d
    return rules(cands, last_nag_at, min_gap_min)
