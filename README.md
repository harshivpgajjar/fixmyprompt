# Whetstone

A prompt coach for Claude Code. When you submit a rough prompt, Whetstone offers a sharper, send-ready version and **one** teaching point — so over time you write better prompts and need it less. It stays completely silent on the prompts that don't need it, and it never "fixes" intentional exploration.

> It sharpens *you*, not just the prompt.

---

## What it actually does

Three surfaces, use as much or as little as you like:

1. **`/refine <rough prompt>`** — opt-in, zero-risk. Hand it a draft; get back a refined version, the detected mode, and one prompting lesson. Works today, no setup beyond install.
2. **Coach Gate** (the live "before enter" flow) — you hit Enter on a rough prompt, it **doesn't send**. With no API key it shows an instant local scaffold of what's missing + one teaching point; you edit and resend. With `ANTHROPIC_API_KEY` set it shows an AI-written rewrite you can send with a single **`y`** (or **⌘V** to tweak, or type anything to send your own). Off by default; `whetstone on` to enable.
3. **Weekly report** — every prompt is scored locally and logged; the Saturday self-audit reports whether your prompts are getting more self-sufficient, and your top recurring gaps. Teaching, made measurable.

## The honest ceiling (read this)

Claude Code's hook API **cannot** pre-fill your input line with editable text — there is no supported way to rewrite what's in the box before you send. So the literal "my Enter transforms the text in place" isn't possible. Whetstone gets as close as the platform allows:

- **Everywhere:** block → refined text on your clipboard → `y` sends it (zero paste), or ⌘V to edit. The refined text is one keystroke away.
- **In tmux (opt-in):** the refined text is *pasted into your input line, editable, ~0.5s after you hit Enter* — via tmux's pane-targeted paste-buffer. This is the closest thing to the dream, and it's why running Claude Code inside tmux is recommended.

A block can **never** happen twice in a row (a one-shot session flag guarantees the second Enter always goes through), and any error fails **open** — the coach can never lock you out or lose your prompt.

## Design principles

- **Silent on continuations.** "yes / go / continue / run it", slash commands, pastes, and anything under ~12 words are never touched (~0ms, no LLM call).
- **Mode-aware.** "Blow me away" is a valid *discovery* request — Whetstone recognizes explore mode and leaves it alone (or makes the exploration more productive). It only coaches under-specified *execute* prompts.
- **Voice-preserving.** It adds scaffolding (a concrete "done means…", constraints, the target file) — it never rewrites your Hinglish/casual voice or "corrects" typos.
- **Fail-open, always bypassable.** Errors pass through; `y`/edit/override are all one keystroke; `whetstone off` kills it.

## Install

```bash
cd ~/Desktop/whetstone
./install.sh
```

This installs the runtime to `~/.claude/whetstone` (off the iCloud Desktop), wires the `UserPromptSubmit` hook into `~/.claude/settings.json`, and puts the `whetstone` CLI on your PATH. **Coaching is OFF by default** — nothing changes until you opt in. The hook activates on your next new session.

```bash
whetstone status          # show config
whetstone on              # enable live coaching (mode=always)
whetstone mode sigil      # only coach prompts starting with ??  (opt-in per prompt)
whetstone off             # disable live coaching
whetstone refine "fix the mobile version"   # try it right now
whetstone report          # your prompting trend (last 7 days)
whetstone selftest        # offline classifier smoke test
```

## Configuration

`~/.claude/whetstone/config.json` (or `PCOACH_*` env vars):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `off` | `always` / `sigil` / `off` |
| `sigil` | `??` | prefix that opts a prompt in, in sigil mode |
| `min_words` | `4` | never coach prompts shorter than this (4 catches short vague asks; raise it if too eager) |
| `cooldown_sec` | `90` | anti-nag: no second coach within this window |
| `inject` | `true` | in tmux, paste the refined text into the input line |
| `model` | `claude-haiku-4-5` | refiner model (fail-open) |
| `coach_below_quality` | `0.7` | only coach when the local quality score is below this |

