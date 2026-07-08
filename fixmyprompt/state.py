"""Session state for the Coach Gate: the one-shot bypass flag, the anti-nag
cooldown, and the late-paste backstop cache.

The load-bearing invariant: after the gate blocks a prompt it writes a pending
flag; the very next submission in that session consumes the flag and passes
through unconditionally. The hook therefore can NEVER block twice in a row —
no refine loops are possible, and "send my original" always costs one Enter.

This guarantee assumes SERIAL submission within a session (the real usage — a
human submits one prompt at a time). take_pending is read-then-unlink without
a lock, so many simultaneous submissions in one session could race; the
worst case is a redundant coach, never a crash or a wrong send (the gate still
fails open and emits valid-JSON-or-empty on every path).
"""
from __future__ import annotations

import json
import os
import re
import time

from . import config

PENDING_DIR = config.RUNTIME_DIR / "pending"
COOLDOWN_DIR = config.RUNTIME_DIR / "cooldown"
BACKSTOP_PATH = config.RUNTIME_DIR / "backstop.json"


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "nosession")[:128]


def _chmod(path, mode: int) -> None:
    # No-op on Windows/unsupported FS (which uses ACLs, not POSIX bits) — harmless.
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _secure_write(path, text: str) -> None:
    """Write owner-only. These files hold the user's in-flight PROMPT text, so on
    a shared host they must not be world-readable.

    The mode is passed to the OPEN call itself (os.O_CREAT with mode=0o600), not
    applied afterward via chmod — a separate write-then-chmod has a TOCTOU
    window where the file briefly exists at the default umask (typically 0o644)
    before being narrowed, during which another local process could read it."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # os.fdopen's context manager owns fd from here — its __exit__ closes it
    # exactly once, on success or exception, so no separate close/except needed.
    with os.fdopen(fd, "w") as f:
        f.write(text)
    # Belt-and-suspenders: O_CREAT only sets the mode on a NEW file — if the path
    # already existed (e.g. reusing a session id) its old mode carries over, so
    # narrow it explicitly too. Cheap and idempotent when it's already 0600.
    _chmod(path, 0o600)


def _ensure() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    COOLDOWN_DIR.mkdir(parents=True, exist_ok=True)
    # Owner-only dirs: a 0700 dir blocks other local users from reaching any file
    # inside it (they can't traverse it), protecting the pending/cooldown/backstop
    # files and the prompt log regardless of individual file modes.
    for d in (config.RUNTIME_DIR, PENDING_DIR, COOLDOWN_DIR):
        _chmod(d, 0o700)


def set_pending(session_id: str, refined_text: str, original_text: str = "") -> None:
    """Arm the one-shot bypass for the next submission in this session. Stores the
    refined text (for a `y` accept) AND the user's original (so they can send it
    unchanged with `n`, rejecting the rewrite)."""
    _ensure()
    path = PENDING_DIR / f"{_safe(session_id)}.json"
    _secure_write(path, json.dumps({"ts": time.time(), "refined": refined_text,
                                    "original": original_text}))
    # also seed the backstop cache for a late paste that misses the one-shot
    try:
        _secure_write(BACKSTOP_PATH, json.dumps({"ts": time.time(), "refined": refined_text}))
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
    _secure_write(COOLDOWN_DIR / f"{_safe(session_id)}", str(time.time()))


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
