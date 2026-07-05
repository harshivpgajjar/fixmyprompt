#!/usr/bin/env python3
"""Whetstone Coach Gate — the UserPromptSubmit hook entrypoint.

Flow (see SPEC.md Phase 2):
  1. Resubmit branch: if a one-shot pending flag is set, consume it and either
     accept (bare `y` -> send the refined text via additionalContext) or pass
     through untouched. This makes blocking twice in a row impossible.
  2. Backstop: a late paste closely matching the last refined text passes.
  3. Gate: local, zero-latency silence checks (command / continuation / paste /
     short / non-execute / cooldown / sigil).
  4. Refine: Haiku (fail-open). If nothing to gain, pass through.
  5. Present: copy refined to clipboard, arm the one-shot flag, optionally paste
     it into the input line via tmux, and BLOCK with a banner.

Contract: JSON on stdin (fields incl. `prompt`, `session_id`). JSON on stdout.
Never raises to the caller — any failure passes the prompt through (exit 0).

This is a working baseline; a Fable 5 subagent hardens edge cases + tests it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whetstone import (  # noqa: E402
    ACTION_ACCEPT,
    ACTION_COACH,
    ACTION_EDIT,
    ACTION_PASS,
    config,
    refiner,
    scorelog,
    scorer,
    state,
)

_CONFIRM = {"y", "ye", "yes", "yep", "yeah", "ok", "okay", "k", "send", "send it", "ship it", "go"}


def _emit_passthrough() -> None:
    """No output = prompt goes to the model unchanged."""
    sys.exit(0)


def _emit_block(reason: str) -> None:
    json.dump({"decision": "block", "reason": reason}, sys.stdout)
    sys.exit(0)


def _emit_accept(refined: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "The user reviewed and approved this refined version of the "
                    "prompt they just tried to send. Treat THIS as their actual "
                    "request and act on it directly:\n\n" + refined
                ),
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def _clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=3, check=False)
    except Exception:
        pass


def _tmux_inject(text: str, delay_ms: int) -> None:
    """Detached: after a short delay, paste the refined text into the tmux pane
    so it lands in the input line, editable. Bracketed paste (-p) keeps newlines
    from submitting. Pane-targeted so it survives focus changes."""
    pane = os.environ.get("TMUX_PANE")
    if not os.environ.get("TMUX") or not pane:
        return
    try:
        script = (
            f"sleep {max(0, delay_ms) / 1000.0}; "
            f"printf %s {shell_quote(text)} | tmux load-buffer -b whetstone - ; "
            f"tmux paste-buffer -p -d -b whetstone -t {shell_quote(pane)}"
        )
        subprocess.Popen(
            ["bash", "-lc", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _banner(refined: str, tip: str) -> str:
    rule = "─" * 52
    out = [
        "── Whetstone · refined prompt (copied to clipboard) ──",
        "",
        refined.strip(),
    ]
    if tip.strip():
        out += ["", f"why: {tip.strip()}"]
    out += [
        rule,
        "[y ⏎] send refined   ·   [⌘V, edit, ⏎] tweak   ·   [type anything] send your own",
    ]
    return "\n".join(out)


def _read_stdin() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def main() -> None:
    try:
        data = _read_stdin()
        prompt = data.get("prompt", "") or ""
        session_id = data.get("session_id", "") or "nosession"
        cfg = config.load()

        # 1. RESUBMIT BRANCH (one-shot bypass) — guarantees no double-block.
        pending = state.take_pending(session_id)
        if pending is not None:
            refined = pending.get("refined", "")
            norm = prompt.strip().lower().rstrip(".! ")
            if norm in _CONFIRM:
                scorelog.log(prompt, scorer.classify(prompt), ACTION_ACCEPT, cfg)
                _emit_accept(refined)
            else:
                scorelog.log(prompt, scorer.classify(prompt), ACTION_EDIT, cfg)
                _emit_passthrough()

        # 2. BACKSTOP — late paste of a recently refined prompt.
        recent = state.recent_refined()
        if recent and state.token_overlap(prompt, recent) > 0.6:
            scorelog.log(prompt, scorer.classify(prompt), ACTION_EDIT, cfg)
            _emit_passthrough()

        # sigil mode: only engage on prompts that opt in; strip sigil for processing.
        body = prompt
        if cfg.get("mode") == "sigil":
            sig = cfg.get("sigil", "??")
            if prompt.lstrip().startswith(sig):
                body = prompt.lstrip()[len(sig):].lstrip()
            else:
                scorelog.log(prompt, scorer.classify(prompt), ACTION_PASS, cfg)
                _emit_passthrough()

        features = scorer.classify(body)

        # 3. GATE (local, zero-latency) + cooldown.
        if not scorer.should_coach(features, cfg) or state.cooldown_active(session_id, cfg):
            scorelog.log(prompt, features, ACTION_PASS, cfg)
            _emit_passthrough()

        # 4. REFINE (fail-open).
        result = refiner.refine(body, cfg=cfg)
        if not result.get("needs_refinement") or not result.get("refined", "").strip():
            scorelog.log(prompt, features, ACTION_PASS, cfg)
            _emit_passthrough()

        # 5. PRESENT.
        refined = result["refined"].strip()
        tip = result.get("tip", "")
        _clipboard(refined)
        state.set_pending(session_id, refined)
        state.mark_coached(session_id)
        if cfg.get("inject"):
            _tmux_inject(refined, cfg.get("inject_delay_ms", 450))
        scorelog.log(prompt, features, ACTION_COACH, cfg)
        _emit_block(_banner(refined, tip))
    except SystemExit:
        raise
    except Exception:
        # Absolute fail-open: never break the user's turn.
        _emit_passthrough()


if __name__ == "__main__":
    main()
