"""Prompt classifier — the local, deterministic, zero-latency brain of the gate.

BASELINE implementation (correct interface, reasonable heuristics). The scorer
is one of the two "hard" components; a Fable 5 subagent hardens the heuristics
and adds the exhaustive test suite against the frozen interface below.

Pure functions, no I/O, no network. classify() must run in well under a
millisecond so it can sit in front of every keystroke-submitted prompt.
"""
from __future__ import annotations

import re

# --- lexicons -------------------------------------------------------------

_CONTINUATION = re.compile(
    r"^(y|ye|yes|yep|yeah|yup|ok|okay|k|sure|go|go on|continue|proceed|do it|"
    r"send it|ship it|next|more|again|retry|try again|run it|run|do that|"
    r"perfect|great|thanks|thank you|ty|nice|cool|no|nope|stop|wait|n)\b[\s.!]*$",
    re.IGNORECASE,
)

_EXPLORE = re.compile(
    r"\b(blow me away|surprise me|go wild|go bold|be creative|get creative|"
    r"something (new|unique|different|the world)|dont? know what i want|"
    r"not sure what|give me (some )?(options|ideas|variations|variants|directions|"
    r"concepts|inspiration)|explore|brainstorm|riff|play with|wow me|"
    r"impress me|show me (some )?options)\b",
    re.IGNORECASE,
)

_EXECUTE = re.compile(
    r"\b(build|create|make|add|implement|fix|change|update|refactor|remove|"
    r"delete|rename|migrate|write|set up|setup|wire|integrate|deploy|debug|"
    r"connect|convert|generate|configure|install|replace)\b",
    re.IGNORECASE,
)

_DESIGN = re.compile(
    r"\b(design|ui|ux|layout|hero|landing|page|website|site|logo|brand|"
    r"color|colour|palette|font|typeface|typography|theme|aesthetic|"
    r"visual|style|mockup|preloader|animation|section|look|vibe)\b",
    re.IGNORECASE,
)

_REFERENCE = re.compile(
    r"(https?://|\blike\b.*\b(this|that|these|the ones?)\b|similar to|"
    r"inspired by|reference|in the style of|based on|à la|a la|"
    r"like (my|the) [a-z]+|\.(com|io|app|design|studio)\b)",
    re.IGNORECASE,
)

_DONE = re.compile(
    r"(done means|acceptance criteria|definition of done|so that|it should\b|"
    r"must\b|needs? to\b|the (goal|result) is|success is|when (it|this)\b|"
    r"expect(ed)?\b|verify|test(s|ing|ed)?\b|passes?\b|works? when)",
    re.IGNORECASE,
)

_CONSTRAINT = re.compile(
    r"(only|don'?t|do not|never|without|must not|avoid|keep|use\b|"
    r"instead of|not the|except|limit|max\b|min\b|within|no more than|"
    r"mobile[- ]first|in [a-z0-9./]+\.(ts|tsx|js|py|kt|swift|css|html|go|rs))",
    re.IGNORECASE,
)

_NUMBERED = re.compile(r"(^|\n)\s*(\d+[.)]|[-*])\s+\S", re.MULTILINE)

_CODE_FENCE = re.compile(r"```")
_LOGLINE = re.compile(r"^\s*(\S+/|\[|\d{2,4}[-:]|at\s|File\s|\w+Error|Traceback|"
                      r"\s{2,}at\b|npm\s|error:|warning:|\+|\-\-)", re.IGNORECASE)


def _looks_like_paste(prompt: str) -> bool:
    if _CODE_FENCE.search(prompt):
        return True
    lines = [ln for ln in prompt.splitlines() if ln.strip()]
    if len(lines) >= 4:
        hits = sum(1 for ln in lines if _LOGLINE.match(ln))
        if hits / len(lines) >= 0.5:
            return True
    return False


def classify(prompt: str) -> dict:
    prompt = prompt or ""
    stripped = prompt.strip()
    words = re.findall(r"\S+", stripped)
    wc = len(words)

    is_command = bool(stripped[:1] in ("/", "!", "#"))
    is_continuation = bool(_CONTINUATION.match(stripped)) or (wc <= 2 and not is_command)
    looks_paste = _looks_like_paste(prompt)

    explore = bool(_EXPLORE.search(prompt))
    execute = bool(_EXECUTE.search(prompt))
    is_design = bool(_DESIGN.search(prompt))

    if explore:
        mode = "explore"
    elif execute:
        mode = "execute"
    else:
        mode = "other"

    has_reference = bool(_REFERENCE.search(prompt))
    has_done = bool(_DONE.search(prompt)) or bool(_NUMBERED.search(prompt))
    has_constraints = bool(_CONSTRAINT.search(prompt)) or bool(_NUMBERED.search(prompt))

    gaps: list[str] = []
    if mode == "execute":
        if not has_done:
            gaps.append("no acceptance criteria")
        if is_design and not has_reference and not has_constraints:
            gaps.append("design ask with no reference or constraints")
        if wc < 8:
            gaps.append("very terse for a build request")

    quality = _quality(mode, wc, has_done, has_constraints, has_reference, is_design, gaps)

    return {
        "word_count": wc,
        "is_command": is_command,
        "is_continuation": is_continuation,
        "looks_like_paste": looks_paste,
        "is_design": is_design,
        "mode": mode,
        "has_constraints": has_constraints,
        "has_done_criteria": has_done,
        "has_reference": has_reference,
        "gaps": gaps,
        "quality": quality,
    }


def _quality(mode, wc, has_done, has_constraints, has_reference, is_design, gaps) -> float:
    if mode == "explore":
        return 1.0  # intentional exploration is not "low quality"
    if mode == "other":
        return 0.8
    score = 0.4
    if has_done:
        score += 0.3
    if has_constraints:
        score += 0.2
    if is_design and has_reference:
        score += 0.1
    if wc >= 12:
        score += 0.1
    score -= 0.1 * len(gaps)
    return max(0.0, min(1.0, score))


def should_coach(features: dict, cfg: dict) -> bool:
    """The silence gate. Pure decision — no I/O. State checks (cooldown, pending,
    sigil) are applied by the hook around this. Returns True only when there is
    plausibly something worth offering."""
    mode = cfg.get("mode", "off")
    if mode == "off":
        return False
    if features.get("is_command"):
        return False
    if features.get("is_continuation"):
        return False
    if features.get("looks_like_paste"):
        return False
    if (features.get("word_count") or 0) < cfg.get("min_words", 12):
        return False
    # never coach intentional exploration or non-tasks
    if features.get("mode") != "execute":
        return False
    if features.get("quality", 1.0) >= cfg.get("coach_below_quality", 0.7):
        return False
    return True
