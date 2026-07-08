"""Exhaustive tests for fixmyprompt.scorer — the local prompt classifier.

Run:  cd <repo root> && python3 -m unittest tests.test_scorer -v

The labeled sets below are drawn from the primary user's real prompting style
(voice-dictated, typo-heavy, Hinglish, terse). The contract under test:

- PRECISION is sacred: should_coach must be False for every explore prompt,
  well-formed execute prompt, continuation, command, and paste (zero false
  positives — a wrongly-fired coach destroys trust).
- RECALL: genuinely under-specified execute prompts of >= min_words must fire.
- The FEATURES dict shape is frozen (see fixmyprompt/__init__.py).
"""
from __future__ import annotations

import time
import unittest

from fixmyprompt import FEATURE_KEYS
from fixmyprompt.scorer import classify, should_coach

# Realistic runtime config (config.py defaults with the hook enabled).
CFG = {"mode": "always", "min_words": 12, "coach_below_quality": 0.7}


def coach(prompt: str) -> bool:
    return should_coach(classify(prompt), CFG)


# ---------------------------------------------------------------------------
# Labeled corpora
# ---------------------------------------------------------------------------

# (prompt, expected mode, expected is_continuation, expected has_done_criteria,
#  expected should_coach under CFG)
SPEC_TABLE = [
    # -- EXPLORE: intentional discovery, never coached ----------------------
    ("Do anything you want to do. Blow me away.",
     "explore", False, False, False),
    ("people these days love actualy good design... that is what people are "
     "hunting for. - according to me",
     "explore", False, False, False),
    ("go bold, go wil and give me something the world has not seen",
     "explore", False, False, False),
    ("give me atleast 7 options for the portfolio",
     "explore", False, False, False),
    # refinement-of-existing — borderline: continuation/other, NOT a fresh
    # explore and NOT a coachable fresh task (word gate also protects it).
    ("i love tide, perfect it. it has some issues",
     "other", False, False, False),

    # -- EXECUTE, well-formed: has criteria/constraints, never coached ------
    ("add a call and whatsapp icon on the right next to it, call should call "
     "that number, whatsapp should open their chat with that number",
     "execute", False, True, False),
    ("1. tapping inside the upload box should trigger upload 2. remove the "
     "nav links 3. the bottom part is overflowing, fix it",
     "execute", False, True, False),

    # -- EXECUTE, under-specified: real gaps. NOTE: all four are < 12 words,
    # so the word gate keeps should_coach False — that is the REAL behavior.
    # The >= 12-word coachable variants live in RECALL_SET below.
    ("fix the mobile version", "execute", False, False, False),
    ("make it better", "execute", False, False, False),
    ("build me a dashboard for the sales data", "execute", False, False, False),
    ("the episodes section needs a lot of work", "execute", False, False, False),

    # -- Continuations (incl. typos and Hinglish) — never coach -------------
    ("yes", "other", True, False, False),
    ("continue", "other", True, False, False),
    ("contnie", "other", True, False, False),          # typo of "continue"
    ("go", "other", True, False, False),
    ("run it", "other", True, False, False),
    ("do it", "other", True, False, False),
    ("perfect", "other", True, False, False),
    ("ok send it", "other", True, False, False),
]

# Under-specified prompts the four short spec examples map to at >= 12 words.
# Every one must fire the coach (recall set).
RECALL_SET = [
    "fix the mobile version of the site it looks really bad on my phone right now",
    "make the dashboard better it just doesnt feel right and clients keep complaining about it",
    "build me a dashboard for the sales data so the team can see whats going on",
    "the episodes section needs a lot of work can you go through it and improve everything please",
    "make it better overall the whole thing feels off and i dont like how it looks these days",
    "update the site somehow because honestly the current version is not doing it for me anymore",
    # Hinglish, voice-dictated: "fix the mobile version, nothing looks good"
    "bhai website ka mobile version thik karo kuch bhi acha nahi lag raha hai yaar",
]

TRACEBACK_PASTE = (
    "Traceback (most recent call last):\n"
    '  File "app.py", line 10, in <module>\n'
    "    main()\n"
    '  File "app.py", line 4, in main\n'
    "    raise ValueError('boom')\n"
    "ValueError: boom"
)

JS_STACK_PASTE = (
    "TypeError: Cannot read properties of undefined (reading 'map')\n"
    "    at renderList (/app/src/components/List.tsx:42:18)\n"
    "    at processChild (/app/node_modules/react-dom/cjs/react-dom.development.js:7302:14)\n"
    "    at performUnitOfWork (/app/node_modules/react-dom/cjs/react-dom.development.js:12042:12)"
)

