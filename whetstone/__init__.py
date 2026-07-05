"""Whetstone — a prompt coach for Claude Code.

A prompt-quality coach that teaches better prompting in the flow, measures
improvement over time, and stays silent on everything that doesn't need it.

Module map / interface contract (stable — subagents build against this):

- config.py   : load() -> dict of settings (defaults + config.json + PCOACH_* env)
- scorer.py   : classify(prompt) -> FEATURES dict ; should_coach(features, cfg) -> bool
- refiner.py  : refine(prompt, context, cfg) -> {needs_refinement, mode, refined, tip}
- state.py    : one-shot session bypass + cooldown + backstop cache
- scorelog.py : log(prompt, features, action) -> append redacted record for the weekly report
- report.py   : summarize(days) -> markdown section for the Saturday self-audit
- bin/coach_gate.py : the UserPromptSubmit hook entrypoint (orchestrates the above)
- bin/whetstone     : CLI (report / on / off / status / refine / mode)

FEATURES schema returned by scorer.classify():
    word_count        : int
    is_command        : bool   # starts with / ! #
    is_continuation   : bool   # yes/ok/go/continue/do it/... — never coach
    looks_like_paste  : bool   # code block or pasted log/stacktrace
    is_design         : bool   # a visual/design ask
    mode              : str     # "explore" | "execute" | "other"
    has_constraints   : bool
    has_done_criteria : bool
    has_reference     : bool   # design ask that cites an example/brand/"like X"
    gaps              : list[str]  # human-readable missing pieces (execute mode)
    quality           : float  # 0.0..1.0 composite, for the trend line
"""

__version__ = "0.1.0"

# Canonical feature keys — importable so scorer and coach_gate never drift.
FEATURE_KEYS = (
    "word_count",
    "is_command",
    "is_continuation",
    "looks_like_paste",
    "is_design",
    "mode",
    "has_constraints",
    "has_done_criteria",
    "has_reference",
    "gaps",
    "quality",
)

# Actions recorded in the prompt log (for the weekly trend).
ACTION_PASS = "pass"        # went through untouched (gated out or already good)
ACTION_COACH = "coach"      # gate fired, refined version offered
ACTION_ACCEPT = "accept"    # user pressed y — sent the refined version
ACTION_EDIT = "edit"        # user resubmitted after a coach (edited or overrode)
