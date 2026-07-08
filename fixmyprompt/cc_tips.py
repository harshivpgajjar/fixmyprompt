"""Claude Code feature catalog + situational tips.

Two jobs:
  1. A browsable CATALOG of Claude Code efficiency features (`fixmyprompt
     features`) — each with a use-case and its token/time trade-off — so hidden
     features are discoverable and users pick the right tool for the task.
  2. SITUATIONAL tips surfaced during coaching (`analyze`) — when a prompt
     signals a specific situation, suggest the one most relevant feature.

Entries reflect current Claude Code behavior — including inline keywords like
`ultrathink` (deeper reasoning for a turn) and `ultracode` (a dynamic multi-agent
workflow for a turn), which are real: typing them in a prompt makes Claude Code
show "Deeper reasoning requested" / "Dynamic workflow requested". Pure/
deterministic; no I/O, no network.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# FEATURE CATALOG — grouped by category. Each: name, use (when to use it),
# tradeoff (token/time cost or saving). Doc-verified; curated to the features
# that actually change your token/time efficiency.
# --------------------------------------------------------------------------

FEATURES: list[dict] = [
    # --- Context management ---
    {"name": "/clear", "category": "Context",
     "use": "Switching to unrelated work or starting fresh (the old thread stays resumable via /resume).",
     "tradeoff": "Saves tokens on every later turn; a bloated context degrades output and loses early instructions."},
    {"name": "/compact [focus]", "category": "Context",
     "use": "A long session you still need — replace history with a summary, optionally focused (e.g. /compact focus on the API changes).",
     "tradeoff": "Spends one summarization pass now to save tokens on every turn after; detail is dropped, so keep durable rules in CLAUDE.md."},
    {"name": "/context", "category": "Context",
     "use": "See exactly what's filling the context window.",
     "tradeoff": "Free, instant readout — tells you when it's time to /compact or /clear."},

    # --- Reasoning depth ---
    {"name": "ultrathink (word in your prompt)", "category": "Reasoning",
     "use": "Ask for deeper reasoning on a single turn — just put the word \"ultrathink\" in your prompt (Claude Code shows \"Deeper reasoning requested for this turn\").",
     "tradeoff": "Spends more thinking tokens + time on that one turn; zero setup and doesn't persist, so it's ideal for a one-off hard step."},
    {"name": "/effort <low|medium|high|xhigh|max>", "category": "Reasoning",
     "use": "Dial reasoning to the task and make it STICK: low for simple/high-volume work, xhigh/max for the hardest problems (high is the default).",
     "tradeoff": "Higher levels spend more thinking tokens + time for better answers; lower is faster and cheaper. Persists across sessions (max lasts only the current one) — the persistent counterpart to a one-off `ultrathink`."},
    {"name": "Plan mode (Shift+Tab)", "category": "Reasoning",
     "use": "Explore the code and agree on an approach BEFORE any edits.",
     "tradeoff": "Costs a planning round up front; saves the far larger cost of undoing wrong changes."},

    # --- Model selection ---
    {"name": "/model <sonnet|haiku|opus|opusplan>", "category": "Model",
     "use": "Match the model to the task — haiku for mechanical/cheap work, sonnet (default) for most coding, opus for deep reasoning, opusplan to plan on Opus then build on Sonnet.",
     "tradeoff": "Cheaper models save tokens+time at lower capability; premium models cost more but reason better. Switching itself is free."},

    # --- Delegation & parallelism ---
    {"name": "ultracode (word in your prompt)", "category": "Delegation",
     "use": "Trigger a dynamic multi-agent workflow for a turn — put the word \"ultracode\" in your prompt (Claude Code shows \"Dynamic workflow requested for this turn\", opt+w to ignore).",
     "tradeoff": "Spawns multiple agents — the highest token cost, but the most thorough and parallel path for big audits, migrations, or reviews."},
    {"name": "Subagents (\"use a subagent to …\")", "category": "Delegation",
     "use": "A verbose side task (research, log analysis, verification) that would flood your main thread with output you won't reuse.",
     "tradeoff": "Keeps your main context lean — only the summary returns; spends tokens on the subagent's own context in exchange."},
    {"name": "/goal <condition>", "category": "Delegation",
     "use": "A big task with a verifiable finish line — Claude keeps working until the condition is met, no re-prompting.",
     "tradeoff": "Spends tokens/time autonomously; saves you from babysitting and typing 'continue'."},
    {"name": "/loop [interval] <prompt>", "category": "Delegation",
     "use": "Run a prompt on a recurring interval or let Claude self-pace repeated passes.",
     "tradeoff": "Spends tokens each recurrence; saves the manual re-prompting for scheduled or set-and-forget work."},
    {"name": "Parallel agents (Agent view)", "category": "Delegation",
     "use": "Run many isolated tasks at once and monitor them from one screen.",
     "tradeoff": "Saves wall-clock time by parallelizing; spends tokens proportional to the number of agents (each is its own context)."},

    # --- Input methods ---
    {"name": "@file / @dir", "category": "Input",
     "use": "Point Claude straight at a file or directory instead of describing it or pasting it.",
     "tradeoff": "Spends context on exactly what you name — cheaper and more precise than letting Claude hunt for it."},
    {"name": "Vision (paste/drag an image)", "category": "Input",
     "use": "Implement UI from a mockup/screenshot, or debug a visual bug — drop the image right into the terminal.",
     "tradeoff": "Spends vision tokens on the image; saves the far longer effort of describing a layout in words."},

    # --- Output & artifacts ---
    {"name": "Artifacts (\"make an artifact …\")", "category": "Output",
     "use": "Output that's easier to see than to read — annotated diffs, dashboards, charts, comparison layouts you can open in the browser.",
     "tradeoff": "Spends more output tokens than plain terminal text (inline CSS/JS); worth it for visual clarity and one-URL sharing."},

    # --- History & recovery ---
    {"name": "/rewind (or Esc twice)", "category": "History",
     "use": "Undo a wrong turn — restore code, conversation, or both to an earlier checkpoint (one is saved at every prompt).",
     "tradeoff": "Free recovery; cheaper than re-explaining or hand-undoing changes. Note: bash and external file edits aren't checkpointed."},

    # --- Memory ---
    {"name": "CLAUDE.md", "category": "Memory",
     "use": "Store always-on project conventions, build commands, and constraints so you never re-explain them.",
     "tradeoff": "Loads every session (keep it tight, ~under 200 lines), but survives compaction — unlike instructions buried in the chat."},
    {"name": "/memory  (+ \"remember that …\")", "category": "Memory",
     "use": "View/edit persistent memory, or let Claude auto-save build commands and preferences across sessions.",
     "tradeoff": "Saves re-explaining patterns next time; small write cost now. Path-scoped rules load only for matching files."},

    # --- Sessions ---
    {"name": "claude --continue / --resume", "category": "Sessions",
     "use": "Pick up prior work in a directory — most recent (--continue) or from a picker (--resume).",
     "tradeoff": "Saves rebuilding context; history resumes intact."},
    {"name": "/branch  (--fork-session)", "category": "Sessions",
     "use": "Try a different approach without losing the original session.",
     "tradeoff": "Forks from a checkpoint into an independent branch; the original stays untouched."},
    {"name": "/export", "category": "Sessions",
     "use": "Save a readable transcript to share or archive.",
     "tradeoff": "No token cost; a human-readable file instead of copy-pasting."},

    # --- Diagnostics ---
    {"name": "/usage", "category": "Diagnostics",
     "use": "See what's actually consuming tokens — per session, skill, subagent, and MCP server.",
     "tradeoff": "Free; stops you guessing where the spend goes."},
    {"name": "/permissions", "category": "Diagnostics",
     "use": "Pre-approve trusted tools/commands so Claude stops asking each time.",
     "tradeoff": "Saves repeated approval prompts; no token cost."},
]

_CATEGORY_ORDER = ["Context", "Reasoning", "Model", "Delegation", "Input",
                   "Output", "History", "Memory", "Sessions", "Diagnostics"]


# --------------------------------------------------------------------------
# SITUATIONAL triggers — when a prompt matches, surface one relevant tip during
# coaching. Priority order top-to-bottom. `engage`: surface even on an already
# well-formed prompt (True) vs only ride along when the gate already coaches.
# --------------------------------------------------------------------------

_NEW_WORK = re.compile(
    r"\b(?:"
    r"new feature|add(?:ing)? (?:a |another )?new (?:\w+ ){0,3}?feature|"
    # NB: deliberately excludes component/page/screen — adding a page or
    # component to an EXISTING project is routine continuing work, not a
    # context switch worth a /clear. "new project/task/module/app/system"
    # are the ones that plausibly mean "I'm done with what I was doing".
    r"new (?:project|task|module|app|system|thing)|"
    r"let'?s (?:start|begin|kick off|move on)|now let'?s (?:start|build|add|create|make|implement)|"
    r"start(?:ing)? (?:a |on |building |fresh)|build me a (?:new|whole)|"
    r"moving on|move on to|onto the next|next feature|"
    # "switch to X" alone is too broad — it also matches tech/implementation
    # choices ("switch to postgres", "switch to dark mode"), which are part
    # of the SAME task, not a context switch. Only count it when the target
    # is itself a fresh unit of work.
    r"switch(?:ing)? to (?:a |the )?(?:different|other|new) (?:project|task|topic|thing)|"
    r"another (?:feature|thing|project|task)|"
    r"time to (?:build|start|tackle)|implement (?:a |the )?(?:new|whole)"
    r")\b",
    re.IGNORECASE,
)
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

# Every tip ends with a "→ do this" line carrying the EXACT command/keystroke to
# run — an execution path, not just "you could use X". The goal tip fills the
# finish condition from the user's own words so they can paste it verbatim.
_TIP_NEW_WORK = (
    "💡 Starting new/unrelated work? A bloated context wastes tokens and degrades output.\n"
    "→ run  /clear  first (or  /compact  to keep a summary), then send your prompt."
)
_TIP_PLAN = (
    "💡 Hard/architectural task? Give it deeper reasoning — right in your prompt.\n"
    "→ add the word  ultrathink  to your prompt for deeper reasoning this turn "
    "(or  /effort xhigh  to make it persist); for big changes, press Shift+Tab first to plan before editing."
)
_TIP_BROAD = (
    "💡 Broad or multi-part work runs faster fanned out — and keeps your main context clean.\n"
    "→ add this line to your prompt:  \"Use subagents to work on these in parallel.\""
)

# Pull the finish condition out of the user's own words so the /goal line is
# paste-ready, not a <placeholder>.
_GOAL_COND = re.compile(
    r"(?:keep (?:going|working)\s+)?(?:until|till)\s+(.+?)(?:[.;,]|$)",
    re.IGNORECASE,
)
_VAGUE_COND = {"everything", "done", "complete", "finished", "it", "it's done", "its done"}


def _goal_condition(prompt: str) -> str:
    """The concrete finish condition for a /goal line, derived from the prompt."""
    m = _GOAL_COND.search(prompt)
    if m:
        cond = m.group(1).strip().rstrip(".!?,;:").strip()
        if 3 <= len(cond) <= 60 and cond.lower() not in _VAGUE_COND:
            return cond
    low = prompt.lower()
    if "test" in low:
        return "all tests pass"
    if "end-to-end" in low or "end to end" in low:
        return "it works end-to-end"
    return "it's fully working and verified"


def _tip_big_goal(prompt: str) -> str:
    cond = _goal_condition(prompt)
    return (
        "💡 This has a clear finish line — let Claude self-verify instead of you re-checking.\n"
        f"→ run this first, then send your prompt:  /goal {cond}"
    )


def analyze(prompt: str, features: dict) -> dict | None:
    """Return the single most relevant Claude Code feature tip, or None.

    Each tip carries an execution path (the exact command/keystroke to use), so
    the user can act on it immediately rather than merely learn it exists.
    """
    prompt = prompt or ""
    # priority: new-work > big-goal > broad (→ subagents) > hard (→ ultrathink/plan).
    if _NEW_WORK.search(prompt):
        return {"tip": _TIP_NEW_WORK, "engage": True}
    if _BIG_GOAL.search(prompt):
        return {"tip": _tip_big_goal(prompt), "engage": True}
    if _BROAD.search(prompt):
        return {"tip": _TIP_BROAD, "engage": False}
    try:
        from .scorer import _HARD_TASK
        if _HARD_TASK.search(prompt):
            return {"tip": _TIP_PLAN, "engage": False}
    except Exception:
        pass
    return None


def catalog(category: str | None = None) -> str:
    """A browsable, grouped reference of Claude Code efficiency features with
    use-case and token/time trade-off."""
    want = category.strip().lower() if category else None
    cats: dict[str, list[dict]] = {}
    for f in FEATURES:
        cats.setdefault(f["category"], []).append(f)
    ordered = [c for c in _CATEGORY_ORDER if c in cats] + [c for c in cats if c not in _CATEGORY_ORDER]
    out = ["Claude Code efficiency features — pick the right tool for the task.", ""]
    shown = 0
    for cat in ordered:
        if want and want != cat.lower():
            continue
        out.append(f"── {cat} ──")
        for f in cats[cat]:
            out.append(f"  {f['name']}")
            out.append(f"      use:   {f['use']}")
            out.append(f"      cost:  {f['tradeoff']}")
            shown += 1
        out.append("")
    if want and shown == 0:
        return (f"No category '{category}'. Try one of: "
                + ", ".join(sorted({f['category'] for f in FEATURES})))
    return "\n".join(out).rstrip() + "\n"