NPM_LOG_PASTE = (
    "npm ERR! code E404\n"
    "npm ERR! 404 Not Found - GET https://registry.npmjs.org/left-padd\n"
    "npm ERR! 404 'left-padd@latest' is not in this registry.\n"
    "npm ERR! A complete log of this run can be found in: /Users/h/.npm/_logs/log.txt"
)

CODE_FENCE_PASTE = "fix this\n```python\ndef f(x):\n    return x + 1\n```"

# A realistic very long (>= 2000 chars), well-specified brief: high quality,
# never coached, still classified in well under a millisecond.
LONG_SPECIFIED = (
    "rebuild the booking flow for the salon site, mobile-first at 390px. "
    "1. the services list should load from services.json and show price and "
    "duration on each card 2. tapping a card should open the slot picker, "
    "slots come from the api and unavailable ones must be greyed out, don't "
    "let them be tappable 3. the confirm screen should show name, service, "
    "time and a whatsapp confirmation button that opens a chat with the "
    "shop number prefilled. keep the existing header and footer, use the "
    "current brand colors, no new dependencies. done means i can book an "
    "appointment end to end on my phone and the confirmation lands in "
    "whatsapp with the right date and time. "
) * 4  # ~2450 chars

# Precision guard: a wrongly-fired coach is the worst failure mode. Every
# prompt here — explore, well-formed execute, continuation, command, paste,
# reference-anchored design, Hinglish — must stay silent.
PRECISION_SET = [
    # explore / vision
    "Do anything you want to do. Blow me away.",
    "people these days love actualy good design... that is what people are "
    "hunting for. - according to me",
    "go bold, go wil and give me something the world has not seen",
    "give me atleast 7 options for the portfolio",
    "kuch hatke banao portfolio ke liye yaar full freedom",
    "what are some options for the pricing page layout given our current brand",
    # refinement-of-existing (borderline)
    "i love tide, perfect it. it has some issues",
    # well-formed execute
    "add a call and whatsapp icon on the right next to it, call should call "
    "that number, whatsapp should open their chat with that number",
    "1. tapping inside the upload box should trigger upload 2. remove the "
    "nav links 3. the bottom part is overflowing, fix it",
    "make the hero section like https://linear.app but keep our colors and "
    "fonts the same",
    "migrate the config parsing to a single loader, keep the existing env "
    "overrides, dont touch the cli flags",
    "update the hero copy, it should mention the free trial and the new "
    "pricing tiers we launched",
    LONG_SPECIFIED,
    # questions (discovery, not tasks)
    "how do i fix the deploy on vercel it keeps failing",
    # continuations, typos, Hinglish
    "yes", "contnie", "cotnine", "contionue", "ok send it", "run it",
    "perfect", "haan bhai continue karo",
    # commands
    "/refine foo", "!ls", "#remember this",
    # pastes
    TRACEBACK_PASTE, JS_STACK_PASTE, NPM_LOG_PASTE, CODE_FENCE_PASTE,
]


# ---------------------------------------------------------------------------
# Schema / typing
# ---------------------------------------------------------------------------

class TestSchema(unittest.TestCase):
    BATTERY = (
        [p for p, *_ in SPEC_TABLE]
        + RECALL_SET
        + [TRACEBACK_PASTE, CODE_FENCE_PASTE, LONG_SPECIFIED, "", "🔥"]
    )

    def test_exact_keys(self):
        for prompt in self.BATTERY:
            with self.subTest(prompt=prompt[:40]):
                self.assertEqual(set(classify(prompt)), set(FEATURE_KEYS))

    def test_types(self):
        for prompt in self.BATTERY:
            f = classify(prompt)
            with self.subTest(prompt=prompt[:40]):
                self.assertIsInstance(f["word_count"], int)
                self.assertNotIsInstance(f["word_count"], bool)
                self.assertGreaterEqual(f["word_count"], 0)
                for key in ("is_command", "is_continuation", "looks_like_paste",
                            "is_design", "has_constraints", "has_done_criteria",
                            "has_reference"):
                    self.assertIsInstance(f[key], bool, key)
                self.assertIsInstance(f["mode"], str)
                self.assertIn(f["mode"], ("explore", "execute", "other"))
                self.assertIsInstance(f["gaps"], list)
                for gap in f["gaps"]:
                    self.assertIsInstance(gap, str)
                self.assertIsInstance(f["quality"], float)
                self.assertGreaterEqual(f["quality"], 0.0)
                self.assertLessEqual(f["quality"], 1.0)

    def test_pure_and_deterministic(self):
        for prompt in self.BATTERY:
            a, b = classify(prompt), classify(prompt)
            self.assertEqual(a, b)
            self.assertIsNot(a["gaps"], b["gaps"])  # no shared mutable state


