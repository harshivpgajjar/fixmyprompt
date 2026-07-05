"""The refiner: turns a raw prompt into {needs_refinement, mode, refined, tip}.

Two backends, tried in order, both fail-open (any error/timeout -> no refinement,
so the prompt just sends normally and the coach can never lock the user out):
  1. Anthropic Messages API via urllib, if ANTHROPIC_API_KEY is set.
  2. `claude -p --model <model>` subprocess fallback (zero-config, slower).

The user's personal context (core.md + design-taste.md) is injected so the
refiner respects their stack, voice, and taste — and is mode-aware, so it never
"fixes" an intentional explore prompt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import config

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_PATH = _PROMPT_DIR / "coach_system_prompt.md"

_EMPTY = {"needs_refinement": False, "mode": "other", "refined": "", "tip": ""}


def _system_prompt(context: str) -> str:
    try:
        base = _SYSTEM_PATH.read_text()
    except Exception:
        base = (
            "You are Whetstone, a prompt coach. Return strict JSON "
            '{"needs_refinement":bool,"mode":str,"refined":str,"tip":str}. '
            "Preserve the user's voice; be mode-aware; never fix explore prompts."
        )
    return base.replace("<context>", context or "(none)")


def load_user_context() -> str:
    """Best-effort personalization from the user's memory files."""
    home = Path(os.path.expanduser("~")) / ".claude" / "memory"
    parts = []
    for name in ("core.md", "design-taste.md"):
        p = home / name
        try:
            if p.exists():
                parts.append(f"### {name}\n{p.read_text()[:3000]}")
        except Exception:
            pass
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    # direct parse, else first {...} block
    for candidate in (text, *re.findall(r"\{.*\}", text, re.DOTALL)):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and "needs_refinement" in obj:
                return obj
        except Exception:
            continue
    return None


def _normalize(obj: dict | None) -> dict:
    if not obj:
        return dict(_EMPTY)
    return {
        "needs_refinement": bool(obj.get("needs_refinement")),
        "mode": str(obj.get("mode") or "other"),
        "refined": str(obj.get("refined") or ""),
        "tip": str(obj.get("tip") or ""),
    }


def _via_api(prompt: str, system: str, cfg: dict) -> dict | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import urllib.request

    body = json.dumps(
        {
            "model": cfg["model"],
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["refine_timeout_sec"]) as resp:
            payload = json.loads(resp.read().decode())
        text = "".join(
            b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
        )
        return _extract_json(text)
    except Exception:
        return None


def _via_cli(prompt: str, system: str, cfg: dict) -> dict | None:
    combined = (
        system
        + "\n\n---\nHere is the user's raw prompt to evaluate. Return only the JSON.\n\n"
        + prompt
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", cfg["model"], combined],
            capture_output=True,
            text=True,
            timeout=cfg["refine_timeout_sec"] + 6,
        )
        if proc.returncode != 0:
            return None
        return _extract_json(proc.stdout)
    except Exception:
        return None


def refine(prompt: str, context: str | None = None, cfg: dict | None = None) -> dict:
    """Return {needs_refinement, mode, refined, tip}. Never raises; fail-open."""
    cfg = cfg or config.load()
    if context is None:
        context = load_user_context()
    system = _system_prompt(context)
    obj = _via_api(prompt, system, cfg)
    if obj is None:
        obj = _via_cli(prompt, system, cfg)
    result = _normalize(obj)
    # guard: if the model flags refinement but gives no refined text, treat as no-op
    if result["needs_refinement"] and not result["refined"].strip():
        return dict(_EMPTY)
    return result
