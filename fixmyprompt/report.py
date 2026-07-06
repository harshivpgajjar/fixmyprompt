"""Weekly prompting-quality report — the teaching-made-measurable feature.

summarize(days) returns a markdown section the Saturday self-audit embeds, and
`fixmyprompt report` prints. It answers: are the user's substantive prompts getting
more self-sufficient over time, and what are the recurring gaps?

progress(period) is the richer interactive tracker behind `fixmyprompt progress`:
headline trend vs the previous period, a sparkline, streaks, volume, gap
trending, and the most-improved prompting axis.
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime, timedelta

from . import scorelog


def _substantive(records: list[dict]) -> list[dict]:
    # ignore continuations/commands — they aren't "prompts" for coaching purposes.
    # Threshold matches the gate's default min_words (4) so the very prompts that
    # trigger coaching (e.g. "fix the mobile version" = 4 words) aren't filtered
    # out of the metrics.
    return [
        r
        for r in records
        if not r.get("is_continuation") and (r.get("word_count") or 0) >= 4
    ]


def _self_sufficient(r: dict) -> bool:
    """A substantive prompt that wouldn't have benefited from coaching."""
    if r.get("mode") == "explore":
        return True  # intentional exploration is self-sufficient by definition
    return bool(r.get("has_done_criteria")) and (
        bool(r.get("has_constraints")) or not r.get("gaps")
    )


def _rate(records: list[dict]) -> float | None:
    # "Execute-mode self-sufficiency" — computed over EXECUTE prompts only, so
    # intentional explore prompts (which always score self-sufficient) can't
    # inflate the number or make it non-zero when there are no execute prompts.
    execs = [r for r in _substantive(records) if r.get("mode") == "execute"]
    if not execs:
        return None
    good = sum(1 for r in execs if _self_sufficient(r))
    return good / len(execs)


def summarize(days: int = 7) -> str:
    recent = scorelog.read(days=days)
    subs = _substantive(recent)
    if not subs:
        return (
            "## Prompting (FixMyPrompt)\n"
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

    lines = ["## Prompting (FixMyPrompt)", ""]
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


# --------------------------------------------------------------------------
# Progress tracker (`fixmyprompt progress [day|week|month]`)
# --------------------------------------------------------------------------

_BLOCKS = "▁▂▃▄▅▆▇█"
_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}
# sparkline shape per period: (bucket count, days per bucket)
_SPARK = {"day": (7, 1), "week": (14, 1), "month": (8, 7)}


def sparkline(values: list[float | None]) -> str:
    """Pure: map 0..1 floats to ▁▂▃▄▅▆▇█ block chars.

    None (a bucket with no data) renders as '·'; empty input -> "".
    Values are clamped into [0, 1].
    """
    chars = []
    for v in values:
        if v is None:
            chars.append("·")
            continue
        v = max(0.0, min(1.0, float(v)))
        chars.append(_BLOCKS[min(len(_BLOCKS) - 1, int(v * len(_BLOCKS)))])
    return "".join(chars)


def _bucket_rates(
    records: list[dict], buckets: int, bucket_days: int
) -> list[tuple[str, float | None]]:
    """Self-sufficiency rate per calendar bucket (local time), oldest first.

    The newest bucket ends today; each covers `bucket_days` calendar days.
    Returns (iso-date-of-bucket-start, rate or None if no data).
    """
    today = date.today()
    dated: list[tuple[date, dict]] = []
    for r in _substantive(records):
        if r.get("mode") == "other":
            continue
        dated.append((datetime.fromtimestamp(r.get("ts", 0)).date(), r))
    out: list[tuple[str, float | None]] = []
    for k in range(buckets):
        end = today - timedelta(days=(buckets - 1 - k) * bucket_days)
        start = end - timedelta(days=bucket_days - 1)
        recs = [r for d, r in dated if start <= d <= end]
        rate = (
            sum(1 for r in recs if _self_sufficient(r)) / len(recs) if recs else None
        )
        out.append((start.isoformat(), rate))
    return out