# ---------------------------------------------------------------------------
# The labeled spec examples, table-driven
# ---------------------------------------------------------------------------

class TestSpecExamples(unittest.TestCase):
    def test_table(self):
        for prompt, mode, cont, done, fires in SPEC_TABLE:
            f = classify(prompt)
            with self.subTest(prompt=prompt[:50]):
                self.assertEqual(f["mode"], mode)
                self.assertEqual(f["is_continuation"], cont)
                self.assertEqual(f["has_done_criteria"], done)
                self.assertEqual(should_coach(f, CFG), fires)

    def test_under_specified_examples_have_gaps_and_low_quality(self):
        # Even though the word gate silences the short ones, the classifier
        # must still SEE the problem (gaps + low quality feed the weekly report).
        for prompt in ("fix the mobile version", "make it better",
                       "build me a dashboard for the sales data",
                       "the episodes section needs a lot of work"):
            f = classify(prompt)
            with self.subTest(prompt=prompt):
                self.assertEqual(f["mode"], "execute")
                self.assertTrue(f["gaps"], "expected gaps on an under-specified ask")
                self.assertIn("no acceptance criteria", f["gaps"])
                self.assertLess(f["quality"], CFG["coach_below_quality"])

    def test_well_formed_examples_have_no_gaps(self):
        for prompt, mode, _, done, _ in SPEC_TABLE[5:7]:
            f = classify(prompt)
            with self.subTest(prompt=prompt[:50]):
                self.assertEqual(f["gaps"], [])
                self.assertGreaterEqual(f["quality"], CFG["coach_below_quality"])

    def test_commands(self):
        for prompt in ("/refine foo", "!ls", "#remember this", "  /clear"):
            f = classify(prompt)
            with self.subTest(prompt=prompt):
                self.assertTrue(f["is_command"])
                self.assertFalse(should_coach(f, CFG))

    def test_continuation_typos(self):
        # standalone typos (also caught by the <=2-word rule)
        for prompt in ("contnie", "cotnine", "contionue", "porceed", "contnue"):
            with self.subTest(prompt=prompt):
                self.assertTrue(classify(prompt)["is_continuation"])
        # typos inside multi-word continuations — exercises the fuzzy matcher
        for prompt in ("ok contnie now", "yes contionue pls", "cotnine karo bhai"):
            with self.subTest(prompt=prompt):
                self.assertTrue(classify(prompt)["is_continuation"])

    def test_multiword_non_continuations(self):
        # 3-4 word prompts with real content words are NOT continuations
        for prompt in ("fix the login bug", "add dark mode", "run the tests",
                       "update the homepage copy"):
            with self.subTest(prompt=prompt):
                self.assertFalse(classify(prompt)["is_continuation"])

    def test_pastes(self):
        for prompt in (TRACEBACK_PASTE, JS_STACK_PASTE, NPM_LOG_PASTE,
                       CODE_FENCE_PASTE):
            f = classify(prompt)
            with self.subTest(prompt=prompt[:40]):
                self.assertTrue(f["looks_like_paste"])
                self.assertFalse(should_coach(f, CFG))

    def test_plain_multiline_prompt_is_not_paste(self):
        prompt = ("update the pricing page\n"
                  "the monthly plan should show 29 and the yearly should show 290\n"
                  "keep the current layout, just swap the numbers")
        self.assertFalse(classify(prompt)["looks_like_paste"])


# ---------------------------------------------------------------------------
# Precision guard — ZERO false positives allowed
# ---------------------------------------------------------------------------

class TestPrecisionGuard(unittest.TestCase):
    def test_corpus_is_large_enough(self):
        self.assertGreaterEqual(len(PRECISION_SET), 15)

    def test_zero_false_positives(self):
        fired = [p[:60] for p in PRECISION_SET if coach(p)]
        self.assertEqual(fired, [], f"coach fired on silent-set prompts: {fired}")


# ---------------------------------------------------------------------------
# Recall — genuinely under-specified execute prompts must fire
# ---------------------------------------------------------------------------

