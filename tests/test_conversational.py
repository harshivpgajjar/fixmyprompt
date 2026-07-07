"""Regression: FixMyPrompt must NOT coach conversational questions / mid-dialogue
replies. The refiner would rewrite the user's actual meaning (it once turned
"So all features that are in mac are not built for windows?" into an unrelated
scoping question, hallucinating project names from memory). These are things the
user says TO the assistant, not prompts to refine — even in tutorial mode.
"""
import unittest

from fixmyprompt import scorer

TUTORIAL = {"mode": "always", "min_words": 4, "tutorial": True}
PLAIN = {"mode": "always", "min_words": 4}


class ConversationalSuppressionTest(unittest.TestCase):
    def _coached(self, prompt, cfg):
        return scorer.should_coach(scorer.classify(prompt), cfg)

    def test_conversational_prompts_never_coached_even_in_tutorial(self):
        for p in [
            "So all features that are in mac are not built for windows?",
            "No, i want to replace Odoo too, by building our own version of the two tools",
            "Actually, use blue instead",
            "also add a footer to that",
            "wait, that's not what I meant",
            "is fixmyprompt active?",
            "what do you think of my prompts?",
            "hmm ok so it only works on mac?",
            "yeah but does that break the daemon?",
        ]:
            self.assertTrue(scorer.classify(p)["is_conversational"], p)
            self.assertFalse(self._coached(p, TUTORIAL), f"tutorial coached: {p}")
            self.assertFalse(self._coached(p, PLAIN), f"coached: {p}")

    def test_real_work_prompts_still_coached_in_tutorial(self):
        for p in [
            "build a settings page with a dark mode toggle",
            "make the navbar sticky on scroll",
            "fix the login bug so invalid passwords show an error",
            "blow me away with a landing page, go wild",
        ]:
            self.assertFalse(scorer.classify(p)["is_conversational"], p)
            self.assertTrue(self._coached(p, TUTORIAL), f"tutorial skipped: {p}")

    def test_task_phrased_as_question_stays_a_task(self):
        # a question that carries an execute verb is still a build request
        f = scorer.classify("can you build me a whole dashboard?")
        self.assertEqual(f["mode"], "execute")
        self.assertFalse(f["is_conversational"])
        self.assertTrue(self._coached("can you build me a whole dashboard?", TUTORIAL))

    def test_tutorial_does_not_coach_mode_other_statements(self):
        # non-conversational mode='other' (a statement) is still not a task
        f = scorer.classify("the sky is blue and grass is green today")
        self.assertEqual(f["mode"], "other")
        self.assertFalse(self._coached("the sky is blue and grass is green today", TUTORIAL))


if __name__ == "__main__":
    unittest.main()
