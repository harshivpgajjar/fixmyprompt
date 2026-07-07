"""Prompt classifier — the local, deterministic, zero-latency brain of the gate.

Hardened heuristics tuned to the primary user's real prompting style:
voice-dictated, typo-heavy, Hinglish-mixed, terse. Design goals, in order:

1. PRECISION over recall — a wrongly-fired coach destroys trust, a miss costs
   nothing. Every ambiguous signal resolves toward silence.
2. Mode awareness — intentional exploration ("blow me away", "give me 7
   options") is a *good* prompt, never a coaching target. Refinements of
   existing work ("perfect it, it has some issues") are continuations of a
   conversation, not fresh under-specified asks.
3. Speed — pure functions, precompiled regexes, no I/O, no imports beyond
   `re`. classify() runs in well under a millisecond.

Frozen interface (other modules depend on it — see fixmyprompt/__init__.py):
    classify(prompt: str) -> dict  with exactly the FEATURE_KEYS keys
    should_coach(features: dict, cfg: dict) -> bool
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Continuations — acknowledgements / "keep going" / micro-directives at work
# already in flight. Never coach these, in any language, with any typo.
# --------------------------------------------------------------------------

# Tokens that, in a <=4-word prompt, read as continuation/confirmation filler.
_CONT_TOKENS = frozenset(
    """
    y ye yes yep yeah yup ya yess yesss ok okay okey oke k kk kay sure fine
    go ahead on continue proceed resume next more again retry redo
    do it that this them then run send ship try keep going carry finish
    perfect great nice cool awesome amazing love thanks thank you ty thx
    no nope nah stop wait hold up n hmm hm ah oh done good sounds looks lgtm
    pls please plz now for the and same
    haan han ha nahi karo kar chalo theek thik hai bhai yaar acha accha
    badhiya sahi bas ruko krdo karde
    """.split()
)

# Typo-prone continuation words worth fuzzy-matching (voice dictation / fast
# typing produces contnie / cotnine / contionue and friends).
_FUZZY_CONT = ("continue", "proceed", "perfect", "okay", "please", "thanks", "again")

_TOKEN_CLEAN = re.compile(r"[^\w']+")


def _damerau(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (transposition counts as one edit).
    Only ever called on short tokens from <=4-word prompts, so O(len^2) is fine."""
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return la or lb
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[lb]


def _cont_token(tok: str) -> bool:
    if tok in _CONT_TOKENS:
        return True
    if len(tok) >= 5:  # fuzzy only for words long enough to carry a typo
        for target in _FUZZY_CONT:
            if abs(len(tok) - len(target)) <= 2 and _damerau(tok, target) <= 2:
                return True
    return False


def _is_continuation(words: list[str], is_command: bool) -> bool:
    if is_command or not words:
        return False
    if len(words) <= 2:
        # "yes", "go", "run it", "fix it", "contnie", a lone emoji — a fresh
        # task is never two words; treat as continuation-class, never coach.
        return True
    if len(words) <= 4:
        toks = [_TOKEN_CLEAN.sub("", w).lower() for w in words]
        toks = [t for t in toks if t]
        return bool(toks) and all(_cont_token(t) for t in toks)
    return False


# --------------------------------------------------------------------------
# Mode detection
# --------------------------------------------------------------------------

