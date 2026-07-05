# Whetstone — HANDOFF (2026-07-05)

A Claude Code prompt-coach plugin: coaches vague prompts at submit time, mode-aware (never touches intentional "explore"/"blow me away"), voice-preserving. Teaches + measures prompting quality over time.

## State (all verified this session)
- **123/123 tests pass** (`python3 -m unittest discover -s tests`), 15 commits, pure stdlib.
- Installed at `~/.claude/whetstone` (runtime), source at `~/Desktop/whetstone` (git repo). Hook wired into `~/.claude/settings.json`; `/refine` skill live; 911 real prompts backfilled into the report; weekly self-audit emits a "Prompting (Whetstone)" section.
- **Fully subscription, NO API key** — proven. Live gate = local analysis + optional warm `claude` daemon (~1.7s written rewrites) + whisper mode. API key optional only.
- **LIVE CONFIG (a 1-day trial):** `mode=always`, `use_daemon=true`, daemon running (pid was 53177, ~200MB) under LaunchAgent `com.whetstone.daemon` (KeepAlive). `min_words=4`.

## Running processes
- **`com.whetstone.daemon`** — warm `claude` stream-json session, KeepAlive LaunchAgent. This is a persistent SERVICE, not a batch run (no completion notification needed). It self-restarts on death; gate fails open to the local scaffold if it's down. Stop with `whetstone daemon off`.

## Next steps
1. **After the trial day:** ask Harshiv how the daemon's written rewrites felt (latency? over-firing? worth the 200MB?). Tune from there.
2. If over-firing on clear prompts → tighten the scorer; if vague prompts slip → drop `min_words` to 3.
3. **Phase 3 (when he's happy):** distributable marketplace packaging — strip personal taste files (`core.md`/`design-taste.md` dependency), ship a generic default rubric + configurable aggressiveness, publish.

## Gotchas (learned this session)
- `claude -p` boots a full agent (~10-24s) — too slow for a sync hook; the daemon keeps ONE session warm so each call is inference-only. First (cold) call ~2.6s; `daemon start`/`on` warms it.
- `claude -p` waits 3s on open-but-silent stdin — always `</dev/null` for one-shots.
- OAuth-token extraction is a dead end (`--bare` rejects OAuth tokens) AND a security/ToS risk — do not revisit.
- whisper mode does NOT use the daemon (it nudges the main model); `daemon on` now auto-switches whisper/off → always.
- Test isolation: CLI `try` tests must set `WHETSTONE_HOME` to a temp dir or they read the user's live config.

## Revert
`whetstone daemon off` (stops process + removes agent) then `whetstone mode whisper` (or `off`).