def daily_rates(
    records: list[dict], days: int
) -> list[tuple[str, float | None]]:
    """Pure: daily self-sufficiency rate for the last `days` local calendar
    days (oldest first, ending today). Days with no substantive
    execute/explore prompts get None."""
    return _bucket_rates(records, days, 1)


def streak_info(records: list[dict]) -> dict:
    """Pure: {current, best} runs of consecutive *active* days on which every
    substantive execute-mode prompt was self-sufficient (no coaching needed).

    A day with no execute prompts is neutral — it neither extends nor breaks a
    streak. `current` is the run ending at the most recent active day (0 if
    that day had a non-self-sufficient prompt)."""
    by_day: dict[date, bool] = {}
    for r in _substantive(records):
        if r.get("mode") != "execute":
            continue
        d = datetime.fromtimestamp(r.get("ts", 0)).date()
        by_day[d] = by_day.get(d, True) and _self_sufficient(r)
    current = best = 0
    for d in sorted(by_day):
        current = current + 1 if by_day[d] else 0
        best = max(best, current)
    return {"current": current, "best": best}


def _axis_rates(records: list[dict]) -> dict[str, float | None]:
    """Fraction of prompts hitting each coaching axis (None = no data)."""
    subs = _substantive(records)
    execs = [r for r in subs if r.get("mode") == "execute"]
    designs = [r for r in subs if r.get("is_design")]

    def frac(recs: list[dict], key: str) -> float | None:
        return sum(1 for r in recs if r.get(key)) / len(recs) if recs else None

    return {
        "done-criteria": frac(execs, "has_done_criteria"),
        "constraints": frac(execs, "has_constraints"),
        "reference-on-design": frac(designs, "has_reference"),
    }


def _short(iso_day: str) -> str:
    return datetime.strptime(iso_day, "%Y-%m-%d").strftime("%b %d")


