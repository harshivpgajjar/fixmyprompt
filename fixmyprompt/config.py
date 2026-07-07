"""Configuration for FixMyPrompt.

Precedence (low -> high): DEFAULTS < config.json < PCOACH_* environment vars.
Runtime home is ~/.claude/fixmyprompt (stable, git-backed, off the iCloud Desktop).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
RUNTIME_DIR = Path(os.environ.get("FIXMYPROMPT_HOME", HOME / ".claude" / "fixmyprompt"))
CONFIG_PATH = RUNTIME_DIR / "config.json"

DEFAULTS = {
    # mode: how the live hook behaves.
    #   "always"  — block before send, show a refined version / local scaffold
    #   "sigil"   — same as always, but only for prompts starting with the sigil
    #   "whisper" — DON'T block; inject a coaching note so the main session model
    #               (subscription, no key, no extra call) asks for the missing
    #               piece. Fully subscription, zero key, zero added latency.
    #   "off"     — never intercept; /refine and logging still work
    "mode": "off",
    "sigil": "??",
    # silence gate: prompts with fewer than this many words are never coached.
    # 4 catches the short vague asks people actually type ("fix the mobile
    # version", "build me a dashboard") while ≤3-word instructions and
    # continuations stay silent. Raise it if you find it too eager.
    "min_words": 4,
    # anti-nag: don't coach again in the same session within this many seconds.
    "cooldown_sec": 90,
    # one-shot bypass flag lifetime (the "second Enter always sends" guarantee).
    "pending_ttl_sec": 600,
    # backstop: a late paste within this window of a refined text passes through.
    "backstop_ttl_sec": 1800,
    # tmux tier: if true and running inside tmux, inject the refined text into the
    # input line via paste-buffer (~0.5s after the block). Clipboard is the fallback.
    "inject": True,
    "inject_delay_ms": 450,
    # model + timeout for the refiner (fail-open on any error/timeout).
    # 15s gives `claude -p` room for a cold start; keep it below the hook's
    # 20s timeout so the refiner fails open gracefully before the hook is killed.
    "model": "claude-haiku-4-5",
    "refine_timeout_sec": 15,
    # warm daemon: fast (~1.5s) AI-written rewrites on your subscription, no key.
    # Enabled by `fixmyprompt daemon on`. When up, the block gate shows written
    # rewrites with y-to-send instead of the fill-in scaffold.
    "use_daemon": False,
    "daemon_timeout": 4.0,  # covers a cold first request (~2.6s); warm is ~1.7s
    # remembers the mode `daemon on` overrode, so `daemon off` can restore it.
    "daemon_prior_mode": None,
    # coaching only engages when the composite quality score is below this.
    "coach_below_quality": 0.7,
    # tutorial mode: coach EVERY real prompt (well-formed ones get an
    # affirmation) so you learn what "good" looks like. `fixmyprompt tutorial on`.
    "tutorial": False,
    # append a best-suited model + effort suggestion to the coaching output.
    "suggest_model": True,
    # surface a relevant Claude Code feature tip (/clear before new work, /goal
    # for big tasks, plan mode, subagents) when the prompt signals the situation.
    "cc_tips": True,
    # write full (redacted) prompt previews to the log for the weekly report.
    "log_previews": True,
}

# Env var -> (config key, caster)
_ENV = {
    "PCOACH_MODE": ("mode", str),
    "PCOACH_SIGIL": ("sigil", str),
    "PCOACH_MIN_WORDS": ("min_words", int),
    "PCOACH_COOLDOWN": ("cooldown_sec", int),
    "PCOACH_INJECT": ("inject", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
    "PCOACH_MODEL": ("model", str),
    "PCOACH_TIMEOUT": ("refine_timeout_sec", int),
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except Exception:
        pass  # fail open to defaults — config must never break the hook
    for env_key, (cfg_key, cast) in _ENV.items():
        raw = os.environ.get(env_key)
        if raw is not None and raw != "":
            try:
                cfg[cfg_key] = cast(raw)
            except Exception:
                pass
    return cfg


def save(patch: dict) -> dict:
    """Merge a patch into config.json and return the new full config."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    current = {}
    try:
        if CONFIG_PATH.exists():
            current = json.loads(CONFIG_PATH.read_text())
    except Exception:
        current = {}
    current.update({k: v for k, v in patch.items() if k in DEFAULTS})
    CONFIG_PATH.write_text(json.dumps(current, indent=2) + "\n")
    return load()
