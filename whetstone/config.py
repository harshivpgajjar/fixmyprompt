"""Configuration for Whetstone.

Precedence (low -> high): DEFAULTS < config.json < PCOACH_* environment vars.
Runtime home is ~/.claude/whetstone (stable, git-backed, off the iCloud Desktop).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
RUNTIME_DIR = Path(os.environ.get("WHETSTONE_HOME", HOME / ".claude" / "whetstone"))
CONFIG_PATH = RUNTIME_DIR / "config.json"

DEFAULTS = {
    # mode: how the live hook behaves.
    #   "always" — coach every qualifying execute-mode prompt (the intended UX)
    #   "sigil"  — only coach prompts that start with the sigil (opt-in per prompt)
    #   "off"    — never intercept; /refine and logging still work
    "mode": "off",
    "sigil": "??",
    # silence gate: prompts at or below this word count are never coached.
    "min_words": 12,
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
    # coaching only engages when the composite quality score is below this.
    "coach_below_quality": 0.7,
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
