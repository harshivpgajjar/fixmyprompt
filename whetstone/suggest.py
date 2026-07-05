"""Deterministic, offline refinement scaffolds — the fallback for `whetstone
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
    if any("vague target" in g or "terse" in g for g in gaps):
        adds.append("Target: <which page/file/surface, and what's visibly wrong right now>")

    if not adds:
        return None

    lines = [prompt.strip(), ""]
    lines += [f"+ {a}" for a in adds]
    return "\n".join(lines)
