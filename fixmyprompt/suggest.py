"""Deterministic, offline refinement scaffolds — the fallback for `fixmyprompt
refine` when no LLM backend is reachable (no ANTHROPIC_API_KEY and no `claude`).

These are pure, template-based nudges built from the classifier's gaps. They are
intentionally NOT used by the live Coach Gate (which stays silent without an LLM,
to protect precision) — only by the explicit on-demand CLI/skill, where the user
has asked for help and a scaffold beats "nothing to add".
"""
from __future__ import annotations


def template(prompt: str, features: dict) -> str | None:
    """Return a scaffolded version of the prompt, or None if there's nothing to add."""
    mode = features.get("mode")
    gaps = features.get("gaps", []) or []
    if mode != "execute" or not gaps:
        return None

    adds: list[str] = []
    if "no acceptance criteria" in gaps:
        adds.append(
            "Done means: <spell out the checkable end-state — e.g. no horizontal "
            "scroll at 390px, tap targets hit, console clean>"
        )
    if any("design ask" in g for g in gaps):
        adds.append(
            "Direction: <a reference ('like <site>') or constraints "
            "(palette hex + typeface + mood) — pick one>"
        )
    if any("vague target" in g for g in gaps):
        adds.append("Target: <which page/file/surface, and what's visibly wrong right now>")

    if not adds:
        return None

    lines = [prompt.strip(), ""]
    lines += [f"+ {a}" for a in adds]
    return "\n".join(lines)


def affirm(features: dict) -> str:
    """A short affirmation for a well-formed prompt (tutorial mode) — names what
    makes it good so the user reinforces the pattern, never fabricates a flaw."""
    good = []
    if features.get("mode") == "explore":
        return ("Good exploration ask — open-ended on purpose. Tip: add "
                "'as N distinct single screens' to keep it cheap and comparable.")
    if features.get("has_done_criteria"):
        good.append("a checkable done-state")
    if features.get("has_constraints"):
        good.append("clear constraints")
    if features.get("has_reference") and features.get("is_design"):
        good.append("a design reference")
    if not good:
        return "Clear, scoped instruction — nothing to add. Keep it this tight."
    return "Well-specified ✓ — " + ", ".join(good) + ". Keep doing exactly this."


def model_line(sug: dict) -> str:
    """One-line model/effort suggestion for the coaching banner (+ optional
    upgrade note on a second line)."""
    line = f"→ suggested: {sug['model']} · {sug['effort']}  ({sug['why']})"
    if sug.get("note"):
        line += f"\n   ↑ {sug['note']}"
    return line