def progress(period: str = "week") -> str:
    """Rich terminal progress report for the last day/week/month vs the one
    before it. Never raises on empty or sparse data."""
    if period not in _PERIOD_DAYS:
        raise ValueError(f"period must be one of {sorted(_PERIOD_DAYS)}, got {period!r}")
    pdays = _PERIOD_DAYS[period]
    buckets, bucket_days = _SPARK[period]
    lookback = max(2 * pdays, buckets * bucket_days)
    records = scorelog.read(days=lookback)

    now = time.time()
    cut_this = now - pdays * 86400
    cut_prev = now - 2 * pdays * 86400
    this = [r for r in records if r.get("ts", 0) >= cut_this]
    prev = [r for r in records if cut_prev <= r.get("ts", 0) < cut_this]

    end_d = date.today()
    start_d = end_d - timedelta(days=pdays - 1)
    span = end_d.strftime("%b %d") if pdays == 1 else (
        f"{start_d.strftime('%b %d')} – {end_d.strftime('%b %d')}"
    )
    header = f"FixMyPrompt — prompt progress ({period}: {span})"
    lines = [header, "─" * min(len(header), 60), ""]

    subs_this = _substantive(this)
    if not subs_this:
        lines.append(f"Not enough data yet, keep going — no substantive prompts this {period}.")
        return "\n".join(lines) + "\n"

    # Headline: execute-mode self-sufficiency vs previous period
    this_rate, prev_rate = _rate(this), _rate(prev)
    if this_rate is None:
        lines.append("Execute-mode self-sufficiency: n/a — no execute/explore prompts yet.")
    elif prev_rate is None:
        lines.append(
            f"Execute-mode self-sufficiency: {this_rate * 100:.0f}% "
            f"(no previous-{period} data to compare)"
        )
    else:
        delta = (this_rate - prev_rate) * 100
        arrow = "↑" if delta > 0.5 else ("↓" if delta < -0.5 else "→")
        lines.append(
            f"Execute-mode self-sufficiency: {this_rate * 100:.0f}% {arrow} "
            f"{delta:+.0f} pts vs previous {period} ({prev_rate * 100:.0f}%)"
        )
    lines.append("")

    # Sparkline trend
    rates = _bucket_rates(records, buckets, bucket_days)
    unit = "weekly" if bucket_days == 7 else "daily"
    unit_name = "weeks" if bucket_days == 7 else "days"
    lines.append(f"Trend ({unit} self-sufficiency, last {buckets} {unit_name}; · = no data)")
    lines.append(
        f"  {_short(rates[0][0])} {sparkline([v for _, v in rates])} "
        f"{end_d.strftime('%b %d')}"
    )
    lines.append("")

    # Streak
    st = streak_info(records)
    unit_s = "day" if st["current"] == 1 else "days"
    lines.append(
        f"Streak: {st['current']} {unit_s} with every execute prompt self-sufficient "
        f"(best in last {lookback} days: {st['best']})"
    )
    lines.append("")

    # Volume
    n_exec = sum(1 for r in subs_this if r.get("mode") == "execute")
    n_explore = sum(1 for r in subs_this if r.get("mode") == "explore")
    n_other = len(subs_this) - n_exec - n_explore
    coached = sum(1 for r in this if r.get("action") == "coach")
    accepted = sum(1 for r in this if r.get("action") == "accept")
    lines.append(f"Volume (this {period})")
    lines.append(
        f"  {len(subs_this)} substantive prompts — "
        f"{n_exec} execute / {n_explore} explore / {n_other} other"
    )
    lines.append(f"  coach fired {coached}× · refined rewrite accepted {accepted}×")
    lines.append("")

    # Top recurring gaps, with trend vs previous period
    gaps_this: Counter[str] = Counter(
        g for r in subs_this for g in (r.get("gaps") or [])
    )
    gaps_prev: Counter[str] = Counter(
        g for r in _substantive(prev) for g in (r.get("gaps") or [])
    )
    if gaps_this:
        lines.append(f"Top gaps (▼ down vs previous {period} / ▲ worse / — flat)")
        for g, n in gaps_this.most_common(4):
            was = gaps_prev.get(g, 0)
            mark = "▼" if n < was else ("▲" if n > was else "—")
            label = g if len(g) <= 38 else g[:37] + "…"
            lines.append(f"  {label:<38} {n:>3}  {mark} was {was}")
    else:
        lines.append(f"No gaps recorded this {period} — clean prompts.")
    lines.append("")

    # Most-improved axis
    ax_this, ax_prev = _axis_rates(this), _axis_rates(prev)
    deltas = {
        k: ax_this[k] - ax_prev[k]
        for k in ax_this
        if ax_this[k] is not None and ax_prev[k] is not None
    }
    if deltas:
        axis, d = max(deltas.items(), key=lambda kv: kv[1])
        if d > 0:
            lines.append(f"Most improved: {axis} ({d * 100:+.0f} pts vs previous {period})")
        else:
            lines.append(
                f"Most improved: none this {period} — "
                f"{axis} held closest ({d * 100:+.0f} pts)"
            )
    else:
        lines.append("Most improved: not enough data to compare axes yet.")

    # Prompt of the period — your sharpest prompt, as a template to repeat
    best = best_prompt(this)
    if best:
        lines.append("")
        lines.append(f"⭐ Prompt of the {period} (your sharpest — do more like this):")
        lines.append(f"   \"{best}\"")

    return "\n".join(lines) + "\n"


def best_prompt(records: list[dict]) -> str | None:
    """The user's sharpest substantive prompt in the set — highest quality with a
    real (non-redacted) preview, tie-broken by length. Returns the preview, or
    None if there's nothing worth surfacing."""
    def _real(p: str) -> bool:
        pl = p.lower()
        # skip redactions and meta/system prompts that got logged
        if "redacted" in pl:
            return False
        return not pl.startswith(("you are ", "return only", "system:", "your job",
                                  "you refine", "reply with only"))

    cands = [
        r for r in _substantive(records)
        if r.get("mode") in ("execute", "explore")
        and (r.get("quality") or 0) >= 0.75
        and r.get("preview") and _real(r.get("preview") or "")
    ]
    if not cands:
        return None
    best = max(cands, key=lambda r: ((r.get("quality") or 0), r.get("word_count") or 0))
    return best.get("preview")


if __name__ == "__main__":
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(summarize(days))
