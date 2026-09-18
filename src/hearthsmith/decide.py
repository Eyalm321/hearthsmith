"""T1: the decision. State in, typed answers out. One interface, four backends:

  openrouter  Jev via OpenRouter's Decisions router — POST /api/alpha/decisions (default).
              Undocumented outside openrouter.ai/openapi.json; ~400ms, ~$0.00002/call.
              NOT /chat/completions (500s) and the TypeSafe SDK can't target it (hardcodes
              /v1/systemone), so this is a raw httpx call.
  typesafe    TypeSafeClient → api.typesafe.ai (needs TYPESAFE_API_KEY)
  adapter     MIT system-one-adapter over any OpenAI-compatible chat model (~20s; last resort)
  rules       no model at all; overdue + gap heuristics. Also the fallback when a backend errors.

The questions are the same Noul/Score/Choice objects in every case, so swapping backends is a
config key, not a code change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import httpx
from typesafe_sdk import Choice, Noul, Score

from hearthsmith.config import DecideCfg
from hearthsmith.store import Task

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

def _wire(q) -> dict:
    """Noul/Score/Choice → the JSON the Decisions router expects."""
    kind = type(q).__name__.lower()
    d = {"type": kind, "instructions": q.instructions}
    if q.criteria is not None:
        d["criteria"] = q.criteria
    return d


def _openrouter(cfg: DecideCfg, state: str, cands: list[Task]) -> Decision:
    r = httpx.post(f"{cfg.adapter_base_url.rstrip('/').removesuffix('/v1')}/alpha/decisions",
                   timeout=15.0,
                   headers={"Authorization": f"Bearer {os.environ[cfg.adapter_key_env]}"},
                   json={"model": cfg.openrouter_slug, "state": state,
                         "questions": {k: _wire(v) for k, v in questions(cands).items()},
                         "session_id": "hearthsmith"})
    r.raise_for_status()
    body = r.json()

    class A:  # duck-typed like SDK answers: .noul / .score / .choice / .probabilities
        def __init__(self, d): self.__dict__.update(d)
    ans = {k: A(v) for k, v in body["answers"].items()}
    d = _from_answers(ans, cands, "openrouter")
    d.raw["usage"] = body.get("usage")
    d.raw["model"] = body.get("model")
    return d


def _typesafe(cfg: DecideCfg, state: str, cands: list[Task]) -> Decision:
    from typesafe_sdk import TypeSafeClient
    with TypeSafeClient() as client:  # TYPESAFE_API_KEY from env
        r = client.system_one(state=state, questions=questions(cands))
    return _from_answers(r.answers, cands, "typesafe")


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
        if cfg.backend == "openrouter":
            return _openrouter(cfg, state, cands)
        if cfg.backend == "typesafe":
            return _typesafe(cfg, state, cands)
        if cfg.backend == "adapter":
            return _adapter(cfg, state, cands)
    except Exception as e:  # noqa: BLE001 — any backend failure degrades to rules, never to silence
        d = rules(cands, last_nag_at, min_gap_min)
        d.raw["backend_error"] = f"{cfg.backend}: {type(e).__name__}: {e}"[:300]
        return d
    return rules(cands, last_nag_at, min_gap_min)