### Refiner backend — no API key needed

A submit-time hook has a hard latency budget (a few seconds), and the only
subscription-authenticated model call from a shell is `claude -p`, which spins
up a full agent session (~20–40s) — far too slow to sit in front of every
prompt. So Whetstone does **not** put an LLM in the critical path by default:

- **Live gate (default, zero key, zero network):** a deterministic *local*
  analysis. The classifier finds the gaps and the gate blocks with a fill-in
  **scaffold** + one teaching point — instant, private, works on any Claude
  subscription with no setup. Nothing leaves your machine.
- **Live gate + warm daemon (subscription, no key, ~1.5s):** run `whetstone daemon on`
  and the block gate shows AI-*written* rewrites with one-keystroke `y`-to-send —
  the full "before enter" experience — on your **subscription, no API key**. It
  works by keeping one `claude` session warm (a ~200MB background process, opt-in,
  KeepAlive), so each refine is inference-only (~1.7s warm, ~2.6s cold). It never
  touches a credential — the child `claude` self-authenticates like any session.
  If the daemon is down or slow, the gate falls back to the scaffold instantly.
- **Live gate (API-key upgrade):** set `ANTHROPIC_API_KEY` for the fastest path
  (~1s, no daemon). Optional.
- **`/refine` skill (no key):** a full LLM rewrite using your **session model** —
  subscription-native, on demand, higher quality than the scaffold. Use this
  whenever you want a written refinement without a key.
- **`whetstone refine` CLI:** local scaffold by default; set `WHETSTONE_BACKEND=cli`
  to force a `claude -p` subscription rewrite (slow, ~20s — you're waiting on
  purpose), or `ANTHROPIC_API_KEY` for the fast API rewrite.

**Recursion guard:** if the CLI backend (`claude -p`) is ever used, that nested
session's own `UserPromptSubmit` hook would re-enter the gate. Whetstone sets
`WHETSTONE_IN_REFINER` on the subprocess so nested invocations no-op instantly.

## Privacy

The prompt log (`~/.claude/whetstone/prompt-log.jsonl`) stores quality scores plus a short redacted preview; anything matching a secret pattern suppresses the preview entirely. It's gitignored. This is measurement, never surveillance. Delete it anytime.

## Architecture

```
UserPromptSubmit hook ─▶ bin/coach_gate.py
                            │
      ┌─────────────────────┼───────────────────────────┐
   scorer.py (local gate)  refiner.py (Haiku, fail-open) state.py (one-shot flag)
      │                     │                             │
   classify()           refine()                     take_pending()/set_pending()
      └──────────── scorelog.py ──▶ report.py ──▶ Saturday self-audit
```

- `whetstone/scorer.py` — deterministic classifier (mode, gaps, quality), <1ms, no I/O.
- `whetstone/refiner.py` — the LLM refiner, mode- and taste-aware, fail-open.
- `whetstone/state.py` — the loop-proof one-shot bypass + cooldown + backstop.
- `whetstone/scorelog.py` / `report.py` — measurement and the weekly trend.
- `bin/coach_gate.py` — the hook orchestration.
- `bin/whetstone` — the CLI.

## Tests

Pure stdlib, no dependencies:

```bash
cd ~/Desktop/whetstone && python3 -m unittest discover -s tests -v
```

Covers: classifier precision/recall on real prompt styles, the loop-proof and fail-open invariants, the accept/edit/override branches, sigil mode, the backstop, secret redaction, and the report math.

## Uninstall

```bash
./uninstall.sh            # removes the hook + CLI, keeps your config/log
./uninstall.sh --purge    # also deletes ~/.claude/whetstone
```

## Roadmap

- v0.1 (this): `/refine`, Coach Gate, weekly report, tmux injector, install/uninstall, tests.
- v0.2: distributable marketplace packaging with a generic default rubric + taste template; per-user aggressiveness presets.
