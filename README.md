# FixMyPrompt

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A prompt coach for [Claude Code](https://claude.com/claude-code). When you submit a rough prompt, FixMyPrompt offers a sharper, send-ready version and **one** teaching point — so over time you write better prompts and need it less. It stays completely silent on the prompts that don't need it, and it never "fixes" intentional exploration.

> It sharpens *you*, not just the prompt.

Runs entirely on **your Claude subscription — no API key, no external service, nothing leaves your machine** unless you opt into the (also keyless) subscription daemon for faster rewrites.

---

## What it actually does

Four surfaces. Use as much or as little as you like — everything is off by default.

1. **`/refine <rough prompt>`** — opt-in, zero-risk. Hand it a draft; get back a refined version, the detected mode, one prompting lesson, and a model+effort suggestion. Works immediately after install, no configuration.
2. **Coach Gate** (the live "before you hit send" flow) — you hit Enter on a rough prompt, it **doesn't send**. Without any daemon/key it shows an instant local scaffold of what's missing + one teaching point; you fill it in and resend. With the optional daemon (or an API key) it shows an AI-*written* rewrite you can send with a single **`y`**, tweak with **⌘V**, or override by typing anything. Off by default — `fixmyprompt on` to enable.
3. **Whisper mode** — an alternative to Coach Gate that never blocks: it quietly tells your *main* Claude Code session to ask about the one missing piece, in one sentence, before doing the work. Zero extra latency, zero extra process.
4. **Weekly report + progress tracker** — every prompt is scored locally and logged; `fixmyprompt report` / `fixmyprompt progress` show whether your prompts are getting more self-sufficient over time, a sparkline trend, your streak, and your top recurring gaps. Teaching, made measurable.

## The honest ceiling (read this)

Claude Code's hook API **cannot** pre-fill your input line with editable text — there is no supported way to rewrite what's in the box before you send, and blocking a submission **clears the box**; it does not leave your typed text there. So the literal "my Enter transforms the text in place" isn't possible, and "just press Enter to send it as-is" would be a lie unless something is actually staged for you to send. FixMyPrompt gets as close as the platform allows:

- **Everywhere:** a block never traps you. `y ⏎` sends the AI rewrite (zero paste); **`n ⏎` sends your original prompt unchanged** — rejecting the rewrite entirely; and ⌘V (Ctrl+V on Windows) pastes the text to edit first. The `n` path always works, even where there's no clipboard tool (it sends the stored original directly).
- **In tmux (opt-in):** that same text is *pasted into your input line, editable, ~0.5s after you hit Enter* — via tmux's pane-targeted paste-buffer. This is the closest thing to the dream, and it's why running Claude Code inside tmux is recommended if you want it.

A block can **never** happen twice in a row (a one-shot session flag guarantees the second Enter always goes through), and any error fails **open** — the coach can never lock you out or lose your prompt.

## Design principles

- **Silent on continuations.** "yes / go / continue / run it", slash commands, pastes, and anything under a few words are never touched (~0ms, no LLM call).
- **Mode-aware.** "Blow me away" is a valid *discovery* request — FixMyPrompt recognizes explore mode and leaves it alone (or makes the exploration more productive). It only coaches under-specified *execute* prompts.
- **Voice-preserving.** It adds scaffolding (a concrete "done means…", constraints, the target file) — it never rewrites your casual voice or "corrects" typos.
- **Fail-open, always bypassable.** Errors pass through silently; `y`/edit/override are all one keystroke; `fixmyprompt off` kills it entirely.
- **Teach, don't create dependence.** The goal is that you need this tool *less* over time — every intervention carries at most one teaching point, and the progress tracker proves whether it's working.

## Install

### Option A — Claude Code plugin marketplace (recommended)

Inside a Claude Code session:

```
/plugin marketplace add harshivpgajjar/fixmyprompt
/plugin install fixmyprompt@fixmyprompt
```

This wires the `/refine` skill and the live coaching hook automatically. **Coaching is OFF by default** — nothing intercepts your prompts until you opt in. Restart your session (or start a new one) for the hook to take effect.

**Note:** the marketplace install does not put the `fixmyprompt` CLI on your PATH — every command in Usage below (`fixmyprompt on`, `report`, `tour`, etc.) needs it. To get the CLI too, also run Option B's install script once (`git clone` + `python3 install.py`) — it's idempotent and safe to run alongside the marketplace install.

### Option B — clone + install script (macOS · Linux · Windows)

```bash
git clone https://github.com/harshivpgajjar/fixmyprompt.git
cd fixmyprompt
python3 install.py         # macOS / Linux / Windows  (or:  ./install.sh)
```

On **Windows** (PowerShell):

```powershell
git clone https://github.com/harshivpgajjar/fixmyprompt.git
cd fixmyprompt
py install.py              # or:  .\install.ps1
```

The cross-platform `install.py` installs the runtime to `~/.claude/fixmyprompt`, wires the `UserPromptSubmit` hook into `~/.claude/settings.json` (via a tiny Node launcher so it works identically on every OS — Node ships with Claude Code), installs the `/refine` skill, and puts the `fixmyprompt` CLI on your `PATH` (a symlink in `~/.local/bin` on macOS/Linux; a `.cmd` shim + user-PATH entry on Windows). Re-running it after a `git pull` is safe: it never touches your config, prompt log, or learned criteria/project hints, and it refuses to overwrite a malformed `settings.json`.

> **Windows note:** install real Python from **python.org** (tick *"Add python.exe to PATH"*) — the Microsoft Store `python`/`python3` aliases don't work for the hook. The warm daemon and tmux inject are macOS/Linux-only; on Windows FixMyPrompt uses the keyless local-scaffold coach (no daemon needed) and the clipboard for send-as-is.

### Updating

Marketplace install: `/plugin update fixmyprompt@fixmyprompt`. Manual install: `git pull && ./install.sh`.

## Claude Desktop & manual setup

FixMyPrompt is a **Claude Code plugin** — it works through a `UserPromptSubmit` hook. Hooks are a Claude Code feature, so FixMyPrompt runs everywhere Claude Code runs, because they all share the same `~/.claude/settings.json`:

| Where you use Claude | Runs FixMyPrompt? | Why |
|---|---|---|
| Claude Code **CLI** (terminal) | ✅ Yes | Reads `~/.claude/settings.json` |
| Claude Code **desktop app** (Code tab) | ✅ Yes | Same settings file as the CLI |
| Claude Code **IDE extensions** (VS Code / JetBrains) | ✅ Yes | Same settings file |
| Standalone **Claude Desktop** chat app | ❌ No | That app uses MCP servers (`claude_desktop_config.json`), not hooks — FixMyPrompt is a hook, not an MCP server |

> **In short:** use it in the **Claude Code desktop app** (or CLI/IDE). The one place it can't run is the separate Claude *Desktop* chat app, which has no hook system. If that's the only Claude app you use, FixMyPrompt won't apply to it.

The easiest path is [Option A or B above](#install) — both wire everything for you. The steps below are for wiring it **by hand** (e.g. on the desktop app, or to see exactly what gets configured).

### Step-by-step manual setup (the config JSON)

1. **Get the code onto your machine** and into the runtime location the plugin expects:
   ```bash
   git clone https://github.com/harshivpgajjar/fixmyprompt.git ~/.claude/fixmyprompt
   ```

2. **Open your Claude Code settings file** (create it if it doesn't exist):
   - macOS / Linux: `~/.claude/settings.json`
   - Windows: `%USERPROFILE%\.claude\settings.json`

3. **Add the `UserPromptSubmit` hook.** Merge this into the JSON (keep any hooks you already have — `UserPromptSubmit` is an array, so append to it rather than replacing). The hook invokes a tiny **Node launcher** that finds Python and runs the coach — this one command works on macOS, Linux, and Windows (replace the path with your absolute path; forward slashes are fine on Windows too):
   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "matcher": "",
           "hooks": [
             {
               "type": "command",
               "command": "node",
               "args": ["<HOME>/.claude/fixmyprompt/bin/coach_gate_launcher.mjs"],
               "timeout": 20
             }
           ]
         }
       ]
     }
   }
   ```
   The `matcher: ""` means "every prompt"; the hook itself decides what (if anything) to coach, and **fails open** — if anything goes wrong it lets your prompt straight through, so it can never block you.

4. **Turn coaching on** (it ships **off** — nothing intercepts your prompts until you opt in):
   ```bash
   ~/.claude/fixmyprompt/bin/fixmyprompt on
   ```

5. **Restart Claude Code** (or start a new session) so it re-reads `settings.json`. Send a deliberately vague prompt like `make the site responsive` — you should see FixMyPrompt step in before it's sent. Toggle off any time with `fixmyprompt off`.

### Choosing a model *and* effort level

Two independent dials control cost vs. capability — **model** (which brain) and **effort** (how hard it thinks). Set both to fit the task:

**Model** — `/model <alias>` in a session (saved as your default), or the `"model"` key in `settings.json`, or the `ANTHROPIC_MODEL` env var (env wins if set):
```json
{ "model": "sonnet" }
```
Aliases: `haiku` (fastest/cheapest) · `sonnet` (default, most coding) · `opus` (deepest reasoning) · `opusplan` (Opus while planning, Sonnet to execute) · `default` (your account default). Add `[1m]` for a 1M-token context window, e.g. `opus[1m]`.

**Effort** — `/effort <level>` in a session (persists across sessions, except `max`):

| Level | Use for | Cost/time |
|---|---|---|
| `low` | simple edits, high-volume/mechanical work | cheapest, fastest |
| `medium` | balanced everyday work | moderate |
| `high` *(default)* | most coding & agentic tasks | mid |
| `xhigh` | hard debugging / architecture | more thinking tokens + time |
| `max` | one hardest problem (current session only) | maximum |

> **Inline keywords, too:** put the word **`ultrathink`** in a prompt for deeper reasoning on that turn (Claude Code shows *"Deeper reasoning requested"*) — the zero-setup counterpart to `/effort`. Put **`ultracode`** in a prompt to kick off a **dynamic multi-agent workflow** for that turn (*"Dynamic workflow requested"*, `opt+w` to ignore).

**Pick both at once, by task** (this is exactly what `fixmyprompt suggest "..."` recommends for any prompt):

| Task | Model | Effort |
|---|---|---|
| Rename / typo / copy tweak | `haiku` | `low` |
| Standard feature or bug fix | `sonnet` | `high` |
| Design / "give me options" exploration | `sonnet` | `high` |
| Refactor / re-architect / gnarly race condition | `opus` | `xhigh` |

### Environment & auth

- **No API key needed.** In the Claude Code desktop app (and CLI) you authenticate by logging into your subscription — the same login you already use. FixMyPrompt runs entirely on that; you never paste an `ANTHROPIC_API_KEY`. (If one *is* set in your environment, Claude Code will prefer it — unset it to stay on your subscription.)
- **Setting env vars for hooks:** add an `"env"` block to `settings.json`; those variables are passed to hook scripts:
  ```json
  { "env": { "PCOACH_MODE": "always", "PCOACH_COOLDOWN": "90" } }
  ```
  FixMyPrompt reads its own config from `~/.claude/fixmyprompt/config.json`, but any `PCOACH_*` var overrides the matching config key (handy for a one-off session). See [Configuration](#configuration).
- **Optional faster rewrites:** `fixmyprompt daemon on` runs a warm subscription-backed daemon for ~1.5s AI-written rewrites — still no API key. Leave it off and you get instant local scaffolds instead.

## Usage

```bash
fixmyprompt tour            # interactive onboarding walkthrough (runs automatically on first use)
fixmyprompt help            # re-run the tour  (help --commands lists every command)
fixmyprompt status          # show current config
fixmyprompt try "..."       # SAFE simulator — preview what the live gate would do, changes nothing
fixmyprompt on              # enable live coaching (mode=always)
fixmyprompt mode whisper    # switch to whisper mode (main model asks, zero extra process)
fixmyprompt mode sigil      # only coach prompts starting with ??  (fully opt-in per prompt)
fixmyprompt off             # disable live coaching entirely
fixmyprompt tutorial on     # coach EVERY prompt; affirm the good ones (learning mode)
fixmyprompt suggest "..."   # best-suited model + effort for a prompt
fixmyprompt features        # browse ALL Claude Code efficiency features (use-case + token/time cost)
fixmyprompt features model  # ...or filter to one category (context|reasoning|model|delegation|…)
fixmyprompt tips "..."      # the one Claude Code feature a specific prompt should reach for
fixmyprompt project add "<name-or-path-substring>" "<clarifying question>"
fixmyprompt project list    # see/manage your per-project clarifying hints
fixmyprompt daemon on       # optional: fast AI-written rewrites on your subscription, no key
fixmyprompt refine "..."    # refine a rough prompt right now, from the shell
fixmyprompt report          # weekly prompting trend + top gaps + outcome stats
fixmyprompt progress week   # richer tracker: sparkline, streak, most-improved axis (day|week|month)
fixmyprompt digest          # send your progress report to Telegram (if configured) or print it
fixmyprompt selftest        # offline classifier smoke test
```

Start here: run `fixmyprompt tour` for a 60-second walkthrough, or `fixmyprompt try "fix the login page"` to see the coach's judgment with zero risk, then `fixmyprompt on` when you're ready to feel it live.

### Extra features

- **Interactive onboarding** (`fixmyprompt tour`, re-run via `fixmyprompt help`): a short guided walkthrough of the coach flow, the feature catalog, teach-mode, the daemon, and token-usage warnings. It runs automatically the first time you use FixMyPrompt.
- **Images are never intercepted**: a submission that carries a screenshot/image is *never* blocked — it always passes straight through (coaching, if any, rides along as a non-blocking note). Blocking a submission would discard the attachment, so FixMyPrompt refuses to risk your image. You never have to re-attach.
- **Tutorial (teach-mode)** (`fixmyprompt tutorial on`, on by default after install): coaches *every* real prompt regardless of size/vagueness. Well-formed prompts get an **affirmation** ("Well-specified ✓ — done-state, constraints. Keep doing this") so you learn what good looks like, not just what's broken. Continuations/commands/pastes always stay silent.
- **Model + effort suggestion**: every coaching output recommends the best-suited model + effort tier — mechanical edits → a small/cheap model, design/iteration → a mid-tier model, hard/architectural work → your top subscription model (with an optional note for stronger models you may have separate access to).
- **Claude Code feature catalog** (`fixmyprompt features`): a browsable, grouped reference of the *built-in Claude Code features* that make you efficient — context (`/clear`, `/compact`, `/context`), reasoning (`ultrathink`, `/effort`, plan mode), model routing (`/model`), delegation (`ultracode`, `subagents`, `/goal`, `/loop`, parallel agents), input (`@file`, vision/screenshots), output (`artifacts`), recovery (`/rewind`), memory (`CLAUDE.md`, `/memory`), sessions (`--resume`, `/branch`, `/export`), and diagnostics (`/usage`, `/permissions`). Each entry says **when to use it** and its **token/time trade-off**, so hidden features are discoverable and you pick the right tool for the task. Filter by category with `fixmyprompt features <category>`.
- **Situational feature tips with an execution path**: the coach surfaces the single most relevant feature exactly when a prompt calls for it — and gives you the **exact thing to run**, not just "you could use X." Starting new work → `→ run /clear first`; a task with a finish line → the ready-to-paste `→ /goal all tests pass` (condition filled from your own words); broad multi-file work → `→ add "Use subagents…" to your prompt`; a hard/architectural task → `→ add the word ultrathink to your prompt`. Preview any prompt's tip with `fixmyprompt tips "..."`.
- **Per-project hints**: `fixmyprompt project add` teaches the coach a clarifying question for a specific project/directory (e.g. "which app — mobile or web?"), which then gets baked into rewrites and scaffolds automatically when you're working in that directory.
- **Acceptance-criteria memory**: the coach learns your recurring done-criteria from the rewrites you accept, and starts suggesting them proactively.
- **Outcome tracking**: measures whether coached prompts actually lead to *fewer* follow-up corrections in the same session — proof the coaching helps, not just noise. Self-gating: it says "not enough data yet" honestly rather than overclaiming on a handful of samples.
- **Progress tracker**: self-sufficiency trend vs. the previous period, an ASCII sparkline, your current/best streak of coaching-free execute prompts, and your "prompt of the period" — your sharpest recent prompt, surfaced as a template to repeat.
- **Voice-dictation de-ramble**: the LLM refiner detects rambling dictation and tightens it — disfluencies stripped, self-corrections resolved, your voice kept.
- **Weekly Telegram digest**: `fixmyprompt digest-schedule on` sends your progress report to Telegram every Sunday (needs a notifier script — see the source for the hook point if you want to wire your own).

## Configuration

`~/.claude/fixmyprompt/config.json` (or `PCOACH_*` env vars override it):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `off` | `always` / `whisper` / `sigil` / `off` |
| `sigil` | `??` | prefix that opts a prompt in, in sigil mode |
| `min_words` | `4` | never coach prompts shorter than this |
| `cooldown_sec` | `90` | anti-nag: no second coach within this window per session |
| `tutorial` | `false` | coach every prompt (see Tutorial mode above) |
| `use_daemon` | `false` | route rewrites through the warm subscription daemon |
| `inject` | `true` | in tmux, paste the refined text into the input line |
| `model` | `claude-haiku-4-5` | model used by the daemon/API refiner backends |
| `coach_below_quality` | `0.7` | only coach when the local quality score is below this |

## Refiner backends — no API key needed, ever

A submit-time hook has a hard latency budget (a few seconds), and the only subscription-authenticated model call from a shell (`claude -p`) spins up a full agent session (10–40s) — far too slow to sit in front of every prompt. So FixMyPrompt does **not** put a slow LLM call in the critical path by default:

- **Live gate, default (zero key, zero network):** a deterministic *local* classifier finds the gaps and the gate blocks with a fill-in **scaffold** + one teaching point — instant, private, works on any Claude subscription with zero setup.
- **Live gate + optional warm daemon (subscription, no key, ~1.5s):** run `fixmyprompt daemon on` and the block gate switches to AI-*written* rewrites with one-keystroke `y`-to-send. It works by keeping one `claude` session warm in the background (a small resident process, opt-in, auto-restarting via a macOS LaunchAgent), so each rewrite is inference-only. It never touches a credential — the child process authenticates itself exactly like any normal Claude Code session. If the daemon is down or slow, the gate falls back to the instant scaffold automatically.
- **Live gate, API-key upgrade (optional):** set `ANTHROPIC_API_KEY` for the fastest path (~1s), no daemon required.
- **`/refine` skill (no key, always available):** a full LLM rewrite using your current session's model — on demand, higher quality than the scaffold, needs nothing extra.

**Recursion guard:** the daemon shells out to `claude -p`; that nested session's own `UserPromptSubmit` hook would otherwise re-enter this same gate. FixMyPrompt sets an environment flag on the subprocess so nested invocations no-op instantly — verified by a dedicated test.

## Privacy

The prompt log (`~/.claude/fixmyprompt/prompt-log.jsonl`) stores quality scores plus a short preview of each prompt; anything that looks like a secret (API keys, tokens, passwords) suppresses the preview entirely, and the learned criteria/project stores apply the same filter. Nothing is sent anywhere except to Claude itself (your own subscription, for the optional AI-written-rewrite paths). Delete the log anytime — it's just a local file.

## Architecture

```
UserPromptSubmit hook ─▶ bin/coach_gate.py
                            │
   ┌────────────┬───────────┼────────────┬──────────────┐
