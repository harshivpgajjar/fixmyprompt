# Whetstone — a prompt coach for Claude Code
*Spec v0.2 · 2026-07-05. Working name "Whetstone" (it sharpens the user, not just the prompt). Alts: Hone, Muse, Prompt Coach.*

> **STATUS: BUILT & INSTALLED (2026-07-05).** All three phases shipped in one pass. 88/88 tests green (stdlib unittest); scorer and Coach Gate hardened + mutation-tested by Fable 5 subagents; every hook branch live-verified. Installed to `~/.claude/whetstone`, hook wired into settings.json (coaching **off** by default — `whetstone on` to enable), `/refine` skill live, 911 real prompts backfilled into the report, weekly self-audit now emits a Prompting section. See README.md for usage.

## Problem
Prompt quality is the biggest lever most Claude Code users have left, and nobody teaches it. Existing "prompt enhancers" rewrite one prompt and create dependence — they never build the skill. The gap between a user's best prompt and their worst is enormous (for the primary user: a full written spec that shipped a product in 3 days, vs. "i love tide, perfect it"). Close that gap by **teaching, in the flow, without friction** — and measure the improvement over time.

## Users
- **v1 (me-first):** Harshiv — voice-dictates, works in explore→execute cycles, has a recorded taste file. Tune hard to this.
- **v3 (everyone):** any Claude Code user, via a configurable distributable plugin.

## Core principles (non-negotiable)
1. **Teach > rewrite.** The goal is that the user needs the tool *less* over time. Every intervention carries at most ONE teaching point.
2. **Silent on continuations.** "yes / go / continue / run it", slash commands, and resubmits are NEVER touched. (~60% of real prompts.)
3. **Mode-aware.** Distinguish *intentional* vagueness (explore mode — "blow me away" is a valid discovery ask; leave it, maybe confirm) from *accidental* under-specification (execute mode missing a constraint the user has in mind). A naive refiner that "fixes" explore prompts would sabotage the user's own workflow — this is the #1 way this plugin could do harm.
4. **Preserve voice.** Add operational scaffolding (mode, constraints, "done means…"); never convert Hinglish/voice-dictation into corporate prompt-ese. Intent in, scaffolding added, personality kept.
5. **Always bypassable.** A prefix (`!` / `raw:`) and a global toggle. Friction is the thing that gets plugins uninstalled.

## Hard technical constraints (verified 2026-07-05 via claude-code-guide)
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

**Refiner:** `claude-haiku-4-5` via curl, structured JSON out (`{needs_refinement, refined}`), fail-open on any error/timeout (prompt just sends normally — the coach can never lock you out). ~0.8–2.5s only on coached prompts (~20%); ~$0.002–0.005 each (pennies). Reads `core.md` + `design-taste.md` so refinement is voice- and taste-aware, and mode-aware (never "fixes" an explore prompt).

**The tmux tier (opt-in `PCOACH_INJECT=1`) — the actual dream:** if you run Claude Code inside tmux, after the block the hook fires `tmux set-buffer` + `paste-buffer -p` targeted at your pane — the refined text **materializes in your input line, editable, ~0.5s after you hit Enter.** Pane-targeted (survives cmd-tab), bracketed-paste (multi-line safe), no macOS permission prompts. Clipboard is the fallback if the redraw race misses. (iTerm2 AppleScript and a guarded single ⌘V via System Events are lower tiers for non-tmux setups.)

**Whisper fallback:** prompts that fall just *under* the gate get silent `additionalContext` (e.g. explore mode → "produce 5 distinct hero variants from design-taste.md") — useful even when the coach stays quiet.

**Rejected:** a PTY wrapper around the `claude` binary (the only path to literal in-place editing) — re-implements the whole line editor, breaks on every UI update, corrupts input on failure. tmux paste-buffer gives ~90% of it at ~2% of the risk.

### Phase 3 — Distributable "Whetstone" plugin (for people everywhere)
- Strip personal files → generic default rubric + taste template. Aggressiveness as a setting (`off` / `gentle` / `assertive`). Package per the verified plugin structure; ship a marketplace entry. Bundles its own weekly prompt-quality report.

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

## Open questions
- Phase 2 default: block-and-suggest (visible, teaches, small friction) vs. silent-context-injection (invisible, better outcomes, no teaching) — probably offer both, default block-and-suggest only for the highest-value gaps.
- Name: Whetstone / Hone / Muse / Prompt Coach.
