"""Config: ~/.config/hearthsmith/config.yaml over defaults. Every key optional."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.environ.get("HEARTHSMITH_CONFIG", Path.home() / ".config/hearthsmith/config.yaml"))
STATE_DIR = Path(os.environ.get("HEARTHSMITH_STATE_DIR", Path.home() / ".local/state/hearthsmith"))


@dataclass
class DecideCfg:
    # "openrouter" (Jev via /api/alpha/decisions, default) | "typesafe" (direct, TYPESAFE_API_KEY)
    # | "adapter" (MIT system-one-adapter over a chat model, slow) | "rules"
    backend: str = "openrouter"
    adapter_model: str = "deepseek/deepseek-v4.1-flash"
    adapter_base_url: str = "https://openrouter.ai/api/v1"
    adapter_key_env: str = "OPENROUTER_API_KEY"
    openrouter_slug: str = "typesafe/jev-1.13"


@dataclass
class ComposeCfg:
    # T2: local Ornith on the Mac via Ollama. T3: OpenRouter fallback when the Mac is down.
    ollama_url: str = "http://admins-mac-mini.local:11434"
    ollama_model: str = "hf.co/ornith-ai/Ornith-1.5-9B-GGUF:Q5_K_M"
    fallback_model: str = "deepseek/deepseek-v4.1-flash"
    fallback_base_url: str = "https://openrouter.ai/api/v1"
    fallback_key_env: str = "OPENROUTER_API_KEY"
    # condenses a goal org's state (spec, siblings, bus reports, queue) into one paragraph
    # before Jev judges a pane's suggested next step. Flash is plenty; -pro is a swap here.
    judge_model: str = "xiaomi/mimo-v2.6-flash"
    persona: str = (
        "You are a gruff but warm-hearted dwarven blacksmith who keeps the user's task forge. "
        "One or two short sentences. Smithing metaphors welcome, never cheesy. "
        "Never invent tasks; only mention what is in the state."
    )


@dataclass
class HyperpanesCfg:
    control_file: Path = Path.home() / ".local/state/hyperpanes/control.json"
    tail_lines: int = 20
    # pane input is arbitrary command execution; nags go through /messages, never /input
    allow_pane_input: bool = False
    delegate_queue: str = "hearthsmith"
    # off until something drains the queue (`hyperpanes worker --queue hearthsmith -- ...`); otherwise
    # a "delegate" decision marks the task delegated and nothing ever happens
    delegate_enabled: bool = False
    # A Claude pane offers its own next prompt as ghost text after a turn. "observe" = he notices
    # and tells you; "off" = ignore. Accepting on your behalf is a later stage: the screen read is
    # plain text, so a suggestion and a line you half-typed look the same — he only ever reports
    # one that has sat unchanged for `suggestion_settle_s`.
    suggestions: str = "observe"
    suggestion_settle_s: int = 20
    # "accept": Jev judges each settled suggestion — accept / wait / dismiss / ask you — and he
    # presses Tab+Enter himself. Only in panes he spawned (meta.owner=hearthsmith), never in one
    # you are typing in, and never while a sibling of the same goal is still working.
    # Requires suggestions: accept. Each press is a recorded run.
    suggestion_accept_min_p: float = 0.7
    # Panes an agent org spawned for itself (goal-orchestrator skill stamps role=spec|impl) may
    # be pressed too; goals-orch stays report-only — its suggestions are goal-level, your call.
    suggestion_accept_roles: list[str] = field(default_factory=lambda: ["impl", "spec"])
    # Stage 3: when the pane carries meta.goal, gather the org (spec text, siblings' last
    # answers, bus reports, work queue) and have `compose.judge_model` condense it for Jev;
    # a suggestion that depends on unfinished work waits regardless of the verdict.
    suggestion_org_aware: bool = True
    # `hearthsmith say` routed to a pane: type into the agent's prompt when it is idle (it acts now);
    # a busy pane gets an inbox message instead (it sees it on its next read_messages)
    say_types_into_idle_pane: bool = True


@dataclass
class DesktopCfg:
    # False = quiet: act through AT-SPI actions/EditableText only, never the real mouse or
    # keyboard, so he can work while you work. True = he may drive the shared cursor.
    hands: bool = False
    max_steps: int = 12
    # Before reporting success, take one look at the screen and ask the local vision model
    # whether the goal is visibly done. Costs ~10s, once per task, only when it claims done.
    verify: bool = True


@dataclass
class BrowserCfg:
    """His own Chrome. Browser errands run here (jev-ultrafast + CDP beats the accessibility
    tree inside a page); everything else stays on AT-SPI in whatever app you have open."""
    binary: str = "google-chrome-stable"
    # Chrome 136+ refuses remote debugging on the default profile, so he gets his own.
    profile: Path = Path.home() / ".config/hearthsmith/chrome"
    cdp_port: int = 9222
    autostart: bool = True


@dataclass
class NagCfg:
    min_gap_minutes: int = 45
    quiet_hours: tuple[int, int] = (23, 8)  # local; no nags from 23:00 to 08:00
    snooze_default_minutes: int = 120
    markdown_file: Path | None = Path.home() / "tasks.md"
    channels: list[str] = field(default_factory=lambda: ["notify", "hyperpanes"])


@dataclass
class BriefCfg:
    """Morning brief + evening wrap (brief.py). Local times, HH:MM."""
    enabled: bool = True
    morning: str = "08:30"
    morning_until: int = 12       # not at your desk by noon → no morning brief that day
    evening: str = "18:30"        # up to quiet hours; after that it waits for tomorrow's
    # hold the brief until you've touched keyboard/mouse within this many seconds
    wait_for_you: bool = True
    present_within_s: int = 120


@dataclass
class VoiceCfg:
    """How he sounds. Zero-shot clone from one reference clip, so the voice IS the clip: swap
    `ref` (and its transcript `ref_text`) and he speaks like someone else.

    Engines, tried in order until one speaks; the first refusal in a process sticks so a dead
    engine costs one failed call, not one per line:
      pocket Kyutai Pocket TTS (100M) on 2 CPU cores in the same warm server. Streams: first
            sound ~0.2s, faster than realtime. Needs the clip only. Weights are gated on HF
            (accept terms at hf.co/kyutai/pocket-tts once).
      auk   Tencent AuK on the HF space (gradio_client). Best clone; wants ~17 GiB so it can't
            run here, and free ZeroGPU quota is ~8 lines/day.
      qwen  Qwen3-TTS-0.6B-Base on this box, warm server at `qwen_url` (~/dev/hearthsmith-voice,
            hearthsmith-voice.service). fp32 on the 2080 Ti, ~6s a line, unlimited."""
    enabled: bool = True
    engines: list[str] = field(default_factory=lambda: ["pocket", "auk", "qwen"])
    # WoW dwarf NPC lines, 16s. A gruff blacksmith should sound like one.
    ref: Path = Path(__file__).resolve().parent.parent.parent / "assets/voice/dwarf.wav"
    ref_text: Path = Path(__file__).resolve().parent.parent.parent / "assets/voice/dwarf.txt"
    # auk
    space: str = "tencent/AuK"
    variant: str = "AuK (Base)"  # or "AuK-Flash ⚡": 4 steps, faster, rougher
    seed: int = 42
    hf_token_env: str = "HF_TOKEN"  # optional; anonymous ZeroGPU quota is small
    # he talks at dwarf pace; the space needs to be told how long the clip is
    words_per_second: float = 2.6
    max_chunk_seconds: float = 14.0  # auk: pack sentences, each call costs ~20s flat
    stream_chunk_seconds: float = 4.0  # qwen: short chunks, first one plays while the next is made
    # qwen
    qwen_url: str = "http://127.0.0.1:7861"
    qwen_language: str = "English"
    timeout_seconds: int = 150  # space queue + ~20s synth; heartbeat has 300
    # PipeWire sink name (`pactl list sinks short`); "" = default output
    sink: str = ""
    cache_dir: Path = STATE_DIR / "voice"


@dataclass
class Config:
    decide: DecideCfg = field(default_factory=DecideCfg)
    compose: ComposeCfg = field(default_factory=ComposeCfg)
    hyperpanes: HyperpanesCfg = field(default_factory=HyperpanesCfg)
    desktop: DesktopCfg = field(default_factory=DesktopCfg)
    browser: BrowserCfg = field(default_factory=BrowserCfg)
    nag: NagCfg = field(default_factory=NagCfg)
    brief: BriefCfg = field(default_factory=BriefCfg)
    voice: VoiceCfg = field(default_factory=VoiceCfg)
    db_path: Path = STATE_DIR / "hearthsmith.db"
    sprite_path: Path = STATE_DIR / "sprite.json"


def _merge(dc, data: dict):
    for k, v in (data or {}).items():
        if not hasattr(dc, k):
            continue
        cur = getattr(dc, k)
        if hasattr(cur, "__dataclass_fields__") and isinstance(v, dict):
            _merge(cur, v)
        elif isinstance(cur, Path) or (cur is None and k.endswith(("_file", "_path"))):
            setattr(dc, k, Path(v).expanduser() if v else None)
        elif isinstance(cur, tuple):
            setattr(dc, k, tuple(v))
        else:
            setattr(dc, k, v)
    return dc


ENV_FILE = Path.home() / ".config/hearthsmith/env"


def load_env(path: Path = ENV_FILE) -> None:
    """KEY=value lines → os.environ (never overrides). So `hearthsmith say` works from the sprite,
    a shell, or a harness without each of them knowing where the keys live."""
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def load(path: Path = CONFIG_PATH) -> Config:
    load_env()
    cfg = Config()
    if path.exists():
        _merge(cfg, yaml.safe_load(path.read_text()) or {})
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return cfg
