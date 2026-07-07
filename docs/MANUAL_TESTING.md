# FixMyPrompt — manual test checklist

A hands-on QA pass covering every feature, for you to run yourself in a real
Claude Code session. The automated suite (`python3 -m unittest discover -s
tests`, 245 tests) covers the logic; this covers the *feel* — what you actually
see when you type things.

Tip: `fixmyprompt try "..."` previews most of these safely (sandboxed, never
touches your real log/state) before you trust the live hook.

---

## 1. Onboarding & discovery

- [ ] **First-ever run**: on a machine/home dir with no `.toured` marker, run `fixmyprompt` with no args in a real terminal → the interactive tour launches automatically.
- [ ] **Tour walkthrough**: run `fixmyprompt tour` → all 6 steps render (coach flow, features, teach-mode, daemon, token warnings, wrap-up); pressing Enter advances each step.
- [ ] **Tour teach-mode prompt**: at step 3, answer `y` → `fixmyprompt status` afterward shows teach-mode on; run again and answer `n` → shows off.
- [ ] **Tour daemon prompt**: at step 4, answer `y` (with daemon not already running) → `fixmyprompt daemon status` shows it running.
- [ ] **`fixmyprompt help`**: re-invokes the same tour.
- [ ] **`fixmyprompt help --commands`**: prints the full command list (not the tour).
- [ ] **First-run hint**: on a fresh home dir, run any other command (e.g. `fixmyprompt status`) *before* touring → a one-line "👋 New to FixMyPrompt? Run `fixmyprompt tour`" hint appears once; run the same command again → hint is gone.
- [ ] **Unknown command**: `fixmyprompt bogus` → prints usage, exits non-zero (doesn't crash).

## 2. Live coaching — the core loop

- [ ] **`fixmyprompt status`**: shows mode, sigil, min_words, cooldown, model, config path.
- [ ] **`fixmyprompt on`**: sets mode=always; `status` confirms it.
- [ ] **Vague prompt blocks**: with coaching on (new session, since it applies to new sessions), type a short/vague prompt like `make it better` → the turn is intercepted with a coaching banner instead of being sent.
- [ ] **Send as-is**: after being coached, press Enter again on the *same* unedited text → it sends this time (one-shot bypass) — confirm it does NOT get coached a second time.
- [ ] **Well-formed prompt passes silently**: type a fully-specified prompt (target, action, done-state) → no interception, goes straight to the model.
- [ ] **Edit-and-resend**: after a coach, edit the prompt substantially and resend → sends normally (not treated as a bare confirm).
- [ ] **`y` to accept a rewrite**: when the coach offers a rewritten prompt (LLM mode / daemon on), reply `y`/`yes`/`ok` → the rewritten version is what the model acts on.
- [ ] **Cooldown / anti-nag**: two vague prompts in quick succession in the same session → only the first is coached; the second within the cooldown window passes through.
- [ ] **`fixmyprompt off`**: sets mode=off; vague prompts afterward (new session) are never intercepted.
- [ ] **`fixmyprompt mode whisper`**: vague prompt is NOT blocked, but the main model asks a clarifying question inline (no separate coaching UI).
- [ ] **`fixmyprompt mode sigil`**: a prompt starting with `??` gets coached; the identical prompt without `??` passes silently.

## 3. Teach-mode (tutorial)

- [ ] **`fixmyprompt tutorial on`**: every real prompt gets a reaction, including well-formed ones.
- [ ] **Affirmation**: send a genuinely well-specified prompt with teach-mode on → get a "Well-specified ✓" affirmation, not a scaffold.
- [ ] **Scaffold still shown for gaps**: send a vague prompt with teach-mode on → get the normal scaffold/rewrite, not a false affirmation.
- [ ] **`fixmyprompt tutorial off`**: only under-specified prompts get coached again.
- [ ] **`fixmyprompt tutorial status`** (or bare `fixmyprompt tutorial`): reports current on/off state.

## 4. Image / attachment safety (the bug fix)

- [ ] **Vague prompt + attached image**: paste a screenshot into the prompt box along with a short/vague ask → the prompt is **never blocked**; it goes through (with at most a non-blocking note), and the image is NOT lost.
- [ ] **Well-formed prompt + image**: fully-specified prompt with an image attached → passes straight through, image intact.
- [ ] **New-work + image**: a "let's start a new feature…" prompt with an image → the /clear tip may ride along, but still never blocks/discards the image.
- [ ] **Resend after image submit**: confirm you never have to re-attach the image on a subsequent turn in the same exchange.

## 5. Feature catalog

- [ ] **`fixmyprompt features`**: prints the full grouped catalog (Context, Reasoning, Model, Delegation, Input, Output, History, Memory, Sessions, Diagnostics), each entry with a use-case and a token/time trade-off.
- [ ] **`fixmyprompt features reasoning`**: filters to just that category (includes `ultrathink` and `/effort`).
- [ ] **`fixmyprompt features delegation`**: includes `ultracode`, subagents, `/goal`, `/loop`, parallel agents.
- [ ] **`fixmyprompt features bogus`**: unknown category → helpful message listing valid categories, not a crash.
- [ ] **`fixmyprompt tips "..."`**: for each, confirm the tip AND its execution path:
  - `"let's start a new feature: profiles"` → suggests `/clear` (or `/compact`), with a literal `→ run /clear first…` line.
  - `"keep going until all tests pass"` → suggests `/goal all tests pass` (condition filled from your own words, not a `<placeholder>`).
  - `"find all usages across the whole codebase"` → suggests adding "Use subagents…" to the prompt.
  - `"re-architect the reporting pipeline for scale"` → suggests adding the word `ultrathink`, plus Shift+Tab for plan mode.
  - `"add a call button next to the phone number"` → no tip (ordinary, well-scoped ask).

## 6. Model + effort suggestion

- [ ] **`fixmyprompt suggest "rename getUser to fetchUser everywhere"`** → Haiku 4.5 · low effort.
- [ ] **`fixmyprompt suggest "add a settings page with a dark mode toggle"`** → Sonnet 5 · high effort.
- [ ] **`fixmyprompt suggest "blow me away with a landing page, go wild"`** → Sonnet 5 · high effort (explore mode).
- [ ] **`fixmyprompt suggest "refactor and re-architect the whole auth system"`** → Opus 4.8 · xhigh effort, with a Fable 5 upgrade note mentioning "subscription."
- [ ] Confirm the **live coaching banner** also shows this same suggestion line under a real coached prompt.

## 7. Safe simulator & manual refine

- [ ] **`fixmyprompt try "fix the login page"`**: shows what the live gate *would* do — mode, would-coach verdict, gaps — without touching your real log, config, or session state.
- [ ] **`fixmyprompt try` on a well-formed prompt**: reports "would PASS silently" with a reason.
- [ ] **`fixmyprompt refine "make it responsive"`**: prints a refined/scaffolded version right from the shell, with a one-line "why."
- [ ] **`/refine` skill** (inside a Claude Code session): pass a rough prompt as an argument → get `mode:`, `── refined ──`, and a `why:` line; does NOT execute the prompt.

## 8. Optional daemon (fast rewrites)

- [ ] **`fixmyprompt daemon status`**: reports running/stopped, pid, turns served, socket path.
- [ ] **`fixmyprompt daemon on`**: starts it; mode auto-switches to `always` if it was off/whisper; a subsequent coached prompt shows an AI-written rewrite (not just a local scaffold) within ~1–2s.
- [ ] **`fixmyprompt daemon off`**: stops it; mode restores to whatever it was before `daemon on` switched it.
- [ ] **Survives a `daemon on` → close terminal → reopen**: `daemon status` still shows running (KeepAlive agent).

## 9. Per-project hints

- [ ] **`fixmyprompt project add "myapp" "which surface — mobile or web?"`**: adds a hint.
- [ ] **`fixmyprompt project list`**: shows it.
- [ ] Working in a directory matching `myapp`, a vague prompt's coaching/scaffold references that clarifying question.
- [ ] **`fixmyprompt project remove myapp"`**: removes it; `project list` confirms.

## 10. Reporting & progress

- [ ] **`fixmyprompt report`**: prints your weekly prompting trend, top gaps, and outcome stats (or an honest "not enough data yet" if you're new).
- [ ] **`fixmyprompt progress week`** (also try `day`, `month`): sparkline, streak, "prompt of the period."
- [ ] **`fixmyprompt digest`**: sends to Telegram if `~/.claude/memory/notify.sh` exists, else prints the digest text with a note.
- [ ] **`fixmyprompt digest-schedule on`**: schedules the Sunday 9am digest (check `launchctl list | grep fixmyprompt.digest`); `digest-schedule off` removes it.

## 11. Privacy / secret redaction

- [ ] Send a prompt containing something that looks like a real secret (e.g. a fake `sk-ant-...`-shaped string, a fake AWS key, a fake JWT) → check `~/.claude/fixmyprompt/prompt-log.jsonl` afterward: the `preview` field should read `[redacted: possible secret]`, not the actual text.
- [ ] Send an ordinary prompt like `"add a password field to the login form"` → confirm this is **NOT** redacted (bare mentions of "password" without a key=value form must stay visible).

## 12. Install / uninstall lifecycle

- [ ] **Marketplace install**: `/plugin marketplace add harshivpgajjar/fixmyprompt` then `/plugin install fixmyprompt@fixmyprompt` inside a session → hook wired, `/refine` skill available.
- [ ] **Manual install**: `git clone` + `./install.sh` on a clean machine (or temp `$HOME`) → runtime lands in `~/.claude/fixmyprompt`, hook wired into `settings.json`, CLI symlinked, coaching OFF by default, teach-mode ON by default.
- [ ] **Re-running `./install.sh`** after a `git pull`: doesn't clobber your config/log/hints; says "existing config kept" / "hook already wired."
- [ ] **`./uninstall.sh`**: removes the hook entry from `settings.json` (leaving any other hooks you have untouched) and the runtime dir.
- [ ] **Malformed `settings.json` before install**: hand-corrupt it (e.g. add a stray comma) → `./install.sh` should **abort with a clear error and NOT overwrite it** (not silently reset to `{}`).

## 13. Robustness / "doesn't break my session"

- [ ] **Huge paste**: paste a very large single-line blob (minified code, a long log line) into the prompt box → no multi-second freeze before your turn goes through.
- [ ] **Non-English / emoji prompt**: send a prompt in Hindi/Hinglish or full of emoji → coaching (if triggered) renders correctly, no crash.
- [ ] **Empty / one-word prompt**: send just `"go"` or a single word → passes through silently (below the word-count gate), no crash.
- [ ] **`fixmyprompt selftest`**: offline classifier smoke test — should report all checks passed with no network.

---

### If something fails
Note the exact command/prompt you used and what you saw vs. expected — that's
everything needed to reproduce and fix it.