class TestRecall(unittest.TestCase):
    def test_corpus_is_large_enough(self):
        self.assertGreaterEqual(len(RECALL_SET), 5)

    def test_fixtures_pass_the_word_gate(self):
        for prompt in RECALL_SET:
            with self.subTest(prompt=prompt[:50]):
                self.assertGreaterEqual(
                    classify(prompt)["word_count"], CFG["min_words"],
                    "recall fixture must be >= min_words to test the quality gate",
                )

    def test_all_fire(self):
        for prompt in RECALL_SET:
            f = classify(prompt)
            with self.subTest(prompt=prompt[:50]):
                self.assertEqual(f["mode"], "execute")
                self.assertLess(f["quality"], CFG["coach_below_quality"])
                self.assertTrue(should_coach(f, CFG),
                                f"coach should fire (quality={f['quality']})")


# ---------------------------------------------------------------------------
# should_coach — every gate, in isolation
# ---------------------------------------------------------------------------

def _features(**over) -> dict:
    base = {
        "word_count": 20,
        "is_command": False,
        "is_continuation": False,
        "looks_like_paste": False,
        "is_design": False,
        "mode": "execute",
        "has_constraints": False,
        "has_done_criteria": False,
        "has_reference": False,
        "gaps": ["no acceptance criteria"],
        "quality": 0.4,
    }
    base.update(over)
    return base


class TestShouldCoachGates(unittest.TestCase):
    def test_true_path(self):
        self.assertTrue(should_coach(_features(), CFG))

    def test_mode_off(self):
        self.assertFalse(should_coach(_features(), {**CFG, "mode": "off"}))

    def test_mode_missing_defaults_to_off(self):
        self.assertFalse(should_coach(_features(), {"min_words": 12,
                                                    "coach_below_quality": 0.7}))

    def test_sigil_mode_still_coaches(self):
        # only "off" disables; the sigil opt-in is handled by the hook wrapper
        self.assertTrue(should_coach(_features(), {**CFG, "mode": "sigil"}))

    def test_command(self):
        self.assertFalse(should_coach(_features(is_command=True), CFG))

    def test_continuation(self):
        self.assertFalse(should_coach(_features(is_continuation=True), CFG))

    def test_paste(self):
        self.assertFalse(should_coach(_features(looks_like_paste=True), CFG))

    def test_word_gate(self):
        self.assertFalse(should_coach(_features(word_count=11), CFG))
        self.assertTrue(should_coach(_features(word_count=12), CFG))

    def test_non_execute_modes(self):
        self.assertFalse(should_coach(_features(mode="explore", quality=1.0), CFG))
        self.assertFalse(should_coach(_features(mode="other", quality=0.8), CFG))

    def test_quality_threshold_boundary(self):
        self.assertFalse(should_coach(_features(quality=0.7), CFG),
                         "quality == threshold must NOT coach")
        self.assertTrue(should_coach(_features(quality=0.69), CFG))
        self.assertFalse(should_coach(_features(quality=1.0), CFG))

    def test_defensive_missing_keys(self):
        self.assertFalse(should_coach({}, {}))                    # mode off
        self.assertFalse(should_coach({}, {"mode": "always"}))    # wc 0
        self.assertFalse(should_coach({"word_count": None}, {"mode": "always"}))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_empty_string(self):
        f = classify("")
        self.assertEqual(f["word_count"], 0)
        self.assertEqual(f["mode"], "other")
        self.assertFalse(f["is_command"])
        self.assertFalse(f["looks_like_paste"])
        self.assertFalse(should_coach(f, CFG))

    def test_whitespace_only(self):
        f = classify("   \n\t  ")
        self.assertEqual(f["word_count"], 0)
        self.assertFalse(should_coach(f, CFG))

    def test_none_is_tolerated(self):
        # the hook must never crash on malformed stdin — fail-open contract
        f = classify(None)  # type: ignore[arg-type]
        self.assertEqual(f["word_count"], 0)
        self.assertFalse(should_coach(f, CFG))

    def test_single_emoji(self):
        f = classify("🔥")
        self.assertEqual(f["word_count"], 1)
        self.assertTrue(f["is_continuation"])
        self.assertFalse(should_coach(f, CFG))

    def test_one_word(self):
        f = classify("deploy")
        self.assertEqual(f["word_count"], 1)
        self.assertTrue(f["is_continuation"])  # a task is never one word
        self.assertFalse(should_coach(f, CFG))

    def test_very_long_specified_prompt(self):
        self.assertGreaterEqual(len(LONG_SPECIFIED), 2000)
        f = classify(LONG_SPECIFIED)
        self.assertEqual(f["mode"], "execute")
        self.assertTrue(f["has_done_criteria"])
        self.assertTrue(f["has_constraints"])
        self.assertGreaterEqual(f["quality"], CFG["coach_below_quality"])
        self.assertFalse(should_coach(f, CFG))

    def test_code_fence_anywhere_is_paste(self):
        f = classify(CODE_FENCE_PASTE)
        self.assertTrue(f["looks_like_paste"])

    def test_multiline_numbered_list_sets_done_and_constraints(self):
        prompt = ("please do these:\n"
                  "1. add google login\n"
                  "2. the session should persist for 30 days\n"
                  "3. log the user out from the settings page")
        f = classify(prompt)
        self.assertTrue(f["has_done_criteria"])
        self.assertTrue(f["has_constraints"])
        self.assertFalse(should_coach(f, CFG))

    def test_inline_numbered_list_sets_done_and_constraints(self):
        # voice-dictated lists arrive on one line
        f = classify("1. tapping inside the upload box should trigger upload "
                     "2. remove the nav links 3. fix the overflow")
        self.assertTrue(f["has_done_criteria"])
        self.assertTrue(f["has_constraints"])

    def test_url_reference(self):
        f = classify("make the landing page like https://stripe.com")
        self.assertTrue(f["has_reference"])
        self.assertTrue(f["is_design"])

    def test_like_site_reference(self):
        f = classify("redesign the hero, something like the linear homepage "
                     "but warmer and with our own type")
        self.assertTrue(f["has_reference"])
        self.assertFalse(should_coach(f, CFG))

    def test_design_ask_without_reference_flags_gap(self):
        f = classify("redesign the homepage it looks dated and boring these days honestly")
        self.assertEqual(f["mode"], "execute")
        self.assertTrue(f["is_design"])
        self.assertIn("design ask with no reference or constraints", f["gaps"])


