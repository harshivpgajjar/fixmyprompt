#!/usr/bin/env python3
"""FixMyPrompt Coach Gate — the UserPromptSubmit hook entrypoint.

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
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixmyprompt import (  # noqa: E402
    ACTION_ACCEPT,
    ACTION_COACH,
    ACTION_EDIT,
    ACTION_PASS,
    cc_tips,
    config,
    refiner,
    scorelog,
    scorer,
    state,
    suggest,
)

_CONFIRM = {"y", "ye", "yes", "yep", "yeah", "ok", "okay", "k", "send", "send it", "ship it", "go"}
# Reject the rewrite and send the ORIGINAL prompt untouched.
_CONFIRM_ORIGINAL = {"n", "no", "nope", "o", "orig", "og", "original", "mine",
                     "keep mine", "keep original", "send mine", "send original",
                     "as is", "as-is", "unchanged", "own", "send my own"}

# A genuine rewrite LEADS with a declarative/imperative restatement of the task
# ("Refactor the pipeline for scale. Which pipeline? ..."). A clarifying question
# LEADS with an interrogative aimed at the USER ("Which 'it'? ...") — that's not
# a prompt at all, so offering "[y] send refined" / clipboard-to-send for it
# would hand Claude a question meant for the human. Detected on the FIRST
# sentence only, so a rewrite that trails into sub-questions still counts.
_QUESTION_LEAD = re.compile(
    r"^(?:which|what|what'?s|how|how'?s|why|where|who|"
    r"is there|are there|do you|does|can you|could you|"
    r"should i|should you|would it|would you)\b",
    re.IGNORECASE,
)


def _is_clarifying_question(text: str) -> bool:
    """True if `text` reads as a question TO THE USER rather than an actual
    rewritten, sendable prompt."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""
    if not first:
        return False
    return first.rstrip().endswith("?") or bool(_QUESTION_LEAD.match(first.strip()))

# An attached/pasted image shows up as a bracketed marker in the prompt text.
# The exact wire format isn't documented, so match generously — a bracketed run
# containing image/screenshot/pasted (incl. a filename like
# "[Screenshot 2026-07-07 at 10.14.32.png]"), or an <image> tag. The [^\]]/[^>]
# runs are bounded so this can't backtrack on a huge paste.
_IMAGE_MARKER = re.compile(
    r"\[[^\]]{0,120}?(?:image|screenshot|pasted)[^\]]{0,120}?\]|<image[^>]{0,60}>",
    re.IGNORECASE,
)
# An image conveyed as a path or data-URI inside any field value.
_IMAGE_VALUE = re.compile(r"data:image/|\.(?:png|jpe?g|gif|webp|bmp|svg|heic|heif)\b", re.IGNORECASE)


def _has_attachment(data: dict, prompt: str) -> bool:
    """True if the submission carries an image/file attachment.

    Blocking a submission discards it, and a hook has no channel to re-inject a
    pasted image — so a blocked image-bearing prompt loses its image on resubmit.
    We must therefore never BLOCK these (we whisper or pass instead). The wire
    format for attachments isn't documented, so detect defensively across three
    channels: an attachment-shaped field key, an image path/data-URI in any field
    value, or an image marker in the prompt text. A false positive only means we
    coach non-blockingly instead of blocking — harmless — so we err toward
    detecting. (Set FIXMYPROMPT_DEBUG_STDIN=1 to confirm the real wire format.)"""
    for key in ("images", "image", "attachments", "attachment", "files",
                "image_paths", "media"):
        if data.get(key):
            return True
    for v in data.values():
        if isinstance(v, str) and _IMAGE_VALUE.search(v):
            return True
    return bool(_IMAGE_MARKER.search(prompt or ""))


def _daemon_up() -> bool:
    """True if the daemon backend should be used: the feature is enabled
    (use_daemon) AND the process is actually running."""
    if os.environ.get("FIXMYPROMPT_FAKE_REFINE"):
        return False  # test seam owns the refine path; don't consult the daemon
    try:
        from fixmyprompt import config, daemon
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


def _emit_json(obj: dict) -> None:
    """Serialize fully, then do ONE write. An all-or-nothing emit keeps the
    invariant 'stdout is only ever valid-JSON-or-empty' — a fault can't leave a
    half-serialized object on the protocol stream."""
    payload = json.dumps(obj)
    sys.stdout.write(payload)
    sys.stdout.flush()
    sys.exit(0)