# Strong, explicit invitations to explore. These win over everything: a prompt
# that grants creative freedom is intentional discovery, not an under-specified
# task, even when it contains an execute verb ("make me 5 logo concepts").
_EXPLORE = re.compile(
    r"\b(?:"
    r"blow (?:me|us) away|surprise me|wow me|impress me|amaze me|"
    r"go (?:wild|wil|bold|big|crazy|nuts|all out)|get creative|be creative|be bold|"
    r"have fun|freestyle|anything you want|up to you|you decide|your call|"
    r"dealer'?s choice|full freedom|no wrong answers|"
    r"something (?:new|fresh|unique|different|nobody|no one|the world)|"
    r"(?:the )?world has (?:not|never|n'?t) seen|never been (?:done|seen)|"
    r"not sure what i want|don'?t know what i want|open to (?:anything|ideas)|"
    r"brainstorm|moodboard|mood board|spitball|riff on|"
    r"kuch (?:naya|alag|hatke|creative)|"
    # "give/show me ... options|ideas|..." with a short gap ("give me atleast 7
    # options"). Plural nouns only — "give the user the option to X" is a task.
    r"(?:give|show|send|get|make|draft|mock) (?:me|us) [^.\n]{0,40}?"
    r"(?:options|ideas|concepts|directions|variations|variants|alternatives|explorations|proposals)\b|"
    # "7 options", "a few directions", "a couple of looks" — but not when they
    # feed into a surface ("add 3 options to the dropdown" is a feature).
    r"(?:a few|a couple(?: of)?|several|multiple|at ?least \d+|\d+)\s+(?:different )?"
    r"(?:options|ideas|concepts|directions|variations|variants|versions|looks|styles|approaches)\b"
    r"(?!\s+(?:to|in|into|inside|on)\b)|"
    r"what are (?:some|a few|the) (?:options|ideas|ways|approaches)|"
    r"how (?:should|could|might) (?:we|i)\b"
    r")",
    re.IGNORECASE,
)

# Opinion / vision musings ("people these days love actualy good design...
# - according to me"). Weak explore signal: only counts when there is no
# execute verb and no refinement-of-existing anaphora.
_OPINION = re.compile(
    r"(?:"
    r"according to me|in my opinion|\bimo\b|"
    r"\bi (?:really |actually |personally )?(?:love|like|hate|prefer|believe|think|feel)\b|"
    r"people (?:these days )?(?:love|want|hate|crave|are hunting|are looking)|"
    r"that(?:'?s| is) what people (?:want|love|are hunting)"
    r")",
    re.IGNORECASE,
)

# Refinement of existing work — "i love tide, perfect it. it has some issues".
# This is a continuation of a project in flight, not a fresh explore or a fresh
# execute ask. Classified as mode "other"; the word gate keeps it silent.
_REFINEMENT = re.compile(
    r"(?:"
    r"\b(?:perfect|polish|refine|tweak|smooth out|tighten(?: up)?|iterate on) (?:it|this|that|them)\b|"
    r"\b(?:it|this|that) (?:still )?(?:has|got) (?:a few |some |few |many |lots of )?"
    r"(?:issues?|problems?|bugs?|quirks?|rough edges)\b|"
    r"\balmost (?:there|perfect|done)\b|\bso close\b"
    r")",
    re.IGNORECASE,
)

# Information questions are discovery, not tasks — "how do i fix the deploy"
# should never be coached as an under-specified execute prompt. ("when" is
# deliberately absent: "when the user clicks, open the modal" is a task.)
_QUESTION = re.compile(
    r"^(?:what|what's|whats|how|how's|hows|why|where|which|who|"
    r"is there|are there|do we|does|can i|could i|should i|should we|would it)\b",
    re.IGNORECASE,
)

