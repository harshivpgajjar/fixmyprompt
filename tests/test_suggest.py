"""Tests for the offline template scaffold fallback."""
import unittest

from fixmyprompt import scorer, suggest


class SuggestTest(unittest.TestCase):
    def test_execute_with_gaps_gets_scaffold(self):
        p = "fix the mobile version"
        s = suggest.template(p, scorer.classify(p))
        self.assertIsNotNone(s)
        self.assertIn("Done means", s)
        self.assertTrue(s.startswith(p))

    def test_design_gap_gets_direction_line(self):
        p = "make a landing page for my coffee shop that looks really good"
        s = suggest.template(p, scorer.classify(p))
        self.assertIsNotNone(s)
        self.assertIn("Direction", s)

    def test_explore_gets_no_scaffold(self):
        p = "blow me away with a landing page, go wild"
        self.assertIsNone(suggest.template(p, scorer.classify(p)))

    def test_wellformed_execute_gets_no_scaffold(self):
        p = ("add a call and whatsapp icon next to it, call should dial that number "
             "and whatsapp should open their chat with that number")
        self.assertIsNone(suggest.template(p, scorer.classify(p)))

    def test_continuation_gets_no_scaffold(self):
        self.assertIsNone(suggest.template("yes", scorer.classify("yes")))

    def test_terse_gap_alone_does_not_add_target_line(self):
        # Regression: the Target line must only appear for an actually vague
        # target (_VAGUE_TARGET, e.g. "change it"), not merely because the
        # prompt is short. "the url" is a concrete target — no Target line.
        p = "can we change the url?"
        f = scorer.classify(p)
        self.assertIn("very terse for a build request", f["gaps"])
        self.assertNotIn("vague target (what exactly should change?)", f["gaps"])
        s = suggest.template(p, f)
        self.assertIn("Done means", s)
        self.assertNotIn("Target:", s)

    def test_genuinely_vague_target_still_gets_target_line(self):
        p = "just change it"
        f = scorer.classify(p)
        s = suggest.template(p, f)
        self.assertIsNotNone(s)
        self.assertIn("Target:", s)


if __name__ == "__main__":
    unittest.main()