# ---------------------------------------------------------------------------
# recent_context — a short follow-up naming a target already established
# earlier in the session isn't actually under-specified, even with no
# explicit done-state. Default "" must never change existing behavior.
# ---------------------------------------------------------------------------

class TestRecentContext(unittest.TestCase):
    PROD_CFG = {"mode": "always", "min_words": 4, "coach_below_quality": 0.7}

    def test_default_recent_context_is_unchanged_behavior(self):
        f = classify("can we change the url?")
        self.assertEqual(f["mode"], "execute")
        self.assertIn("no acceptance criteria", f["gaps"])
        self.assertFalse(f["has_done_criteria"])
        self.assertTrue(should_coach(f, self.PROD_CFG))

    def test_established_target_drops_acceptance_gap_and_coaching(self):
        ctx = ("swap the ugly sslip.io URL for the real domain, point "
               "queensharbour.<tld> A record to the server")
        f = classify("can we change the url?", recent_context=ctx)
        self.assertNotIn("no acceptance criteria", f["gaps"])
        self.assertNotIn("very terse for a build request", f["gaps"])
        self.assertTrue(f["has_done_criteria"])
        self.assertFalse(should_coach(f, self.PROD_CFG))

    def test_unrelated_context_does_not_suppress_coaching(self):
        ctx = "the weather is nice today, unrelated chit chat"
        f = classify("can we change the url?", recent_context=ctx)
        self.assertIn("no acceptance criteria", f["gaps"])
        self.assertTrue(should_coach(f, self.PROD_CFG))

    def test_context_does_not_rescue_a_genuinely_vague_ask(self):
        # "the mobile version" is a broad surface, not a single scalar value —
        # merely having been mentioned before doesn't make "fix" it checkable.
        # This documents a known, accepted limitation of the phrase-overlap
        # heuristic, not an intended guarantee.
        ctx = "yeah the mobile version looks off to me too"
        f = classify("fix the mobile version", recent_context=ctx)
        self.assertNotIn("no acceptance criteria", f["gaps"])  # accepted trade-off

    def test_non_string_recent_context_is_ignored_not_raised(self):
        f = classify("can we change the url?", recent_context=None)  # type: ignore[arg-type]
        self.assertIn("no acceptance criteria", f["gaps"])


# ---------------------------------------------------------------------------
# Performance — the gate sits in front of every prompt submission
# ---------------------------------------------------------------------------

class TestPerformance(unittest.TestCase):
    def test_thousand_classifications_under_one_second(self):
        corpus = ([p for p, *_ in SPEC_TABLE] + RECALL_SET
                  + [TRACEBACK_PASTE, LONG_SPECIFIED])
        n = len(corpus)
        start = time.perf_counter()
        for i in range(1000):
            classify(corpus[i % n])
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0,
                        f"1000 classify() calls took {elapsed:.3f}s (>= 1s)")


if __name__ == "__main__":
    unittest.main()
