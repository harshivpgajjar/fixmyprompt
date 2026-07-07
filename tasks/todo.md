# Onboarding tutorial + daemon toggle + teach-mode + token warnings + bug fix

## A. Bug: image/suggestions lost despite no prompt change  ✅ DONE
- [x] Root cause: `decision:block` DISCARDS the submission; a hook can't
      re-inject a pasted image → the IMAGE is lost on resubmit. Log proved NO
      re-block loop (0 coach→coach) and byte-identical coach/edit previews → text
      is retained; only the image was lost.
- [x] Fix: never BLOCK an image-bearing submission — route to non-blocking
      whisper (additionalContext). Detect via attachment fields OR `[Image …]`
      marker (+ opt-in stdin diagnostic to confirm the wire format).
- [x] Regression tests (image never blocks across shapes/field; text still blocks).

## B. Interactive onboarding tour (`fixmyprompt tour`)  ✅ DONE
- [x] 6 steps: coach flow (send-as-is + image safety), features, teach-mode,
      daemon, token warnings, wrap. Interactive on TTY, linear when piped.
- [x] First-run auto-launch (no-args on TTY) + `.toured` marker + stderr hint.
- [x] `fixmyprompt help` re-invokes it; `help --commands` lists commands.

## C. Teach-mode on by default (for users)  ✅ DONE
- [x] install.sh default config writes tutorial:true; tour confirms/toggles it.
      Library DEFAULT kept false for test/embed safety.

## D. Token-usage warnings  ✅ DONE
- [x] Tour step frames context-bloat as a token warning (/clear·/compact) and
      points to /context and /usage.

## E. Done-state verification  ✅ DONE
- [x] tour runs (interactive + piped), help re-triggers it, daemon status works
      (didn't disturb the live daemon), image bug fixed. 236 tests + ruff green.
      Runtime synced. → commit + push.
