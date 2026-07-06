"""Session state for the Coach Gate: the one-shot bypass flag, the anti-nag
cooldown, and the late-paste backstop cache.

The load-bearing invariant: after the gate blocks a prompt it writes a pending
flag; the very next submission in that session consumes the flag and passes
through unconditionally. The hook therefore can NEVER block twice in a row —
no refine loops are possible, and "send my original" always costs one Enter.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

PENDING_DIR = config.RUNTIME_DIR / "pending"
COOLDOWN_DIR = config.RUNTIME_DIR / "cooldown"
BACKSTOP_PATH = config.RUNTIME_DIR / "backstop.json"


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "nosession")[:128]


def _ensure() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    COOLDOWN_DIR.mkdir(parents=True, exist_ok=True)


def set_pending(session_id: str, refined_text: str) -> None:
    """Arm the one-shot bypass for the next submission in this session."""
    _ensure()
    path = PENDING_DIR / f"{_safe(session_id)}.json"
    path.write_text(json.dumps({"ts": time.time(), "refined": refined_text}))
    # also seed the backstop cache for a late paste that misses the one-shot
    try:
        BACKSTOP_PATH.write_text(json.dumps({"ts": time.time(), "refined": refined_text}))
    except Exception:
        pass


def take_pending(session_id: str, ttl: int | None = None) -> dict | None:
    """Read AND consume the one-shot flag. Returns {ts, refined} if fresh, else None.
    Always deletes the flag on read (consumed), so it can fire at most once."""
    cfg_ttl = ttl if ttl is not None else config.load()["pending_ttl_sec"]
    path = PENDING_DIR / f"{_safe(session_id)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = None
    try:
        path.unlink()  # consume regardless of freshness
    except Exception:
        pass
    if not data:
        return None
    if time.time() - data.get("ts", 0) > cfg_ttl:
        return None
    return data


def recent_refined(ttl: int | None = None) -> str | None:
    """Backstop: the last refined text, if within the backstop window."""
    cfg_ttl = ttl if ttl is not None else config.load()["backstop_ttl_sec"]
    try:
        data = json.loads(BACKSTOP_PATH.read_text())
    except Exception:
        return None
    if time.time() - data.get("ts", 0) > cfg_ttl:
        return None
    return data.get("refined")


def mark_coached(session_id: str) -> None:
    _ensure()
    (COOLDOWN_DIR / f"{_safe(session_id)}").write_text(str(time.time()))


def cooldown_active(session_id: str, cfg: dict) -> bool:
    path = COOLDOWN_DIR / f"{_safe(session_id)}"
    try:
        last = float(path.read_text())
    except Exception:
        return False
    return (time.time() - last) < cfg.get("cooldown_sec", 0)


def token_overlap(a: str, b: str) -> float:
    """Jaccard-ish token overlap for the late-paste backstop."""
    ta = set(re.findall(r"\w+", (a or "").lower()))
    tb = set(re.findall(r"\w+", (b or "").lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
