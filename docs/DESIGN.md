# Design notes: FixMyPrompt — a prompt coach for Claude Code
*Design rationale from the original build, kept for contributors. Not required reading to use the plugin — see the root README for that.*

> **STATUS: shipped and open-sourced.** All three phases below are built. 197+ tests green (stdlib unittest); scorer and Coach Gate mutation-tested; every hook branch live-verified. See README.md for install and usage.

## Problem
Prompt quality is the biggest lever most Claude Code users have left, and nobody teaches it. Existing "prompt enhancers" rewrite one prompt and create dependence — they never build the skill. The gap between a person's best prompt and their worst is often enormous (a full written spec that ships a product in three days, vs. "make it better, you know what I mean"). Close that gap by **teaching, in the flow, without friction** — and measure the improvement over time.

## Users
- **v1 (build target for the initial author):** a power user who voice-dictates, works in explore→execute cycles, and wants the coach tuned hard to real usage first.
- **v3 (everyone):** any Claude Code user, via a configurable, open-source, distributable plugin — this is that v3.

## Core principles (non-negotiable)
1. **Teach > rewrite.** The goal is that the user needs the tool *less* over time. Every intervention carries at most ONE teaching point.
2. **Silent on continuations.** "yes / go / continue / run it", slash commands, and resubmits are NEVER touched. (~60% of real prompts.)
3. **Mode-aware.** Distinguish *intentional* vagueness (explore mode — "blow me away" is a valid discovery ask; leave it, maybe confirm) from *accidental* under-specification (execute mode missing a constraint the user has in mind). A naive refiner that "fixes" explore prompts would sabotage the user's own workflow — this is the #1 way this plugin could do harm.
4. **Preserve voice.** Add operational scaffolding (mode, constraints, "done means…"); never convert Hinglish/voice-dictation into corporate prompt-ese. Intent in, scaffolding added, personality kept.
5. **Always bypassable.** A prefix (`!` / `raw:`) and a global toggle. Friction is the thing that gets plugins uninstalled.

## Hard technical constraints (verified via Claude Code docs)
- `UserPromptSubmit` hook CAN: read the prompt, block it with a visible `systemMessage`, inject hidden `additionalContext`. It runs synchronously (or `async:true`+`asyncRewake:true` to not block).
- It CANNOT: rewrite the prompt, or put editable refined text into the input line for pre-send review. (`updatedInput` exists only on `PreToolUse`.)
- No native prompt-refiner ships in Claude Code.
- Therefore the live UX is **block-and-suggest** (submit → see refined version + tip → resend or bypass), NOT edit-in-place. The clean *interactive* path is a **slash command**.
- Plugin packaging: `.claude-plugin/plugin.json` + `hooks/hooks.json` + `skills/…` + `bin/…`, installable via `/plugin install <url>` or a marketplace.

## Architecture — three phases

### Phase 1 — MVP (opt-in, zero friction, works today)
- **`/refine [rough prompt]`** skill. Returns: (1) a refined prompt that preserves voice, (2) the detected mode, (3) exactly one teaching point, (4) for execute-mode, a proposed "done means…" line. Reads `~/.claude/memory/core.md` + `design-taste.md` so it's tuned to the user. Opt-in — no interception, no latency, no risk.
- **Prompt scorer** — a deterministic local function (no LLM). Scores any prompt on: `is_continuation`, `mode` (explore/execute/unknown), `has_constraints`, `has_done_criteria`, `has_reference` (design asks). Appends to `~/.claude/memory/prompt-log.jsonl`.
- **Weekly report** — extend the existing Saturday `com.claude.weekly-audit` to read `prompt-log.jsonl` and report the trend ("execute-mode prompts self-sufficient: 41% → 73% this month") + the top 2 recurring gaps. This is the *teaching made measurable* — the feature no existing tool has.

### Phase 2 — "Coach Gate": the enter → refine → send flow (Fable 5 design, 2026-07-05)

The literal dream (edit text in place) is impossible — no hook API can pre-fill the input line. But the achievable version is ~90% of it, and inside tmux it's ~100%.

