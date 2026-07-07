"""Tests for the model + effort suggester (fixmyprompt suggest)."""
import unittest

from fixmyprompt import scorer


def sug(prompt):
    return scorer.suggest_model_effort(scorer.classify(prompt), prompt)


class ModelSuggestTest(unittest.TestCase):
    def test_mechanical_gets_haiku(self):
        for p in ["rename the getUser function to fetchUser everywhere",
                  "fix a typo in the readme header",
                  "update the copy on the pricing button to say Subscribe"]:
            s = sug(p)
            self.assertEqual(s["model"], "Haiku 4.5", p)
            self.assertEqual(s["effort"], "low")

    def test_hard_gets_subscription_top_model_with_fable_note(self):
        # Primary recommendation must be a SUBSCRIPTION-included model (Opus 4.8),
        # since Fable 5 is not in the standard subscription — but Fable is offered
        # as an optional upgrade note so nobody is steered to a model they can't run.
        for p in ["refactor the whole auth system to support multi-tenant orgs",
                  "debug why the websocket connection drops under load, root cause it",
                  "architect a distributed job queue for the pipeline"]:
            s = sug(p)
            self.assertEqual(s["model"], "Opus 4.8", p)
            self.assertEqual(s["effort"], "xhigh")
            self.assertIn("Fable 5", s["note"])
            self.assertIn("subscription", s["note"].lower())

    def test_standard_execute_gets_sonnet_high(self):
        s = sug("add a settings page with a dark mode toggle and save it to local storage")
        self.assertEqual(s["model"], "Sonnet 5")
        self.assertEqual(s["effort"], "high")

    def test_explore_gets_sonnet(self):
        s = sug("blow me away with a landing page for my coffee shop, go wild")
        self.assertEqual(s["model"], "Sonnet 5")

    def test_shape_and_keys(self):
        s = sug("build a thing")
        self.assertEqual(set(s), {"model", "effort", "why", "note"})
        self.assertTrue(s["why"])

    def test_never_recommends_non_subscription_model_as_primary(self):
        # the primary `model` must always be subscription-included, never Fable 5
        for p in ["refactor the entire distributed system from scratch",
                  "debug a gnarly race condition", "fix a typo", "build a login form",
                  "blow me away with a hero", "make it look nicer"]:
            self.assertNotEqual(sug(p)["model"], "Fable 5", p)

    def test_large_feature_build_stays_sonnet(self):
        # large ≠ hard: a big feature build routes to Sonnet, not Fable (Fable is
        # for architectural/ambiguous work, not just volume).
        s = sug("build a full analytics dashboard for the sales team so they can see "
                "revenue and reps and pipeline in one place, make it comprehensive")
        self.assertEqual(s["model"], "Sonnet 5")

    def test_architectural_build_escalates(self):
        # hard signal present -> top subscription model + Fable upgrade note
        s = sug("refactor and re-architect the whole reporting pipeline for scale")
        self.assertEqual(s["model"], "Opus 4.8")
        self.assertIn("Fable 5", s["note"])


if __name__ == "__main__":
    unittest.main()
