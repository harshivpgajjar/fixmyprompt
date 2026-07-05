"""Tests for the offline template scaffold fallback."""
import unittest

from whetstone import scorer, suggest


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


if __name__ == "__main__":
    unittest.main()
