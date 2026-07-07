"""Claude Code feature tips — teach users the built-in features that make them
more efficient, surfaced at the moment a prompt signals the relevant situation.

Prompt-quality coaching is only half of "get better at Claude Code". The other
half is knowing WHEN to reach for /clear, /goal, plan mode, subagents, etc.
This module detects situational signals in the prompt and returns one relevant,
verified tip.

Only features verified against the current Claude Code docs are used here — no
invented command names. Pure/deterministic; no I/O, no network.

analyze(prompt, features) -> {"tip": str, "engage": bool} | None
  - "engage": whether this tip is worth surfacing even on an already well-formed
    prompt (True for the high-value context-switch / big-goal cues the user most
    benefits from). Lower-value tips only ride along when the gate already fires.
"""
from __future__ import annotations

import re

# 1. Starting NEW or unrelated work — the highest-value cue. A fresh/bloated
#    context degrades output (official guidance) and wastes tokens; /clear or
#    /compact first. Precision-first: needs a genuine new-work / switch cue.
_NEW_WORK = re.compile(
    r"\b(?:"
    r"new feature|add(?:ing)? (?:a |another )?new (?:\w+ ){0,3}?feature|"
    r"new (?:project|task|module|component|page|screen|app|system|thing)|"
    r"let'?s (?:start|begin|kick off|move on)|now let'?s (?:start|build|add|create|make|implement)|"
    r"start(?:ing)? (?:a |on |building |fresh)|build me a (?:new|whole)|"
    r"moving on|move on to|switch(?:ing)? to|onto the next|next feature|"
    r"another (?:feature|thing|project|task)|"
    r"time to (?:build|start|tackle)|implement (?:a |the )?(?:new|whole)"
    r")\b",
    re.IGNORECASE,
)

# 2. A big task with a verifiable finish line — /goal keeps Claude working
#    until the condition is actually met.
_BIG_GOAL = re.compile(
    r"\b(?:"
    r"until (?:it works|all tests? pass|everything|done|it'?s done|complete)|"
    r"keep (?:going|working) until|don'?t stop until|"
    r"make sure (?:everything|all of it|the whole)|"
    r"end[- ]to[- ]end|the whole (?:thing|app|system|flow)|"
    r"all the way (?:through|done)|fully (?:working|complete|done)|"
    r"overnight|autonomous(?:ly)?|while i (?:sleep|am away)"
    r")\b",
    re.IGNORECASE,
)

# 3. Broad / multi-file / whole-codebase work — subagents fan it out and keep
#    the main context clean.
_BROAD = re.compile(
    r"\b(?:"
    r"across (?:the )?(?:codebase|repo|project|app|whole)|"
    r"(?:all|every) (?:the )?files?|the (?:whole|entire) (?:codebase|repo|project)|"
    r"everywhere|find all|search (?:the|for all|across)|"
    r"audit (?:the|all|every)|review (?:all|every|the whole)|"
    r"go through (?:all|every|the whole)"
    r")\b",
    re.IGNORECASE,
)

_TIP_NEW_WORK = (
    "💡 Starting new/unrelated work? Run /clear first (or /compact to keep a summary). "
    "Claude's output degrades as the context fills — a fresh start saves tokens and avoids sloppy results."
)
_TIP_BIG_GOAL = (
    "💡 Big task with a clear finish line? Try /goal <condition> "
    "(e.g. /goal all tests pass) — Claude keeps working until it's actually met."
)
_TIP_PLAN = (
    "💡 Big or architectural change? Toggle plan mode (Shift+Tab) first, or raise "
    "reasoning with /effort high — so Claude proposes the approach before editing."
)
_TIP_BROAD = (
    "💡 Broad or multi-part? Ask Claude to \"use subagents\" to fan the work out "
    "and keep your main context clean."
)


def analyze(prompt: str, features: dict) -> dict | None:
    """Return the single most relevant Claude Code feature tip, or None."""
    prompt = prompt or ""
    # priority: new-work > big-goal > broad (breadth → subagents) > hard (→ plan).
    # broad is checked before hard because a wide search ("across the codebase")
    # is better served by subagents than by plan mode, and some phrases match both.
    if _NEW_WORK.search(prompt):
        return {"tip": _TIP_NEW_WORK, "engage": True}
    if _BIG_GOAL.search(prompt):
        return {"tip": _TIP_BIG_GOAL, "engage": True}
    if _BROAD.search(prompt):
        return {"tip": _TIP_BROAD, "engage": False}
    # hard/architectural → plan mode; only rides along when already coaching.
    try:
        from .scorer import _HARD_TASK
        if _HARD_TASK.search(prompt):
            return {"tip": _TIP_PLAN, "engage": False}
    except Exception:
        pass
    return None