scorer.py    refiner.py   daemon.py   state.py    context_hints.py
(local gate) (LLM, fail-  (warm sub-  (one-shot    (criteria memory
 <1ms, no     open)        scription   bypass +     + per-project
 network)                  daemon)     cooldown)     hints)
   └──────────────── scorelog.py ──▶ report.py / outcome.py / suggest.py
```

- `fixmyprompt/scorer.py` — deterministic classifier (mode, gaps, quality, model/effort routing), sub-millisecond, no I/O.
- `fixmyprompt/refiner.py` — the LLM refiner (daemon → API → CLI fallback chain), fail-open.
- `fixmyprompt/daemon.py` — the optional warm subscription daemon (unix socket, auto-recycling session).
- `fixmyprompt/state.py` — the loop-proof one-shot bypass + cooldown + backstop.
- `fixmyprompt/context_hints.py` — per-project hints and learned acceptance criteria.
- `fixmyprompt/scorelog.py` / `report.py` / `outcome.py` / `suggest.py` — measurement, the weekly/progress reports, and outcome tracking.
- `bin/coach_gate.py` — the hook orchestration (the `UserPromptSubmit` entrypoint).
- `bin/fixmyprompt` — the CLI.

## Tests

Pure stdlib, zero dependencies, run from the repo root:

```bash
python3 -m unittest discover -s tests -v
```

245+ tests covering: classifier precision/recall on real prompt styles, the loop-proof and fail-open invariants, the accept/edit/override branches, whisper/tutorial contract correctness, sigil mode, the daemon lifecycle and recursion guard, secret redaction, image-attachment preservation, ReDoS guards on the classifier regexes, and the report/progress/outcome math.

For a hands-on pass in a real session (the *feel*, not just the logic), see [docs/MANUAL_TESTING.md](docs/MANUAL_TESTING.md) — a checklist covering every feature.

## Uninstall

```bash
./uninstall.sh            # stops the daemon, removes the hook + CLI, keeps your config/log
./uninstall.sh --purge    # also deletes ~/.claude/fixmyprompt entirely
```

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Design rationale and history for the curious: [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE) — do whatever you want with it.
