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

from . import config

LOG_PATH = config.RUNTIME_DIR / "prompt-log.jsonl"

# Per-invocation context the hook sets once, so every log record can carry the
# session and working directory without threading them through every call site.
# Powers outcome-tracking (within-session correction sequences) and per-project
# tuning (cwd → project).
_CTX = {"session_id": None, "cwd": None}


def set_context(session_id=None, cwd=None) -> None:
    _CTX["session_id"] = session_id
    _CTX["cwd"] = cwd

# Match the key formats people actually paste. The unbounded runs are over
# classes that EXCLUDE '.', and JWT segments are '.'-separated, so there's no
# catastrophic-backtracking ambiguity. Bare-word "password"/"token" are NOT
# redacted on their own (that would hide normal prompts like "add a password
# field"); only the key=value form is.
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|"                       # OpenAI / Anthropic (sk-, sk-ant-, sk-proj-)
    r"r8_[A-Za-z0-9]{8,}|"                          # Replicate
    r"AIza[A-Za-z0-9_-]{10,}|"                      # Google
    r"gh[pousr]_[A-Za-z0-9]{20,}|"                  # GitHub tokens
    r"github_pat_[A-Za-z0-9_]{20,}|"                # GitHub fine-grained PAT
    r"glpat-[A-Za-z0-9_-]{10,}|"                    # GitLab PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"                # Slack
    r"AKIA[0-9A-Z]{16}|"                            # AWS access key id
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*|"  # JWT (header.payload.sig)
    r"Bearer\s+[A-Za-z0-9._/+-]{12,}|"              # bearer token
    r"-----BEGIN|"                                  # PEM private key
    r"(api[_-]?key|secret|password|passwd|token|access[_-]?token|private[_-]?key)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


def _preview(prompt: str, cfg: dict) -> str:
    if not cfg.get("log_previews", True):
        return ""
    # The preview is only the first 140 chars, so scan a bounded slice — a secret
    # past that can't appear in the preview anyway, and this keeps _preview O(1)
    # on a huge paste.
    prompt = (prompt or "")[:2000]
    if _SECRET.search(prompt):
        return "[redacted: possible secret]"
    p = re.sub(r"\s+", " ", prompt).strip()
    return p[:140]


def log(prompt: str, features: dict, action: str, cfg: dict | None = None) -> None:
    """Append one record. Never raises."""
    cfg = cfg or config.load()
    try:
        config.ensure_runtime_dir()
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
            "session_id": _CTX["session_id"],
            "cwd": _CTX["cwd"],
        }
        config.secure_append(LOG_PATH, json.dumps(record) + "\n")
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
            if not isinstance(rec, dict):
                continue
            # Normalize ts to a float here so every downstream consumer (report,
            # progress, streak) is safe against a hand-corrupted log line.
            try:
                rec["ts"] = float(rec.get("ts", 0) or 0)
            except (TypeError, ValueError):
                rec["ts"] = 0.0
            if rec["ts"] >= cutoff:
                out.append(rec)
    except Exception:
        return out
    return out
