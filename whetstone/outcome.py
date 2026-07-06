"""Outcome tracking — does coaching a prompt actually reduce rework?

The scorelog records every prompt with its action ("pass" = sent as-is,
"coach"/"accept" = coaching engaged) and, since session tracking landed, a
session_id. Within a session, a prompt that missed is typically followed by
correction prompts ("no, I meant…", "actually…", "still broken"). If coaching
works, a COACHED substantive prompt should be followed by FEWER corrections
than an UNCOACHED one.

- is_correction(rec)  — pure, precision-first correction detector
- analyze(records)    — pure, tallies correction-rate for coached vs uncoached
- summary(days=30)    — short markdown/terminal block for `whetstone progress`
                        and the weekly self-audit

Correction detection is deliberately conservative: false positives poison the
metric, so phrase markers are anchored or require a distinctive complement.
"""
from __future__ import annotations

import re

from . import scorelog

# A correction only "counts against" a trigger prompt if it arrives within the
# next FOLLOW_K prompts AND within WINDOW_SEC of the trigger.
FOLLOW_K = 3
WINDOW_SEC = 15 * 60
# A short non-continuation prompt fired within SOON_SEC of the previous prompt
# is treated as a rapid re-try (the caller supplies the timing verdict).
SOON_SEC = 120
SHORT_WORDS = 8
# A trigger must be substantive: not a continuation, not itself a correction,
# and at least this many words.
MIN_TRIGGER_WORDS = 4
# summary() refuses to compare rates below this many triggers per arm.
MIN_N = 5

_REDACTED = "[redacted: possible secret]"

# Markers that only signal a correction when the prompt STARTS with them —
# mid-sentence they're far too common ("make sure it actually works",
# "use flexbox instead of floats", "add an undo button").
_LEAD = re.compile(
    r"^(?:no,|no\s+i\b|nope\b|actually\b|wrong\b|undo\b|revert\b|instead\b|"
    r"still\s|broke\b|that'?s not\b|not what\b|not like that\b|i meant\b|"
    r"i said\b|try again\b)"
)

# Markers distinctive enough to signal a correction anywhere in the prompt.
# The vaguer ones require a complement ("that's not" alone would match
# "make sure that's not a problem").
_ANY = re.compile(
    r"\b(?:i meant|i said|try again|not like that|"
    r"not what (?:i|we)\b|that'?s not (?:what|it|right|how)\b|"
    r"(?:doesn'?t|didn'?t|does not|did not) work|"
    r"(?:you|it|that|this) broke)\b"
)


def is_correction(rec: dict, soon_after_prev: bool = False) -> bool:
    """True when the record looks like a rework/correction follow-up.

    Pure and per-record. Two signals:
    1. The preview starts with (or distinctively contains) a correction marker.
    2. It's a short (<8 word) non-continuation prompt fired very soon after
       the previous prompt — a rapid re-try. is_correction can't see timing,
       so the caller does the "very soon" check and passes `soon_after_prev`.
    """
    if not isinstance(rec, dict):
        return False
    text = (rec.get("preview") or "").strip().lower().replace("’", "'")
    if text and text != _REDACTED:
        if _LEAD.match(text) or _ANY.search(text):
            return True
    if (
        soon_after_prev
        and not rec.get("is_continuation")
        and 0 < (rec.get("word_count") or 0) < SHORT_WORDS
    ):
        return True
    return False


