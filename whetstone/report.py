"""Weekly prompting-quality report — the teaching-made-measurable feature.

summarize(days) returns a markdown section the Saturday self-audit embeds, and
`whetstone report` prints. It answers: are the user's substantive prompts getting
more self-sufficient over time, and what are the recurring gaps?
"""
from __future__ import annotations

from collections import Counter

from . import scorelog


def _substantive(records: list[dict]) -> list[dict]:
    # ignore continuations/commands — they aren't "prompts" for coaching purposes
    return [
        r
        for r in records
        if not r.get("is_continuation") and (r.get("word_count") or 0) >= 8
    ]


def _self_sufficient(r: dict) -> bool:
    """A substantive prompt that wouldn't have benefited from coaching."""
    if r.get("mode") == "explore":
        return True  # intentional exploration is self-sufficient by definition
    return bool(r.get("has_done_criteria")) and (
        bool(r.get("has_constraints")) or not r.get("gaps")
    )


def _rate(records: list[dict]) -> float | None:
    subs = _substantive(records)
    execs = [r for r in subs if r.get("mode") != "other"]
    if not execs:
        return None
    good = sum(1 for r in execs if _self_sufficient(r))
    return good / len(execs)


def summarize(days: int = 7) -> str:
    recent = scorelog.read(days=days)
    subs = _substantive(recent)
    if not subs:
        return (
            "## Prompting (Whetstone)\n"
            f"No substantive prompts logged in the last {days} days.\n"
        )

    this_rate = _rate(recent)
    prev = [r for r in scorelog.read(days=days * 2) if r not in recent]
    prev_rate = _rate(prev)

    total = len(subs)
    explore = sum(1 for r in subs if r.get("mode") == "explore")
    execute = sum(1 for r in subs if r.get("mode") == "execute")
    coached = sum(1 for r in recent if r.get("action") == "coach")
    accepted = sum(1 for r in recent if r.get("action") == "accept")

    gap_counter: Counter[str] = Counter()
    for r in subs:
        for g in r.get("gaps", []) or []:
            gap_counter[g] += 1

    lines = ["## Prompting (Whetstone)", ""]
    if this_rate is not None:
        arrow = ""
        if prev_rate is not None:
            delta = this_rate - prev_rate
            arrow = f" ({'+' if delta >= 0 else ''}{delta * 100:.0f} pts vs prior period)"
        lines.append(
            f"- **Execute-mode self-sufficiency: {this_rate * 100:.0f}%**{arrow} "
            f"— prompts that didn't need coaching."
        )
    lines.append(
        f"- {total} substantive prompts ({execute} execute, {explore} explore); "
        f"coach fired {coached}×, you accepted the refined version {accepted}×."
    )
    if gap_counter:
        top = ", ".join(f"{g} ({n})" for g, n in gap_counter.most_common(3))
        lines.append(f"- **Top recurring gaps:** {top}.")
        top_gap = gap_counter.most_common(1)[0][0]
        lines.append(f"- **This week's habit:** front-load *{top_gap}* on execute-mode asks.")
    else:
        lines.append("- No recurring gaps — execute prompts were well-formed. Nice.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(summarize(days))
