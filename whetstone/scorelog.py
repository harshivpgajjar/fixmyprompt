"""Append-only, privacy-conscious log of prompt quality — the fuel for the
weekly self-audit's prompting trend.

We store SCORES + metadata, plus a short redacted preview (opt-out via config).
Anything that looks like a secret suppresses the preview entirely. The log is
gitignored. This is measurement, never surveillance.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

LOG_PATH = config.RUNTIME_DIR / "prompt-log.jsonl"

_SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|r8_[A-Za-z0-9]{8,}|AIza[A-Za-z0-9_\-]{10,}|"
    r"ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN|"
    r"(api[_-]?key|secret|password|token)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


def _preview(prompt: str, cfg: dict) -> str:
    if not cfg.get("log_previews", True):
        return ""
    if _SECRET.search(prompt or ""):
        return "[redacted: possible secret]"
    p = re.sub(r"\s+", " ", (prompt or "")).strip()
    return p[:140]


def log(prompt: str, features: dict, action: str, cfg: dict | None = None) -> None:
    """Append one record. Never raises."""
    cfg = cfg or config.load()
    try:
        config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": round(time.time(), 1),
            "action": action,
            "word_count": features.get("word_count"),
            "mode": features.get("mode"),
            "is_continuation": features.get("is_continuation"),
            "has_constraints": features.get("has_constraints"),
            "has_done_criteria": features.get("has_done_criteria"),
            "has_reference": features.get("has_reference"),
            "is_design": features.get("is_design"),
            "quality": features.get("quality"),
            "gaps": features.get("gaps", []),
            "preview": _preview(prompt, cfg),
        }
        with LOG_PATH.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # logging must never disrupt the user's turn


def read(days: int | None = None) -> list[dict]:
    """Return log records, optionally only those within the last `days`."""
    if not LOG_PATH.exists():
        return []
    cutoff = (time.time() - days * 86400) if days else 0
    out = []
    try:
        for line in LOG_PATH.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("ts", 0) >= cutoff:
                out.append(rec)
    except Exception:
        return out
    return out