**The flow:** you hit Enter on a rough prompt → a `UserPromptSubmit` hook BLOCKS it (never reaches the model) → your refined version is displayed AND copied to your clipboard → you choose, in one keystroke each:
- **`y` + Enter** → sends the refined version (zero paste). Legal within constraints: the hook can't rewrite your prompt, but it CAN pass `y` through with the refined text attached as hidden `additionalContext` — the model acts on the refined version.
- **⌘V, tweak, Enter** → paste the refined text, edit it, send your edit.
- **anything else + Enter** → sends exactly what you typed (your original or something new).

**Loop-proof by design:** a one-shot, session-scoped bypass flag (`~/.claude/prompt-coach/pending/<session_id>.json`, 10-min TTL, consumed on read) guarantees the hook can NEVER block twice in a row. The second Enter always goes through → no refine loops, ever, and "override" costs exactly one extra Enter.

**Silence gates (all local, ~0ms, never nag):** pass straight through if the prompt starts with `/ ! #`, is <12 words, matches a continuation regex (`yes/ok/go/continue/do it/…`), looks like a pasted log/code block, or the session was coached in the last N minutes. Then Haiku's own `needs_refinement:false` suppresses the block on already-good long prompts — so the gate only appears when there's a genuinely better version to offer.

**Refiner:** subscription-native by default (a warm `claude` daemon, or the API path if you set a key), fail-open on any error/timeout (prompt just sends normally — the coach can never lock you out). Optionally reads `~/.claude/memory/core.md` and `design-taste.md` if present (Claude Code's own memory convention) so refinement is voice- and taste-aware where that data exists, and is mode-aware everywhere (never "fixes" an explore prompt).

**The tmux tier (opt-in `PCOACH_INJECT=1`) — the actual dream:** if you run Claude Code inside tmux, after the block the hook fires `tmux set-buffer` + `paste-buffer -p` targeted at your pane — the refined text **materializes in your input line, editable, ~0.5s after you hit Enter.** Pane-targeted (survives cmd-tab), bracketed-paste (multi-line safe), no macOS permission prompts. Clipboard is the fallback if the redraw race misses. (iTerm2 AppleScript and a guarded single ⌘V via System Events are lower tiers for non-tmux setups.)

**Whisper fallback:** prompts that fall just *under* the gate get silent `additionalContext` (e.g. explore mode → "produce 5 distinct hero variants from design-taste.md") — useful even when the coach stays quiet.

**Rejected:** a PTY wrapper around the `claude` binary (the only path to literal in-place editing) — re-implements the whole line editor, breaks on every UI update, corrupts input on failure. tmux paste-buffer gives ~90% of it at ~2% of the risk.

### Phase 3 — Distributable "FixMyPrompt" plugin (for people everywhere) — DONE
Personal seeds stripped (criteria/project stores ship empty and are user-populated via `fixmyprompt project add`); packaged with `.claude-plugin/plugin.json` + `marketplace.json` for `/plugin install`; MIT licensed; public repo. Bundles its own weekly prompt-quality report.

## Non-goals
- Not an inline text editor (the tool can't do it — don't fake it).
- Not a silent rewriter that hides the original (that's dependence, not teaching).
- Never intervenes on continuations/confirmations, or on intentional explore prompts.
- Never rewrites the user's voice.

## Acceptance criteria (MVP / Phase 1)
- `/refine` run on 10 real rough prompts pulled from history: preserves voice on all 10, correctly identifies mode on ≥8, and every output carries exactly one teaching point.
- Scorer logs structured JSON for each; runs in <50ms, no network.
- The Saturday audit shows a "Prompting" section with at least the self-sufficiency trend.
- Zero friction on a control set of 10 continuation prompts (scorer marks them `is_continuation`, nothing else happens).

## Risks & mitigations
- **Friction → uninstall:** opt-in MVP; Phase-2 gate skips ~all short prompts; always bypassable.
- **Latency:** template-first, LLM optional + async.
- **Token cost:** Haiku, and only when the local gate engages.
- **Over-refining discovery:** mode-awareness is a first-class requirement, tested explicitly.

## Resolved design decisions
- Both block-and-suggest AND silent-context-injection shipped, as separate modes (`mode always` vs `mode whisper`) rather than picking one — different users want different tradeoffs between friction and teaching.
- Name settled on **FixMyPrompt**.
