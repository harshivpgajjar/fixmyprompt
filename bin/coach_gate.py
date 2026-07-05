#!/usr/bin/env python3
"""Whetstone Coach Gate — the UserPromptSubmit hook entrypoint.

Flow (see SPEC.md Phase 2):
  1. Resubmit branch: if the one-shot pending flag is set, consume it and either
     accept (bare confirm like "y" -> send the refined text via
     additionalContext) or pass through untouched. This branch runs before
     everything else — including mode checks — so blocking twice in a row is
     impossible regardless of configuration.
  2. Backstop: a late paste closely matching the last refined text passes.
  3. Mode guard: only "always" and "sigil" ever intercept. "off" and any
     unknown mode value pass straight through (silence-first).
  4. Gate: local, zero-latency silence checks (command / continuation / paste /
     short / non-execute / low-quality threshold) plus the per-session cooldown.
  5. Refine: Haiku (fail-open: error, timeout, junk output, or "already good"
     all pass the prompt through untouched).
  6. Present: arm the one-shot flag FIRST (never block without loop protection
     on disk), then copy refined to clipboard, optionally paste it into the
     input line via tmux, and BLOCK with a banner.

Contract (verified platform ground truth):
  - stdin: JSON ({"prompt": ..., "session_id": ..., ...}).
  - Pass-through: print nothing, exit 0.
  - Block: stdout {"decision": "block", "reason": "<text shown to user>"}, exit 0.
  - Inject: stdout {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
    "additionalContext": "..."}}, exit 0.
  - NEVER raises to the caller; any internal failure passes the prompt through.

Every decided path logs exactly one scorelog record (pass/coach/accept/edit).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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

# Only these modes may ever intercept a prompt. Anything else ("off", a typo,
# a future mode this version doesn't know) is treated as silence — the gate
# must fail quiet, never fail loud.
_INTERCEPTING_MODES = ("always", "sigil")


def _emit_passthrough() -> None:
    """No output = prompt goes to the model unchanged."""
    sys.exit(0)


def _emit_block(reason: str) -> None:
    json.dump({"decision": "block", "reason": reason}, sys.stdout)
    sys.stdout.flush()
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
    sys.stdout.flush()
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
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _refine(body: str, cfg: dict) -> dict:
    # test seam — active ONLY when WHETSTONE_FAKE_REFINE is set (never set in
    # production; when unset this function is exactly refiner.refine).
    # The env value is a JSON refine-result, or the sentinel "RAISE" to
    # simulate a crashing refiner. Each consultation appends the body it
    # received to $WHETSTONE_HOME/fake-refine-calls so tests can prove the
    # refiner was (or was NOT) consulted, and that the sigil was stripped.
    fake = os.environ.get("WHETSTONE_FAKE_REFINE")
    if fake:
        try:
            config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            with (config.RUNTIME_DIR / "fake-refine-calls").open("a") as fh:
                fh.write(body.replace("\n", " ")[:200] + "\n")
        except Exception:
            pass
        if fake == "RAISE":
            raise RuntimeError("test seam: simulated refiner crash")
        return json.loads(fake)
    return refiner.refine(body, cfg=cfg)


def main() -> None:
    try:
        data = _read_stdin()
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            prompt = ""
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = "nosession"
        cfg = config.load()

        # 1. RESUBMIT BRANCH (one-shot bypass) — guarantees no double-block.
        # Runs before mode/backstop/gate so the guarantee holds even if the
        # config changed between the block and the resubmission.
        pending = state.take_pending(session_id)
        if pending is not None:
            refined = pending.get("refined") if isinstance(pending, dict) else None
            refined = refined if isinstance(refined, str) else ""
            norm = prompt.strip().lower().rstrip(".! ")
            if norm in _CONFIRM and refined.strip():
                scorelog.log(prompt, scorer.classify(prompt), ACTION_ACCEPT, cfg)
                _emit_accept(refined)
            # Anything else — edited text, an override, a decline, even a bare
            # confirm when there is no refined text to send — passes through.
            scorelog.log(prompt, scorer.classify(prompt), ACTION_EDIT, cfg)
            _emit_passthrough()

        # 2. BACKSTOP — late paste of a recently refined prompt.
        recent = state.recent_refined()
        if recent and state.token_overlap(prompt, recent) > 0.6:
            scorelog.log(prompt, scorer.classify(prompt), ACTION_EDIT, cfg)
            _emit_passthrough()

        # 3. MODE GUARD — "off" and unknown modes never intercept.
        mode = cfg.get("mode")
        if mode not in _INTERCEPTING_MODES:
            scorelog.log(prompt, scorer.classify(prompt), ACTION_PASS, cfg)
            _emit_passthrough()

        # Sigil mode: only engage on prompts that opt in; strip the sigil
        # before classification and refinement.
        body = prompt
        if mode == "sigil":
            sig = str(cfg.get("sigil") or "??")
            if prompt.lstrip().startswith(sig):
                body = prompt.lstrip()[len(sig):].lstrip()
            else:
                scorelog.log(prompt, scorer.classify(prompt), ACTION_PASS, cfg)
                _emit_passthrough()

        features = scorer.classify(body)

        # 4. GATE (local, zero-latency) + cooldown.
        if not scorer.should_coach(features, cfg) or state.cooldown_active(session_id, cfg):
            scorelog.log(prompt, features, ACTION_PASS, cfg)
            _emit_passthrough()

        # 5. REFINE (fail-open). A crashing or junk-returning refiner must
        # never block or error — catch locally so the pass is still recorded
        # as exactly one ACTION_PASS log record.
        try:
            result = _refine(body, cfg)
        except Exception:
            result = None
        if not isinstance(result, dict):
            result = {}
        refined = result.get("refined")
        refined = refined.strip() if isinstance(refined, str) else ""
        if not result.get("needs_refinement") or not refined:
            scorelog.log(prompt, features, ACTION_PASS, cfg)
            _emit_passthrough()

        # 6. PRESENT. Arm the one-shot bypass BEFORE emitting the block: if
        # arming fails we fall to the outer handler and pass through instead —
        # never show a block without loop protection already on disk.
        tip = result.get("tip")
        tip = tip if isinstance(tip, str) else ""
        state.set_pending(session_id, refined)
        state.mark_coached(session_id)
        if not os.environ.get("WHETSTONE_FAKE_REFINE"):
            # test seam guard: while the fake refiner is active (tests only),
            # skip desktop side effects so tests never touch the real
            # clipboard or tmux. No-op in production (env var unset).
            _clipboard(refined)
            if cfg.get("inject"):
                _tmux_inject(refined, cfg.get("inject_delay_ms", 450))
        scorelog.log(prompt, features, ACTION_COACH, cfg)
        _emit_block(_banner(refined, tip))
    except SystemExit:
        raise
    except BaseException:
        # Absolute fail-open: never break the user's turn.
        _emit_passthrough()


if __name__ == "__main__":
    main()
