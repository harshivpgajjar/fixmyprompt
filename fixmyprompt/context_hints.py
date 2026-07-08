"""Context hints — acceptance-criteria memory + per-project tuning.

Coaching gets sharper when it knows (a) the done-criteria this user keeps
reaching for and (b) which project the prompt is being written in. Two small
on-disk stores under config.RUNTIME_DIR hold that, seeded from packaged
defaults on first use (idempotent — an existing valid store is never
overwritten):

  criteria.json — {"criteria": [str, ...]}  recurring acceptance criteria
  projects.json — {"<substring-or-abs-path>": "<clarifying hint>"}

Consumers:
  - refiner.py appends context_block(cwd) to the system-prompt context so
    rewrites reuse the user's own done-criteria and ask the right
    project-specific question.
  - the coach gate's local-scaffold path appends scaffold_extra(cwd) as one
    more "+ ..." line.

Everything here is fail-open (it runs inside the submit-time hook): a
missing/corrupt store reseeds, any unexpected error returns a safe default,
and nothing ever raises to the caller. Learned criteria are normalized
(whitespace collapsed, lowercased) and never include secret-looking clauses.
"""
from __future__ import annotations

import json
import os
import re

from . import config

# --- packaged defaults (seeded to disk on first use) -------------------------

DEFAULT_CRITERIA = [
    "no horizontal scroll at 390px",
    "tap/touch targets ≥44px",
    "console clean (no errors)",
    "nothing clipped or overflowing",
    "tests pass",
]

# key: matched case-insensitively as a substring of the hook's cwd (word-ish
# boundaries; "-"/"_" fold to spaces, so "Education-for-AI" matches
# "education for ai"). A key starting with "/" or "~" is an absolute path
# prefix instead. value: the clarifying question worth asking in that project.
#
# Ships empty — this is a per-user LEARNED store, not a preloaded list of
# someone else's projects. Populate it with:
#   fixmyprompt project add <name-or-path-substring> "<clarifying question>"
DEFAULT_PROJECTS: dict[str, str] = {}

_CRITERIA_CAP = 25       # learned list never grows past this; oldest drop first
_BLOCK_CRITERIA = 6      # how many criteria context_block() surfaces

# clause looks like an acceptance criterion if it contains one of these...
_SIGNALS = ("no ", "≥", ">=", "px", "clean", "pass", "done when", "should", "works when")
# ...or a number+unit.
_UNIT = re.compile(r"\d+\s*(?:px|ms|s|sec|%|kb|mb|gb|pt|em|rem|fps|chars?|words?)\b", re.IGNORECASE)


# bare secret words (any occurrence) — catches "the password should be …" which
# the structured scorelog._SECRET (needs key=value form) misses.
_SECRET_WORD = re.compile(
    r"\b(?:sk-|r8_|aiza|ghp_|xox|-----BEGIN|password|passwd|secret|token|"
    r"api[_-]?key|apikey|credential|private[_-]?key|bearer)\b",
    re.IGNORECASE,
)


def _has_secret(text: str) -> bool:
    """A criterion clause is secret-bearing if EITHER the codebase's structured
    detector (key=value / known prefixes) OR a bare secret word matches. Neither
    alone is a superset, so we OR them."""
    text = text or ""
    if _SECRET_WORD.search(text):
        return True
    try:
        from . import scorelog
        return bool(scorelog._SECRET.search(text))
    except Exception:
        return False


# --- stores (seed-on-first-use, fail-open) -----------------------------------

def _criteria_path():
    return config.RUNTIME_DIR / "criteria.json"


def _projects_path():
    return config.RUNTIME_DIR / "projects.json"


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path, obj) -> None:
    try:
        config.ensure_runtime_dir()
        # encoding=utf-8 is REQUIRED: DEFAULT_CRITERIA holds non-ASCII (e.g. "≥"),
        # and write_text defaults to cp1252 on Windows → UnicodeEncodeError →
        # the criteria/hints memory would silently never persist.
        config.secure_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    except Exception:
        pass  # read-only disk etc. — callers already hold the in-memory value


def _load_criteria() -> list[str]:
    """Current criteria list; seeds the store if missing/corrupt."""
    data = _read_json(_criteria_path())
    if isinstance(data, dict) and isinstance(data.get("criteria"), list):
        return [c.strip() for c in data["criteria"] if isinstance(c, str) and c.strip()]
    seed = list(DEFAULT_CRITERIA)
    _write_json(_criteria_path(), {"criteria": seed})
    return seed


def _load_projects() -> dict:
    """Current project→hint map; seeds the store if missing/corrupt."""
    data = _read_json(_projects_path())
    if isinstance(data, dict):
        return {
            k: v
            for k, v in data.items()
            if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        }
    seed = dict(DEFAULT_PROJECTS)
    _write_json(_projects_path(), seed)
    return seed


