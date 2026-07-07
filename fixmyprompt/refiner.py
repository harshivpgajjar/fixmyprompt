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
import subprocess
from pathlib import Path

from . import config

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_PATH = _PROMPT_DIR / "coach_system_prompt.md"

_EMPTY = {"needs_refinement": False, "mode": "other", "refined": "", "tip": ""}


def _daemon_up() -> bool:
    """True if the daemon backend should be used: enabled (use_daemon) AND running."""
    try:
        from . import daemon
        if not config.load().get("use_daemon"):
            return False
        return daemon.is_running()
    except Exception:
        return False


def _system_prompt(context: str) -> str:
    try:
        base = _SYSTEM_PATH.read_text()
    except Exception:
        base = (
            "You are FixMyPrompt, a prompt coach. Return strict JSON "
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


def _balanced_json_blocks(text: str) -> list[str]:
    """Yield each top-level {...} substring with balanced braces (string-aware),
    so trailing model chatter containing braces can't corrupt the candidate the
    way a greedy `\\{.*\\}` does."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "{":
            depth = 0
            in_str = esc = False
            j = i
            while j < n:
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[i:j + 1])
                        i = j
                        break
                j += 1
        i += 1
    return out


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    # direct parse, else each balanced {...} block
    for candidate in (text, *_balanced_json_blocks(text)):
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
    """Subscription path: `claude -p` authenticates from the user's logged-in
    Claude Code session — no API key required. This is the default backend so a
    distributable plugin works on any user's subscription out of the box.

    Sets FIXMYPROMPT_IN_REFINER so the nested `claude -p` session's own
    UserPromptSubmit hook no-ops instead of recursing into this gate forever."""
    combined = (
        system
        + "\n\n---\nHere is the user's raw prompt to evaluate. Return only the JSON.\n\n"
        + prompt
    )
    try:
        # Feed the prompt on stdin, not argv — argv is world-visible via `ps`,
        # and the prompt can contain sensitive content.
        proc = subprocess.run(
            ["claude", "-p", "--model", cfg["model"]],
            input=combined,
            capture_output=True,
            text=True,
            timeout=cfg.get("refine_timeout_sec", 15),
            env={**os.environ, "FIXMYPROMPT_IN_REFINER": "1"},
        )
        if proc.returncode != 0:
            return None
        return _extract_json(proc.stdout)
    except FileNotFoundError:
        return None  # claude CLI not on PATH — let the API path try
    except Exception:
        return None


def refine(prompt: str, context: str | None = None, cfg: dict | None = None,
           cwd: str | None = None) -> dict:
    """Return {needs_refinement, mode, refined, tip}. Never raises; fail-open."""
    cfg = cfg or config.load()
    if context is None:
        context = load_user_context()
    try:  # per-project hint + the user's recurring acceptance criteria
        from . import context_hints
        block = context_hints.context_block(cwd)
        if block:
            context = (context + "\n\n" + block).strip()
    except Exception:
        pass
    system = _system_prompt(context)
    # Backend selection. The live Coach Gate needs a FAST call, so the default
    # is the API path (~1s) — used only when ANTHROPIC_API_KEY is set. The
    # subscription `claude -p` path works but spins up a full agent session
    # (~20-40s), far too slow for a submit-time hook, so it is OPT-IN only via
    # FIXMYPROMPT_BACKEND=cli (for the on-demand CLI, where a wait is acceptable).
    # With no key and no opt-in, refine() returns "no refinement" and callers
    # fall back to the instant local scaffold (suggest.py) or the /refine skill.
    backend = os.environ.get("FIXMYPROMPT_BACKEND", "").lower()
    if backend == "cli":
        backends = (_via_cli, _via_api)
    else:  # default / "api": fast API path only
        backends = (_via_api,)
    obj = None
    # Warm-daemon path first when it's up: ~1.5s subscription rewrites, no key.
    # When the daemon ANSWERS (dict), it's authoritative — return its verdict,
    # including a definitive "nothing to add", so we never make a redundant
    # second LLM call. Only a None (daemon down/timeout/miss) falls through.
    if _daemon_up():
        from . import daemon
        d = daemon.refine(prompt, timeout=cfg.get("daemon_timeout", 2.5), context=context)
        if isinstance(d, dict):
            return _normalize(d)
        # daemon was down / timed out -> fall through to the configured backends
    for b in backends:
        obj = b(prompt, system, cfg)
        if obj is not None:
            break
    result = _normalize(obj)
    # guard: if the model flags refinement but gives no refined text, treat as no-op
    if result["needs_refinement"] and not result["refined"].strip():
        return dict(_EMPTY)
    return result