def analyze(records: list[dict] | None) -> dict:
    """Correction-rate for coached vs uncoached trigger prompts, per session.

    Pure. Groups by session_id (records without one are pre-tracking backfill
    and are skipped). Within each session, every substantive trigger (not a
    continuation, not itself a correction, word_count >= 4) is classified as
    COACHED (action in {"coach","accept"}) or UNCOACHED (action == "pass");
    other actions (e.g. "edit") are ambiguous and skipped. The trigger counts
    as "corrected" when a correction appears within the next FOLLOW_K prompts
    AND within WINDOW_SEC. Rates are None when the arm has no triggers.
    """
    sessions: dict[str, list[dict]] = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        sid = rec.get("session_id")
        if not sid:
            continue
        sessions.setdefault(sid, []).append(rec)

    coached_n = coached_c = uncoached_n = uncoached_c = 0
    for recs in sessions.values():
        # Flag corrections first (records are in real submit order).
        flags = []
        prev_ts = None
        for rec in recs:
            ts = rec.get("ts")
            soon = (
                prev_ts is not None
                and isinstance(ts, (int, float))
                and 0 <= ts - prev_ts <= SOON_SEC
            )
            flags.append(is_correction(rec, soon_after_prev=soon))
            if isinstance(ts, (int, float)):
                prev_ts = ts

        for i, rec in enumerate(recs):
            if flags[i] or rec.get("is_continuation"):
                continue
            if (rec.get("word_count") or 0) < MIN_TRIGGER_WORDS:
                continue
            action = rec.get("action")
            if action in ("coach", "accept"):
                coached = True
            elif action == "pass":
                coached = False
            else:
                continue
            t0 = rec.get("ts") or 0
            corrected = False
            for j in range(i + 1, min(i + 1 + FOLLOW_K, len(recs))):
                if not flags[j]:
                    continue
                dt = (recs[j].get("ts") or 0) - t0
                if 0 <= dt <= WINDOW_SEC:
                    corrected = True
                    break
            if coached:
                coached_n += 1
                coached_c += 1 if corrected else 0
            else:
                uncoached_n += 1
                uncoached_c += 1 if corrected else 0

    return {
        "coached_n": coached_n,
        "coached_corrected": coached_c,
        "coached_rate": (coached_c / coached_n) if coached_n else None,
        "uncoached_n": uncoached_n,
        "uncoached_corrected": uncoached_c,
        "uncoached_rate": (uncoached_c / uncoached_n) if uncoached_n else None,
        "sessions_analyzed": len(sessions),
    }


def summary(days: int = 30) -> str:
    """Short markdown/terminal block: did coaching reduce follow-up rework?

    Honest by construction — refuses to compare rates until both arms have at
    least MIN_N triggers, and flags small samples as directional. Never raises.
    """
    try:
        stats = analyze(scorelog.read(days=days))
    except Exception:
        stats = analyze([])
    cn, cc = stats["coached_n"], stats["coached_corrected"]
    un, uc = stats["uncoached_n"], stats["uncoached_corrected"]
    cr, ur = stats["coached_rate"], stats["uncoached_rate"]

    lines = ["## Outcomes (Whetstone)", ""]
    if cn < MIN_N or un < MIN_N:
        lines.append(
            "Not enough tracked sessions yet — outcome tracking needs live "
            "coached + uncoached prompts with session ids; keep using it."
        )
        lines.append(
            f"So far: {cn} coached / {un} uncoached trigger prompts across "
            f"{stats['sessions_analyzed']} session(s) in the last {days} days."
        )
        return "\n".join(lines) + "\n"

    if ur > 0 and (ur - cr) >= 0.10:
        cut = (1 - cr / ur) * 100
        if 40 <= cut <= 60:
            verdict = " — coaching roughly halved rework"
        else:
            verdict = f" — coaching cut rework by ~{cut:.0f}%"
    elif (cr - ur) >= 0.10:
        verdict = (
            " — coached prompts saw MORE rework; likely selection bias "
            "(weak prompts are the ones that get coached), watch the trend"
        )
    else:
        verdict = " — no clear difference yet"

    lines.append(
        f"Coached prompts were followed by a correction {cr * 100:.0f}% of the "
        f"time vs {ur * 100:.0f}% uncoached{verdict} "
        f"(coached N={cn}, uncoached N={un})."
    )
    lines.append(
        f"  coached:   {cc}/{cn} followed by a correction "
        f"(within {FOLLOW_K} prompts / {WINDOW_SEC // 60} min)"
    )
    lines.append(f"  uncoached: {uc}/{un}")
    lines.append(f"  ({stats['sessions_analyzed']} sessions, last {days} days)")
    if cn < 20 or un < 20:
        lines.append("  Small sample — read as directional, not proof.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys

    print(summary(int(sys.argv[1]) if len(sys.argv) > 1 else 30))