# --- matching -----------------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase, fold -/_ to spaces, collapse whitespace ("Education-for-AI"
    and "education for ai" normalize identically)."""
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", s.lower())).strip()


def _word_match(key_n: str, cwd_n: str) -> bool:
    """key_n appears in cwd_n at word-ish boundaries — so the "room" project
    matches ".../Room" but never ".../Bathroom-remodel"."""
    return re.search(r"(?<![a-z0-9])" + re.escape(key_n) + r"(?![a-z0-9])", cwd_n) is not None


def project_hint(cwd: str | None) -> str | None:
    """The clarifying hint for the project `cwd` is in, else None.

    Keys match as case-insensitive substrings of the cwd (boundary-aware after
    -/_ folding; multi-word keys also match compacted, so "swift money" catches
    a SwiftMoney dir). Keys starting with "/" or "~" match as absolute path
    prefixes. Most-specific (longest) key wins. Fail-safe: None on any error.
    """
    try:
        if not cwd or not isinstance(cwd, str):
            return None
        projects = _load_projects()
        cwd_l = cwd.lower().rstrip("/")
        cwd_n = _norm(cwd)
        cwd_c = cwd_n.replace(" ", "")  # compact — catches CamelCase dir names
        best = None  # (key length, hint)
        for key, hint in projects.items():
            k = key.strip()
            if k.startswith(("/", "~")):
                kp = os.path.expanduser(k).lower().rstrip("/")
                matched = bool(kp) and (cwd_l == kp or cwd_l.startswith(kp + "/"))
            else:
                kn = _norm(k)
                matched = bool(kn) and (
                    _word_match(kn, cwd_n)
                    or (" " in kn and kn.replace(" ", "") in cwd_c)
                )
            if matched and (best is None or len(k) > best[0]):
                best = (len(k), hint)
        return best[1] if best else None
    except Exception:
        return None


def list_project_hints() -> dict[str, str]:
    """The current project→hint map (seeded on first read). Never raises."""
    try:
        return dict(_load_projects())
    except Exception:
        return {}


def add_project_hint(key: str, hint: str) -> bool:
    """Add or update a project hint. Returns True on success (fail-open: False
    on bad input or a write error, never raises)."""
    key, hint = (key or "").strip(), (hint or "").strip()
    if not key or not hint:
        return False
    try:
        projects = _load_projects()
        projects[key] = hint
        _write_json(_projects_path(), projects)
        return True
    except Exception:
        return False


def remove_project_hint(key: str) -> bool:
    """Remove a project hint by its exact key. Returns True if it existed."""
    key = (key or "").strip()
    if not key:
        return False
    try:
        projects = _load_projects()
        if key not in projects:
            return False
        del projects[key]
        _write_json(_projects_path(), projects)
        return True
    except Exception:
        return False


# --- criteria memory ------------------------------------------------------------

def known_criteria() -> list[str]:
    """The current criteria list (seeded on first read). Never raises."""
    try:
        return _load_criteria()
    except Exception:
        return list(DEFAULT_CRITERIA)


def _clauses(text: str) -> list[str]:
    """Split text on sentence/clause boundaries, whitespace-normalized."""
    out = []
    for part in re.split(r"[.;!?\n•]+", text):
        for clause in re.split(r",\s+|\s+and\s+", part):
            clause = re.sub(r"\s+", " ", clause).strip(" \t*+-–—:\"'")
            if clause:
                out.append(clause)
    return out


def learn_criteria(text: str) -> None:
    """Extract acceptance-criteria-like clauses from an accepted/refined prompt
    and remember the new ones. Heuristic: keep clauses carrying a criteria
    signal (see _SIGNALS / number+unit), normalize (lowercase, collapsed
    whitespace), dedup case-insensitively, cap at 25 dropping oldest. Skips
    anything secret-shaped. Never raises."""
    try:
        if not text or not isinstance(text, str):
            return
        current = _load_criteria()
        seen = {c.lower() for c in current}
        added = False
        for clause in _clauses(text):
            low = clause.lower()
            if len(low) > 100 or len(low.split()) < 2:
                continue  # not a plausible criterion
            if _has_secret(clause):
                continue  # never store secrets (canonical detector)
            if not (any(s in low for s in _SIGNALS) or _UNIT.search(low)):
                continue  # no criteria signal
            if low in seen:
                continue
            current.append(low)
            seen.add(low)
            added = True
        if added:
            if len(current) > _CRITERIA_CAP:
                current = current[-_CRITERIA_CAP:]
            _write_json(_criteria_path(), {"criteria": current})
    except Exception:
        pass  # memory must never disrupt the user's turn


# --- consumer-facing composition ------------------------------------------------

def context_block(cwd: str | None) -> str:
    """A short block for the refiner's system context: the project hint (when
    cwd matches a known project) plus the first ~6 known criteria. Criteria are
    user-level, so they are listed even when cwd is None/unknown. Returns ""
    only when there is nothing at all to add."""
    try:
        lines = []
        hint = project_hint(cwd)
        if hint:
            lines.append(f"Project context: {hint}")
        crits = known_criteria()[:_BLOCK_CRITERIA]
        if crits:
            lines.append(
                "This user's usual acceptance criteria (reuse when relevant): "
                + "; ".join(crits)
            )
        return "\n".join(lines)
    except Exception:
        return ""


def scaffold_extra(cwd: str | None) -> str | None:
    """One extra line for the local scaffold — the project's clarifying hint —
    or None when the cwd doesn't match a known project."""
    try:
        hint = project_hint(cwd)
        return f"+ Project: {hint}" if hint else None
    except Exception:
        return None
