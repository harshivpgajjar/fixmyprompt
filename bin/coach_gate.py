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
    suggest,
)

_CONFIRM = {"y", "ye", "yes", "yep", "yeah", "ok", "okay", "k", "send", "send it", "ship it", "go"}


def _daemon_up() -> bool:
    """True if the daemon backend should be used: the feature is enabled
    (use_daemon) AND the process is actually running."""
    if os.environ.get("WHETSTONE_FAKE_REFINE"):
        return False  # test seam owns the refine path; don't consult the daemon
    try:
        from whetstone import config, daemon
        if not config.load().get("use_daemon"):
            return False
        return daemon.is_running()
    except Exception:
        return False

# Only these modes may ever intercept a prompt. Anything else ("off", a typo,
# a future mode this version doesn't know) is treated as silence — the gate
# must fail quiet, never fail loud.
#   always  — block before send, show a refined version / scaffold
#   sigil   — same as always, but only for prompts starting with the sigil
#   whisper — DON'T block; inject a coaching note so the main session model
#             (subscription, no key, no extra call) asks for the missing piece.
#             The fully-subscription, zero-key, zero-latency automatic path.
_INTERCEPTING_MODES = ("always", "sigil", "whisper")


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


def _emit_whisper(context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    sys.stdout.flush()
    sys.exit(0)


def _whisper_context(features: dict) -> str:
    gaps = features.get("gaps") or []
    missing = ", ".join(gaps) if gaps else "a concrete done-state"
    return (
        "[Whetstone prompt coach] The user's request is under-specified for a clean "
        f"one-pass result — it's missing: {missing}. Before doing the work, briefly ask "
        "the user to confirm the key missing piece (a checkable done-state and the target "
        "surface), UNLESS it's obvious from context — in which case proceed, but state the "
        "assumption you're making. Then, in one short sentence, note what detail would have "
        "made the request unambiguous, so they learn the pattern for next time. Keep this to "
        "2-3 sentences total; ask, don't lecture, and don't mention this note."
    )


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


def _with_project(scaffold: str, cwd: str | None) -> str:
    """Append the per-project clarifying hint to a local scaffold, if any."""
    try:
        from whetstone import context_hints
        extra = context_hints.scaffold_extra(cwd)
        return scaffold + "\n" + extra if extra else scaffold
    except Exception:
        return scaffold


def _banner(body: str, tip: str, kind: str = "refined") -> str:
    rule = "─" * 52
    if kind == "refined":
        header = "── Whetstone · refined prompt (copied to clipboard) ──"
        footer = "[y ⏎] send refined   ·   [⌘V, edit, ⏎] tweak   ·   [type anything] send your own"
    elif kind == "affirm":
        header = "── Whetstone · looks good ✓ ──"
        footer = "press ⏎ to send your prompt as-is"
    else:  # scaffold
        header = "── Whetstone · make this sharper ──"
        footer = "fill the <…> (or add the missing piece), then press ⏎ to send"
    out = [header, "", body.strip()]
    if tip.strip():
        out += ["", f"why: {tip.strip()}"]
    out += [rule, footer]
    return "\n".join(out)


def _read_stdin() -> dict:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _refine(body: str, cfg: dict, cwd: str | None = None) -> dict:
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
    return refiner.refine(body, cfg=cfg, cwd=cwd)


def main() -> None:
    # RECURSION GUARD (must be first): the refiner shells out to `claude -p`,
    # whose own UserPromptSubmit hook would re-enter this gate and, if it also
    # coached, spawn another `claude -p` — an infinite loop. The refiner sets
    # WHETSTONE_IN_REFINER on that subprocess; here it forces an instant
    # passthrough so nested invocations never process anything.
    if os.environ.get("WHETSTONE_IN_REFINER"):
        sys.exit(0)
    try:
        data = _read_stdin()
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            prompt = ""
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = "nosession"
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
        scorelog.set_context(session_id, cwd)  # so every log record carries them
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
                try:  # feed accepted refinements into the criteria memory
                    from whetstone import context_hints
                    context_hints.learn_criteria(refined)
                except Exception:
                    pass
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

        tutorial = bool(cfg.get("tutorial"))
        suggestion = (scorer.suggest_model_effort(features, body)
                      if cfg.get("suggest_model") else None)

        # 4b. WHISPER MODE — fully subscription, no key, no extra model call.
        # Don't block: inject a coaching note so the MAIN session model (already
        # running on the user's subscription) asks for the missing piece and
        # teaches the pattern. Zero added latency; nothing to time out.
        if mode == "whisper":
            # Only whisper when there's genuinely a missing piece to ask about.
            # In tutorial mode should_coach passes well-formed and explore prompts
            # too — whispering "it's under-specified" at those inverts the
            # contract, so pass them through silently.
            if not features.get("gaps"):
                scorelog.log(prompt, features, ACTION_PASS, cfg)
                _emit_passthrough()
            state.mark_coached(session_id)  # respect the cooldown between nudges
            scorelog.log(prompt, features, ACTION_COACH, cfg)
            ctx = _whisper_context(features)
            if suggestion:
                ctx += (f"\n\nAlso tell the user in one short line: this task is best "
                        f"suited to {suggestion['model']} at {suggestion['effort']} effort "
                        f"({suggestion['why']}).")
            _emit_whisper(ctx)

        # 5. REFINE — pick the path by whether a fast LLM is configured.
        #    LLM mode (ANTHROPIC_API_KEY set, or the test seam active): get an
        #      AI-written rewrite; a crash/junk/decline fails OPEN (passthrough).
        #    Local mode (no key — the default, works on any subscription with no
        #      setup): an instant deterministic scaffold from the classifier's
        #      gaps. Nothing to auto-send, so the user edits and resends.
        has_api = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_seam = bool(os.environ.get("WHETSTONE_FAKE_REFINE"))
        daemon_up = _daemon_up()
        llm_mode = has_api or has_seam or daemon_up
        refined_sendable = None  # None = no content produced yet
        banner_body = None
        banner_kind = "scaffold"
        tip = ""
        scaffold_tip = ("Name the done-state and target so it's checkable — "
                        "you get one pass instead of a back-and-forth.")
        if llm_mode:
            try:
                result = _refine(body, cfg, cwd=cwd)
            except Exception:
                result = None
            if not isinstance(result, dict):
                result = {}
            r = result.get("refined")
            r = r.strip() if isinstance(r, str) else ""
            if result.get("needs_refinement") and r:
                refined_sendable = r
                banner_body = r
                banner_kind = "refined"
                t = result.get("tip")
                tip = t if isinstance(t, str) else ""
            elif daemon_up and not has_api and not has_seam:
                # The daemon was our only backend and it missed/declined -> the
                # local scaffold rather than silence.
                scaffold = suggest.template(body, features)
                if scaffold:
                    refined_sendable = ""
                    banner_body = _with_project(scaffold, cwd)
                    tip = scaffold_tip
        else:
            scaffold = suggest.template(body, features)
            if scaffold:
                refined_sendable = ""
                banner_body = _with_project(scaffold, cwd)
                tip = scaffold_tip

        # No refinement/scaffold produced. In tutorial mode, affirm the prompt so
        # the user learns what "good" looks like; otherwise respect the judgment
        # and pass through (fail-open).
        if banner_body is None:
            if tutorial:
                # Tutorial always shows something — but only AFFIRM when the
                # prompt is genuinely well-formed. If it has gaps (and the LLM
                # just failed/declined), show the scaffold, never a false
                # "looks good" on an under-specified prompt.
                scaffold = suggest.template(body, features)
                refined_sendable = ""
                if scaffold:
                    banner_body = _with_project(scaffold, cwd)
                    banner_kind = "scaffold"
                    tip = scaffold_tip
                else:
                    banner_body = suggest.affirm(features)
                    banner_kind = "affirm"
                    tip = ""
            else:
                scorelog.log(prompt, features, ACTION_PASS, cfg)
                _emit_passthrough()

        # Append the best-suited model + effort suggestion to the banner.
        if suggestion:
            banner_body = banner_body + "\n\n" + suggest.model_line(suggestion)

        # 6. PRESENT. Arm the one-shot bypass BEFORE emitting the block so the
        # gate can never block twice in a row. Arming with "" (scaffold/affirm)
        # means a bare `y` won't auto-send — it just passes through.
        state.set_pending(session_id, refined_sendable or "")
        state.mark_coached(session_id)
        if refined_sendable and not os.environ.get("WHETSTONE_FAKE_REFINE"):
            _clipboard(refined_sendable)
            if cfg.get("inject"):
                _tmux_inject(refined_sendable, cfg.get("inject_delay_ms", 450))
        scorelog.log(prompt, features, ACTION_COACH, cfg)
        _emit_block(_banner(banner_body, tip, kind=banner_kind))
    except SystemExit:
        raise
    except BaseException:
        # Absolute fail-open: never break the user's turn.
        _emit_passthrough()


if __name__ == "__main__":
    main()