# Task verbs (English + the user's Hinglish), plus "X needs work" phrasings,
# which are fix-requests without an imperative verb.
_EXECUTE = re.compile(
    r"\b(?:"
    r"build|create|make|add|implement|fix|change|update|refactor|remove|delete|"
    r"rename|migrate|write|set up|setup|wire|integrate|deploy|debug|connect|"
    r"convert|generate|configure|install|replace|improve|enhance|redo|rework|"
    r"rebuild|redesign|resize|move|swap|reorder|restructure|extract|split|"
    r"merge|combine|optimi[sz]e|speed up|clean up|hook up|get rid of|hide|"
    r"align|center|centre|adjust|"
    r"karo|kar do|karde|krdo|banao|bana do|hatao|badlo|lagao|laga do|jodo|"
    r"thik kar|theek kar"
    r")\b"
    r"|\bneeds? (?:a lot of |some |more |lots of )?"
    r"(?:work|love|attention|polish|improvements?|fixing|cleanup|help)\b"
    r"|\bneeds? to be (?:fixed|redone|rebuilt|updated|changed|reworked|improved)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Feature lexicons
# --------------------------------------------------------------------------

_DESIGN = re.compile(
    r"\b(?:"
    r"design|redesign|ui|ux|layout|hero|landing|page|website|site|webpage|"
    r"homepage|portfolio|logo|brand(?:ing)?|color|colour|palette|font|typeface|"
    r"typography|theme|aesthetic|visual|styles?|styling|mockup|moodboard|"
    r"preloader|animation|section|looks|look and feel|the look|vibe|beautiful|"
    r"pretty|sleek|minimal(?:ist)?|nav(?:bar)?|header|footer|responsive"
    r")\b",
    re.IGNORECASE,
)

_REFERENCE = re.compile(
    r"(?:"
    # NB: the pre-dot run is bounded ([^\s.]{1,40}, no unbounded \S+) so a long
    # single-line paste (minified JS, base64, a JWT) can't trigger O(n²)
    # backtracking and stall the submit hook.
    r"https?://\S+|www\.\S+|[^\s.]{1,40}\.(?:com|io|app|dev|design|studio|net|org|co|ai)\b|"
    r"\blike (?:this|that|these|those|the \w+|my \w+|our \w+|your \w+)\b|"
    r"\bsimilar to\b|\bsame as\b|\binspired by\b|\bin the style of\b|\bstyle of\b|"
    r"\bbased on\b|\breference\b|\brefer to\b|\bà la\b|\ba la\b|\bmatch(?:ing)? the\b|"
    r"\bscreenshot\b|\battached\b|\bsee the (?:image|photo|picture|mock|mockup)\b|"
    r"\bthe way \w+ does\b|\bjaisa\b|\bjaise\b"
    r")",
    re.IGNORECASE,
)

# Success / expected-behavior criteria. "\bshould\b" is the workhorse for this
# user ("call should call that number"). Bare "needs to be better" is vague
# desire, not a criterion, and deliberately does NOT match.
_DONE = re.compile(
    r"(?:"
    r"done (?:means|when|if)|acceptance criteria|definition of done|"
    r"\bso that\b|\bshould(?:n'?t| not)?\b|\bmust(?: not)?\b|"
    r"\bthe (?:goal|result|outcome) is\b|success (?:is|means|looks like)|"
    r"\bworks? when\b|\bexpect(?:ed|ing)?\b|\bverify\b|\bmake sure\b|\bensure\b|"
    r"\bconfirm\b|\btests? (?:pass|passes|green)\b|\bpasses\b|\buntil (?:it|the)\b|"
    r"\bwhen (?:i|you|the user|a user|someone) (?:click|tap|press|open|scroll|type|hover|submit)|"
    r"\bhona chahiye\b|\bchahiye\b"
    r")",
    re.IGNORECASE,
)

# Constraints: scope limits, tech choices, placement, concrete values, files.
# "don't like/know/..." is a complaint, not a constraint — excluded.
_CONSTRAINT = re.compile(
    r"(?:"
    r"\bonly\b|\bdon'?t (?!like|love|know|care|think|want|worry)\w+|\bdo not\b|"
    r"\bnever\b|\bwithout\b|\bmust not\b|\bavoid\b|"
    r"\bkeep (?:the|it|this|that|them|all|everything|existing|current|same|my|our|your)\b|"
    r"\bleave (?:the|it)\b|\buse\b|\busing\b|\binstead of\b|\bexcept\b|\blimit\b|"
    r"\bmax\b|\bmin\b|\bwithin\b|\bno more than\b|\bat most\b|\bexactly\b|"
    r"\bmobile[- ]first\b|\bnot the\b|"
    r"\bnext to\b|\b(?:on|at|to) the (?:left|right|top|bottom)\b|"
    r"\babove the\b|\bbelow the\b|\bbetween the\b|"
    r"\b\d+(?:\.\d+)?(?:px|pt|em|rem|%|ms|s|sec|seconds?|minutes?|kb|mb)\b|"
    r"#[0-9a-fA-F]{3,8}\b|"
    r"\bmat (?:karo|kar|karna)\b|\bnahi chahiye\b|"
    r"\b[\w./~-]+\.(?:py|ts|tsx|js|jsx|css|html|json|md|kt|swift|go|rs|java|rb|php|sql|ya?ml|sh|txt|csv)\b"
    r")",
    re.IGNORECASE,
)

# Vague anaphora with no anchor — "make it better", "fix everything".
_VAGUE_TARGET = re.compile(
    r"\b(?:make|fix|improve|update|change) (?:it|this|that|things|stuff|everything)\b",
    re.IGNORECASE,
)

# Signals for model/effort routing (mirrors the user's own routing rules).
_HARD_TASK = re.compile(
    r"\b(?:architect(?:ure)?|system design|design (?:a|the) system|refactor|"
    r"migrat|debug|root[- ]cause|race condition|concurren|deadlock|memory leak|"
    r"performance|optimi[sz]e|algorithm|security|vulnerab|from scratch|"
    r"end[- ]to[- ]end|whole (?:app|system|codebase|thing)|"
    r"entire (?:app|system|codebase)|across (?:the )?(?:codebase|app|system)|"
    r"figure out|investigate|why (?:is|does|isn'?t|won'?t|doesn'?t)|"
    r"multi[- ]?step|distributed|scal(?:e|ing|ability)|thread|async|"
    r"hard problem|tricky|complex)\b",
    re.IGNORECASE,
)
_MECH_TASK = re.compile(
    r"\b(?:rename|typo|spelling|bump (?:the )?version|"
    r"update the (?:copy|text|version|readme|comment|label|wording)|"
    r"change the (?:text|copy|colou?r|string|label|wording|font size)|"
    r"add a (?:log|comment|console\.log|todo)|format|prettier|lint|reword|"
    r"rephrase|tweak the (?:text|copy|label|wording)|one[- ]?liner|"
    r"(?:small|quick|tiny|minor) (?:fix|change|tweak|edit))\b",
    re.IGNORECASE,
)

# Structured lists: newline-anchored (classic) or inline voice-dictated
# ("1. do x 2. do y 3. do z") — both imply the user decomposed the work.
_NUMBERED_LINES = re.compile(r"(?:^|\n)\s*(?:\d{1,2}[.)]|[-*•])\s+\S")
_NUMBERED_INLINE = re.compile(r"\b\d{1,2}[.)]\s+\S")

# --------------------------------------------------------------------------
# Paste detection — code blocks, stack traces, logs, diffs, JSON blobs.
# --------------------------------------------------------------------------

_CODE_FENCE = re.compile(r"```|~~~")
_JSON_BLOB = re.compile(r"^\s*[\[{]")
_LOG_LINE = re.compile(
    r"^\s*(?:"
    r"at\s+\S+\s*\(|at\s+\S+:\d+|File \"|Traceback|Caused by|"
    r"\S*(?:Error|Exception)\b[: ]|"
    r"\[\d|\[\w+\]|\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|"
    r"npm (?:ERR|WARN)|error(?:\[\w+\])?:|warning:|fatal:|panic:|"
    r"\+\+\+|---|@@ |diff --git|index [0-9a-f]+\.\.|"
    r">>> |\$ |"
    r"(?:def|class|function|fn|func)\s+\w+|import\s+[\w{\"'@.]|"
    r"from\s+[\w.\"'@/]+\s+import|(?:const|let|var)\s+\w+\s*=|"
    r"return[ ;(]|if\s*\(|for\s*\(|while\s*\(|"
    r"[{}]\s*$|.*[;{]\s*$|</?\w+|/\S+/"
    r")"
)


def _looks_like_paste(prompt: str) -> bool:
    if _CODE_FENCE.search(prompt):
        return True
    stripped = prompt.strip()
    if len(stripped) >= 120 and _JSON_BLOB.match(stripped) and stripped.endswith(("}", "]")):
        return True
    lines = [ln for ln in prompt.splitlines() if ln.strip()]
    if len(lines) >= 3:
        hits = sum(1 for ln in lines if _LOG_LINE.match(ln))
        if hits / len(lines) >= 0.5:
            return True
    return False


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------

def classify(prompt: str) -> dict:
    if not isinstance(prompt, str):
        prompt = ""
    stripped = prompt.strip()
    wc = len(re.findall(r"\S+", stripped))  # true word count, from the full text
    looks_paste = _looks_like_paste(prompt) if stripped else False

    # Bound what the lexicon regexes scan. Real prompts are far under this, and a
    # giant paste is gated to silence anyway — this is defense-in-depth so no
    # single regex can turn a huge paste into a submit-time stall (cf. _REFERENCE).
    if len(prompt) > 8000:
        prompt = prompt[:8000]
        stripped = prompt.strip()
    words = re.findall(r"\S+", stripped)

    is_command = stripped[:1] in ("/", "!", "#")
    is_continuation = (not looks_paste) and _is_continuation(words, is_command)

    is_design = bool(_DESIGN.search(prompt))
    has_reference = bool(_REFERENCE.search(prompt))

    numbered = bool(_NUMBERED_LINES.search(prompt)) or len(_NUMBERED_INLINE.findall(prompt)) >= 2
    has_done = bool(_DONE.search(prompt)) or numbered
    has_constraints = bool(_CONSTRAINT.search(prompt)) or numbered

    # --- mode (precedence: explore > execute > refinement > opinion > other)
    explore = bool(_EXPLORE.search(prompt))
    execute = bool(_EXECUTE.search(prompt))
    question = bool(_QUESTION.match(stripped))
    refinement = bool(_REFINEMENT.search(prompt))

    if explore:
        mode = "explore"
    elif execute and not question:
        mode = "execute"
    elif refinement:
        mode = "other"  # tightening existing work — a continuation of flow
    elif _OPINION.search(prompt) and not execute:
        mode = "explore"  # taste/vision musing — intentional discovery
    else:
        mode = "other"

    # --- gaps (execute mode only — coaching targets, human-readable)
    gaps: list[str] = []
    if mode == "execute":
        if not has_done:
            gaps.append("no acceptance criteria")
        if is_design and not (has_reference or has_constraints or has_done):
            gaps.append("design ask with no reference or constraints")
        if wc < 8:
            gaps.append("very terse for a build request")
        if _VAGUE_TARGET.search(prompt) and not has_done and not has_constraints:
            gaps.append("vague target (what exactly should change?)")

    quality = _quality(mode, wc, prompt, has_done, has_constraints, has_reference, gaps, is_design)

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


def _quality(mode, wc, prompt, has_done, has_constraints, has_reference, gaps, is_design) -> float:
    if mode == "explore":
        return 1.0  # intentional exploration is not "low quality"
    if mode == "other":
        return 0.8
    score = 0.4
    if has_done:
        score += 0.3
    if has_constraints:
        score += 0.2
    if has_reference:
        # on a design ask, "like <site>" IS the acceptance criterion
        score += 0.25 if is_design else 0.1
    if wc >= 12:
        score += 0.1
    elif wc >= 8:
        score += 0.05
    if wc >= 40:
        score += 0.05
    # multi-clause structure (commas / newlines / sentences) shows decomposition
    if prompt.count(",") + prompt.count(";") + prompt.count("\n") >= 2:
        score += 0.05
    # penalties for gaps not already priced in via the missing has_done bonus
    for gap in gaps:
        if not gap.startswith("no acceptance"):
            score -= 0.1
    return round(max(0.0, min(1.0, score)), 4)


# --------------------------------------------------------------------------
# should_coach — the silence gate
# --------------------------------------------------------------------------

def should_coach(features: dict, cfg: dict) -> bool:
    """The silence gate. Pure decision — no I/O. State checks (cooldown,
    pending, sigil) are applied by the hook around this. Returns True only
    when there is plausibly something worth offering."""
    if cfg.get("mode", "off") == "off":
        return False
    # These never get coached, even in tutorial mode — coaching them is noise.
    if features.get("is_command"):
        return False
    if features.get("is_continuation"):
        return False
    if features.get("looks_like_paste"):
        return False
    # Tutorial mode: coach EVERY real prompt regardless of length, mode, or
    # quality — the well-formed ones get an affirmation so the user learns what
    # "good" looks like, not just what's broken.
    if cfg.get("tutorial"):
        return True
    if (features.get("word_count") or 0) < cfg.get("min_words", 4):
        return False
    # never coach intentional exploration or non-tasks
    if features.get("mode") != "execute":
        return False
    if features.get("quality", 1.0) >= cfg.get("coach_below_quality", 0.7):
        return False
    return True


# --------------------------------------------------------------------------
# Model + effort suggestion — routes the prompt to the best-suited model/effort.
# Primary recommendations are models INCLUDED in the standard Claude subscription
# (Opus 4.8 for hard work, Sonnet 5 for features/design, Haiku 4.5 for
# mechanical). The very hardest tasks add an optional upgrade note for Fable 5,
# which is stronger still but requires separate Mythos access (NOT in the
# standard subscription) — so nobody is steered toward a model they can't run.
# --------------------------------------------------------------------------

# strongest subscription-included model, for the hardest work
_HARD_MODEL = "Opus 4.8"
# optional upgrade for the hardest tasks — not in the standard subscription
_FABLE_NOTE = "Fable 5 is stronger still if you have Mythos access (not in the standard subscription)"


def suggest_model_effort(features: dict, prompt: str) -> dict:
    """Return {"model", "effort", "why", "note"} — the best-suited model + effort
    for this prompt. `model` is always subscription-included; `note` is an
    optional upgrade hint (empty unless present). Pure, deterministic, instant."""
    prompt = prompt or ""
    mode = features.get("mode", "other")
    wc = features.get("word_count", 0) or 0
    gaps = features.get("gaps", []) or []
    is_design = features.get("is_design", False)
    hard = bool(_HARD_TASK.search(prompt))
    mech = bool(_MECH_TASK.search(prompt))

    if mech and not hard:
        return {"model": "Haiku 4.5", "effort": "low", "note": "",
                "why": "mechanical edit — cheap and fast is all it needs"}
    # design/ideation (incl. "blow me away") → fast-iterating model, not raw power
    if mode == "explore" or (is_design and mode == "execute"):
        return {"model": "Sonnet 5", "effort": "high", "note": "",
                "why": "design/iteration — fast turns matter more than raw power"}
    # hard/architectural, or a big under-specified build → top subscription model,
    # with the Fable upgrade noted for the hardest cases.
    if hard or (mode == "execute" and wc >= 45) \
            or (mode == "execute" and len(gaps) >= 3 and wc >= 30):
        return {"model": _HARD_MODEL, "effort": "xhigh", "note": _FABLE_NOTE,
                "why": "hard, ambiguous, or architectural — the top model earns it here"}
    if mode == "execute":
        return {"model": "Sonnet 5", "effort": "high", "note": "",
                "why": "standard feature work"}
    return {"model": "Sonnet 5", "effort": "medium", "note": "",
            "why": "straightforward — no need for the top tier"}