def _emit_passthrough() -> None:
    """No output = prompt goes to the model unchanged."""
    sys.exit(0)


def _emit_block(reason: str) -> None:
    _emit_json({"decision": "block", "reason": reason})


def _emit_accept(text: str, is_original: bool = False) -> None:
    note = (
        "The user declined the suggested rewrite and chose to send their ORIGINAL "
        "prompt unchanged. Treat THIS as their actual request and act on it "
        "directly:\n\n"
        if is_original else
        "The user reviewed and approved this refined version of the prompt they "
        "just tried to send. Treat THIS as their actual request and act on it "
        "directly:\n\n"
    ) + text
    _emit_json({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": note}})


def _emit_whisper(context: str) -> None:
    _emit_json({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    })


def _whisper_context(features: dict, has_attachment: bool = False) -> str:
    gaps = features.get("gaps") or []
    missing = ", ".join(gaps) if gaps else "a concrete done-state"
    image_note = (
        " An image/screenshot is attached — it shows WHAT, not the target surface or "
        "the done-state, so it does NOT excuse skipping this: still confirm or explicitly "
        "state where the change goes and what checkable outcome means it's finished."
        if has_attachment else ""
    )
    return (
        "[FixMyPrompt prompt coach] The user's request is under-specified for a clean "
        f"one-pass result — it's missing: {missing}.{image_note} Before doing the work, ask "
        "the user to confirm the key missing piece (a checkable done-state and the target "
        "surface) UNLESS it is truly unambiguous from context — the bar is high; a design "
        "reference alone is not enough. If you proceed without asking, the FIRST line of "
        "your reply must state the assumption you're making, plainly, so the user can "
        "correct it before you act on it — never bury or skip this. Then, in one short "
        "sentence, note what detail would have made the request unambiguous, so they learn "
        "the pattern for next time. Keep this to 2-3 sentences total; ask, don't lecture, "
        "and don't mention this note."
    )


def _copy_via(cmds: list[list[str]], text: str) -> bool:
    """Try each clipboard command in order; return True on the first that runs.
    DEVNULL both streams — this runs just before the protocol JSON is written, so
    nothing from the copy tool may leak onto the hook's stdout."""
    import shutil
    for cmd in cmds:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text, text=True, timeout=3, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def _win_clipboard(text: str) -> bool:
    """Copy to the Windows clipboard via the Win32 API (ctypes, stdlib-only).
    Full Unicode (CF_UNICODETEXT is UTF-16), instant, no subprocess. user32/
    kernel32 are always present on Windows; on any other OS ctypes.windll doesn't
    exist and this returns False (never reached there anyway)."""
    try:
        import ctypes
        from ctypes import wintypes
        CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            ptr = k32.GlobalLock(handle)
            ctypes.memmove(ptr, data, len(data))
            k32.GlobalUnlock(handle)
            if not u32.SetClipboardData(CF_UNICODETEXT, handle):
                k32.GlobalFree(handle)
                return False
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def _clipboard(text: str) -> bool:
    """Copy text to the OS clipboard. Returns True on success. Cross-platform,
    stdlib-only: macOS pbcopy; Windows Win32 API (ctypes) with clip.exe fallback;
    Linux wl-copy/xclip/xsel (first available)."""
    try:
        if sys.platform == "darwin":
            return _copy_via([["pbcopy"]], text)
        if sys.platform == "win32":
            return _win_clipboard(text) or _copy_via([["clip"]], text)
        return _copy_via([["wl-copy"], ["xclip", "-selection", "clipboard"],
                          ["xsel", "--clipboard", "--input"]], text)
    except Exception:
        return False


def _tmux_inject(text: str, delay_ms: int) -> None:
    """Detached: after a short delay, paste the refined text into the tmux pane
    so it lands in the input line, editable. Bracketed paste (-p) keeps newlines
    from submitting. Pane-targeted so it survives focus changes."""
    if sys.platform == "win32":
        return  # no tmux/paste-buffer equivalent on Windows — clipboard-only there
    pane = os.environ.get("TMUX_PANE")
    if not os.environ.get("TMUX") or not pane:
        return
    try:
        script = (
            f"sleep {max(0, delay_ms) / 1000.0}; "
            f"printf %s {shell_quote(text)} | tmux load-buffer -b fixmyprompt - ; "
            f"tmux paste-buffer -p -d -b fixmyprompt -t {shell_quote(pane)}"
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


def _stage_for_resend(text: str, cfg: dict) -> bool:
    """Put `text` where the user can actually resend it with one paste. Returns
    True if it made it onto the clipboard (so the banner can say so honestly).

    Claude Code does NOT restore the typed prompt into the input box after a
    block — it's gone, not just hidden. So any banner that tells the user
    "press Enter to send it as-is" is a lie unless we've put that exact text
    somewhere they can send it from. This copies it to the clipboard (and, in
    tmux, injects it into the pane) so the instruction is actually true."""
    if os.environ.get("FIXMYPROMPT_FAKE_REFINE"):  # test seam: never touch the real clipboard
        return True
    ok = _clipboard(text)
    if cfg.get("inject"):
        _tmux_inject(text, cfg.get("inject_delay_ms", 450))
    return ok


def _with_project(scaffold: str, cwd: str | None) -> str:
    """Append the per-project clarifying hint to a local scaffold, if any."""
    try:
        from fixmyprompt import context_hints
        extra = context_hints.scaffold_extra(cwd)
        return scaffold + "\n" + extra if extra else scaffold
    except Exception:
        return scaffold


# Paste chord is OS-specific — ⌘V on macOS, Ctrl+V everywhere else.
_PASTE = "⌘V" if sys.platform == "darwin" else "Ctrl+V"


def _banner(body: str, tip: str, kind: str = "refined", staged: bool = True) -> str:
    """`staged` = whether the text actually made it to the clipboard. When False
    (rare — e.g. headless Linux with no clipboard tool) we don't promise a paste
    the user can't perform."""
    rule = "─" * 52
    # `[n ⏎] send your original` always works — it sends the stored original via
    # the one-shot flag, independent of the clipboard. `staged` only decides
    # whether we also offer the clipboard-paste (to edit) affordance.
    paste = f"   ·   [{_PASTE}] paste to edit" if staged else ""
    if kind == "refined":
        header = ("── FixMyPrompt · refined prompt (copied to clipboard) ──"
                  if staged else "── FixMyPrompt · refined prompt ──")
        tweak = f"   ·   [{_PASTE}] paste refined to tweak" if staged else ""
        footer = f"[y ⏎] send refined   ·   [n ⏎] send yours unchanged{tweak}"
    elif kind == "affirm":
        header = "── FixMyPrompt · looks good ✓ ──"
        footer = f"[n ⏎] send your prompt as-is{paste}"
    elif kind == "tip":
        header = "── FixMyPrompt · tip ──"
        footer = f"[n ⏎] send your prompt{paste}"
    else:  # scaffold
        header = "── FixMyPrompt · make this sharper ──"
        add = (f"   ·   [{_PASTE}] paste to add the missing piece above"
               if staged else "   ·   add the missing piece above and resend")
        footer = f"[n ⏎] send unchanged{add}"
    out = [header, "", body.strip()]
    if tip.strip():
        out += ["", f"why: {tip.strip()}"]
    out += [rule, footer]
    return "\n".join(out)


def _utf8_io() -> None:
    """Force UTF-8 on the std streams. Windows stdio defaults to the locale codec
    (cp1252) when redirected — which is exactly how a hook is invoked — so a
    non-ASCII prompt would UnicodeDecodeError and the emit could UnicodeEncodeError.
    No-op on macOS/Linux (already UTF-8). Belt-and-suspenders with the launcher's
    PYTHONUTF8=1 and with reading stdin as bytes below."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _read_stdin() -> dict:
    try:
        # Read raw BYTES so decoding is locale-independent: json.loads detects
        # UTF-8/16/32 (and a BOM) per the JSON spec, so a Windows cp1252 console
        # can't corrupt or drop non-ASCII characters in the prompt.
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read() if buf is not None else sys.stdin.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# Markers of a SYSTEM-INJECTED submission — a background-agent task-notification,
# a system-reminder, or slash-command output that Claude Code delivers to the
# UserPromptSubmit hook as if it were a prompt. The user didn't type these, so
# the coach must never touch them. Matched near the start (where these tags
# always appear) to avoid catching a user who merely mentions the words.
_SYSTEM_MARKERS = (
    "<task-notification", "</task-notification", "<system-reminder",
    "[system notification", "<local-command-stdout", "<local-command-stderr",
    "<command-name>", "<command-message>", "<command-args>",
)


def _is_system_injected(prompt: str) -> bool:
    head = (prompt or "")[:600].lower()
    return any(m in head for m in _SYSTEM_MARKERS)


def _refine(body: str, cfg: dict, cwd: str | None = None) -> dict:
    # test seam — active ONLY when FIXMYPROMPT_FAKE_REFINE is set (never set in
    # production; when unset this function is exactly refiner.refine).
    # The env value is a JSON refine-result, or the sentinel "RAISE" to
    # simulate a crashing refiner. Each consultation appends the body it
    # received to $FIXMYPROMPT_HOME/fake-refine-calls so tests can prove the
    # refiner was (or was NOT) consulted, and that the sigil was stripped.
    fake = os.environ.get("FIXMYPROMPT_FAKE_REFINE")
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
    _utf8_io()  # make stdio UTF-8 before anything reads/writes (Windows-safe)
    # RECURSION GUARD (must be first): the refiner shells out to `claude -p`,
    # whose own UserPromptSubmit hook would re-enter this gate and, if it also
    # coached, spawn another `claude -p` — an infinite loop. The refiner sets
    # FIXMYPROMPT_IN_REFINER on that subprocess; here it forces an instant
    # passthrough so nested invocations never process anything.
    if os.environ.get("FIXMYPROMPT_IN_REFINER"):
        sys.exit(0)
    try:
        data = _read_stdin()
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            prompt = ""
        # SYSTEM-INJECTED GUARD (before touching any state): background-agent
        # task-notifications, system-reminders, and slash-command output arrive
        # at this hook as "prompts" the user never typed. Pass them straight
        # through — never coach, never consume the one-shot pending flag.
        if _is_system_injected(prompt):
            sys.exit(0)
        has_attachment = _has_attachment(data, prompt)
        # Opt-in diagnostic (off by default): record the stdin keys + whether an
        # image was detected, so the exact attachment wire format can be
        # confirmed from a real run without exposing prompt/image contents.
        if os.environ.get("FIXMYPROMPT_DEBUG_STDIN"):
            try:
                config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                with (config.RUNTIME_DIR / "stdin-keys.log").open("a") as fh:
                    fh.write(json.dumps({"keys": sorted(map(str, data.keys())),
                                         "image_detected": has_attachment}) + "\n")
            except Exception:
                pass
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
            original = pending.get("original") if isinstance(pending, dict) else None
            original = original if isinstance(original, str) else ""
            norm = prompt.strip().lower().rstrip(".!? ")
            if norm in _CONFIRM and refined.strip():
                scorelog.log(prompt, scorer.classify(prompt), ACTION_ACCEPT, cfg)
                try:  # feed accepted refinements into the criteria memory
                    from fixmyprompt import context_hints
                    context_hints.learn_criteria(refined)
                except Exception:
                    pass
                _emit_accept(refined)
            if norm in _CONFIRM_ORIGINAL and original.strip():
                # user rejected the rewrite — send their ORIGINAL prompt untouched.
                scorelog.log(prompt, scorer.classify(prompt), ACTION_EDIT, cfg)
                _emit_accept(original, is_original=True)
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
        cc = cc_tips.analyze(body, features) if cfg.get("cc_tips") else None
        cc_tip = cc["tip"] if cc else None
        would_coach = scorer.should_coach(features, cfg)
        # A high-value Claude Code tip (new-work/context-switch, big-goal) is
        # worth surfacing even on an already well-formed prompt; lower-value tips
        # (plan mode, subagents) only ride along when the gate already coaches.
        engage_for_tip = bool(cc and cc["engage"])

        # 4. GATE (local, zero-latency) + cooldown.
        if state.cooldown_active(session_id, cfg) or (not would_coach and not engage_for_tip):
            scorelog.log(prompt, features, ACTION_PASS, cfg)
            _emit_passthrough()

        tip_only = not would_coach  # engaging solely to deliver the Claude Code tip
        tutorial = bool(cfg.get("tutorial"))
        suggestion = (scorer.suggest_model_effort(features, body)
                      if cfg.get("suggest_model") and not tip_only else None)

        # 4b. WHISPER (non-blocking) — inject a coaching note instead of blocking
        # so the MAIN session model (subscription, no key, no extra call) asks for
        # the missing piece. Used for whisper mode AND — crucially — for any
        # submission carrying an image: blocking would discard the attachment
        # (the user would have to re-attach it), so we NEVER block those.
        if mode == "whisper" or has_attachment:
            if tip_only:
                ctx = ("[FixMyPrompt coach] Relay this Claude Code tip to the user "
                       "in one short line, then proceed with their request: " + cc_tip)
            elif features.get("gaps"):
                ctx = _whisper_context(features, has_attachment)
                if suggestion:
                    ctx += (f"\n\nAlso tell the user in one short line: this task is best "
                            f"suited to {suggestion['model']} at {suggestion['effort']} effort "
                            f"({suggestion['why']}).")
                if cc_tip:
                    ctx += "\n\nAlso relay this Claude Code tip in one line: " + cc_tip
            else:
                # would_coach (tutorial) but well-formed and no tip -> stay silent.
                scorelog.log(prompt, features, ACTION_PASS, cfg)
                _emit_passthrough()
            state.mark_coached(session_id)  # respect the cooldown between nudges
            scorelog.log(prompt, features, ACTION_COACH, cfg)
            _emit_whisper(ctx)

        # 4c. TIP-ONLY BLOCK — a well-formed prompt with a high-value CC tip.
        # Show just the tip (no refiner call, no fabricated scaffold). Arm the
        # one-shot bypass first so it stays loop-proof.
        if tip_only:
            state.set_pending(session_id, "", prompt)
            state.mark_coached(session_id)
            staged = _stage_for_resend(prompt, cfg)
            scorelog.log(prompt, features, ACTION_COACH, cfg)
            _emit_block(_banner(cc_tip, "", kind="tip", staged=staged))

        # 5. REFINE — pick the path by whether a fast LLM is configured.
        #    LLM mode (ANTHROPIC_API_KEY set, or the test seam active): get an
        #      AI-written rewrite; a crash/junk/decline fails OPEN (passthrough).
        #    Local mode (no key — the default, works on any subscription with no
        #      setup): an instant deterministic scaffold from the classifier's
        #      gaps. Nothing to auto-send, so the user edits and resends.
        has_api = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_seam = bool(os.environ.get("FIXMYPROMPT_FAKE_REFINE"))
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
            if result.get("needs_refinement") and r and _is_clarifying_question(r):
                # The refiner asked a QUESTION instead of producing a rewrite
                # (e.g. "make it better" -> "Which 'it'? ..."). That's genuinely
                # useful guidance, but it isn't a sendable prompt — sending it
                # would hand Claude a question meant for the user. Present it
                # like a scaffold: show the question, but never offer [y]/claim
                # it's "refined" or ready to send.
                banner_body = r
                banner_kind = "scaffold"
                t = result.get("tip")
                tip = t if isinstance(t, str) else scaffold_tip
            elif result.get("needs_refinement") and r:
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

        # Append the model + effort suggestion and a relevant Claude Code tip.
        if suggestion:
            banner_body = banner_body + "\n\n" + suggest.model_line(suggestion)
        if cc_tip:
            banner_body = banner_body + "\n\n" + cc_tip

        # 6. PRESENT. Arm the one-shot bypass BEFORE emitting the block so the
        # gate can never block twice in a row. Arming with "" (scaffold/affirm)
        # means a bare `y` won't auto-send — it just passes through.
        state.set_pending(session_id, refined_sendable or "", prompt)
        state.mark_coached(session_id)
        staged = True
        if refined_sendable:
            staged = _stage_for_resend(refined_sendable, cfg)
        else:
            # No auto-sendable rewrite (affirm / tip / scaffold). Claude Code
            # clears the input on a block, so stage the user's ORIGINAL prompt on
            # the clipboard — for affirm/tip they send it as-is; for a scaffold
            # they paste it, add the missing piece the banner names, and send.
            # Without this, "press ⏎ to send" does nothing (the box is empty).
            staged = _stage_for_resend(prompt, cfg)
        scorelog.log(prompt, features, ACTION_COACH, cfg)
        _emit_block(_banner(banner_body, tip, kind=banner_kind, staged=staged))
    except SystemExit:
        raise
    except BaseException:
        # Absolute fail-open: never break the user's turn.
        _emit_passthrough()


if __name__ == "__main__":
    main()
