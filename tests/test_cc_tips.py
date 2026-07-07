"""Tests for Claude Code feature tips — the situational suggestions (/clear,
/goal, plan mode, subagents) and their gate integration."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixmyprompt import cc_tips, scorer

GATE = Path(__file__).resolve().parent.parent / "bin" / "coach_gate.py"


def tip(prompt):
    r = cc_tips.analyze(prompt, scorer.classify(prompt))
    return r["tip"] if r else None


class CcTipDetectionTest(unittest.TestCase):
    def test_new_work_suggests_clear(self):
        for p in ["let's start a new feature: user profiles",
                  "now let's build the billing module",
                  "moving on to the next feature",
                  "add a new payments feature to the app"]:
            t = tip(p)
            self.assertIsNotNone(t, p)
            self.assertIn("/clear", t)

    def test_big_goal_suggests_goal_command(self):
        for p in ["fix all the failing tests, keep going until all tests pass",
                  "build the whole checkout flow end-to-end"]:
            self.assertIn("/goal", tip(p) or "", p)

    def test_hard_task_suggests_plan_mode(self):
        t = tip("refactor and re-architect the reporting pipeline for scale")
        self.assertIsNotNone(t)
        self.assertIn("plan mode", t.lower())

    def test_broad_suggests_subagents(self):
        t = tip("find all usages of the old API across the whole codebase")
        self.assertIn("subagent", (t or "").lower())

    def test_ordinary_prompt_no_tip(self):
        self.assertIsNone(tip("add a call button next to the phone number"))

    def test_no_invented_commands(self):
        # accuracy guard: never present a bogus command as a real one
        for prompt in ["let's start a new feature", "keep going until it works",
                       "re-architect the whole thing", "audit every file"]:
            t = tip(prompt) or ""
            self.assertNotIn("/plan ", t)  # plan mode is Shift+Tab, not a /plan command

    def test_ultrathink_redirects_to_effort(self):
        # the "ultrathink" magic word is folklore — surface the correction, and
        # only mention "ultrathink" in the context of debunking it.
        for p in ["ultrathink this refactor", "please ultra think about the design",
                  "turn on extended thinking and solve it", "megathink the algorithm"]:
            t = tip(p)
            self.assertIsNotNone(t, p)
            self.assertIn("/effort", t)
            self.assertIn("folklore", t.lower())

    def test_ultrathink_myth_never_endorsed(self):
        # if the word appears at all, it must be the debunk tip, never an endorsement
        for p in ["ultrathink the migration", "ultra-think this"]:
            t = tip(p) or ""
            self.assertIn("no \"ultrathink\"", t.lower().replace("“", "\"").replace("”", "\""))

    def test_normal_thinking_words_do_not_trip_ultrathink(self):
        # ordinary English must not be mistaken for the magic-word attempt
        # (the plan tip also mentions /effort, so assert on the debunk's unique word)
        for p in ["think about the best data model then build the users table",
                  "I think we should add pagination to the list endpoint"]:
            t = tip(p) or ""
            self.assertNotIn("folklore", t.lower())


class CcCatalogTest(unittest.TestCase):
    """The browsable feature catalog (`fixmyprompt features`)."""

    def test_every_feature_has_use_and_tradeoff(self):
        self.assertGreaterEqual(len(cc_tips.FEATURES), 15)
        for f in cc_tips.FEATURES:
            for key in ("name", "category", "use", "tradeoff"):
                self.assertIn(key, f)
                self.assertTrue(f[key].strip(), f"{f.get('name')}: empty {key}")

    def test_catalog_covers_the_headline_features(self):
        # the user's ask: goal, vision, artifacts, async/parallel, etc. must all be there
        text = cc_tips.catalog().lower()
        for needle in ["/clear", "/compact", "/effort", "/model", "subagent",
                       "/goal", "@file", "vision", "artifact", "/rewind",
                       "claude.md", "/usage", "parallel"]:
            self.assertIn(needle, text, needle)

    def test_catalog_groups_by_category(self):
        text = cc_tips.catalog()
        for cat in ("Context", "Reasoning", "Model", "Delegation"):
            self.assertIn(f"── {cat} ──", text)

    def test_catalog_filter_by_category(self):
        ctx = cc_tips.catalog("context")
        self.assertIn("/clear", ctx)
        self.assertNotIn("/rewind", ctx)  # a History feature, filtered out

    def test_catalog_unknown_category_is_helpful(self):
        out = cc_tips.catalog("nonsense")
        self.assertIn("No category", out)

    def test_catalog_never_ships_ultrathink_as_a_feature(self):
        # accuracy: ultrathink/think-hard must never be a standalone catalog entry
        # (mentioning it inside the /effort entry to debunk it is fine and useful).
        for f in cc_tips.FEATURES:
            name = f["name"].lower()
            self.assertNotIn("ultrathink", name)
            self.assertNotIn("think harder", name)
            self.assertNotIn("think hard", name)
        # and wherever the word appears, it's framed as replaced/folklore, never endorsed
        for f in cc_tips.FEATURES:
            body = (f["use"] + " " + f["tradeoff"]).lower()
            if "ultrathink" in body:
                self.assertTrue("folklore" in body or "replaces" in body or "instead" in body,
                                f"{f['name']} mentions ultrathink without debunking it")

    def test_catalog_command_runs(self):
        cli = Path(__file__).resolve().parent.parent / "bin" / "fixmyprompt"
        out = subprocess.run([sys.executable, str(cli), "features"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("/effort", out.stdout)
        self.assertIn("cost:", out.stdout)


class CcTipGateTest(unittest.TestCase):
    """The high-value new-work tip must surface even on a WELL-FORMED prompt
    (tip-only engagement), and ride along when the gate already coaches."""
    def _gate(self, prompt, home, mode="always"):
        (Path(home)).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_MODE": mode,
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": "",
               "PATH": os.path.join(home, "nobin")}
        return subprocess.run([sys.executable, str(GATE)],
                              input=json.dumps({"prompt": prompt, "session_id": "C"}),
                              capture_output=True, text=True, env=env)

    def test_wellformed_new_work_gets_tip_only_block(self):
        # a fully-specified prompt that also starts new work -> tip-only block
        good = ("let's start a new feature: a settings page with a dark-mode toggle "
                "that persists to local storage. done when it survives a reload.")
        out = self._gate(good, tempfile.mkdtemp())
        data = json.loads(out.stdout)
        self.assertEqual(data.get("decision"), "block")
        self.assertIn("/clear", data["reason"])
        self.assertIn("tip", data["reason"].lower())

    def test_tip_only_is_loop_proof(self):
        home = tempfile.mkdtemp()
        good = "let's start a new feature with a clear done-state: tests pass and it reloads"
        first = self._gate(good, home)
        second = self._gate(good, home)
        self.assertEqual(json.loads(first.stdout).get("decision"), "block")
        self.assertEqual(second.stdout.strip(), "")  # one-shot bypass consumed

    def test_vague_prompt_gets_scaffold_plus_tip(self):
        # under-specified execute prompt that also signals new work -> scaffold
        # AND the tip rides along
        out = self._gate("now let's build the whole dashboard thing", tempfile.mkdtemp())
        data = json.loads(out.stdout)
        self.assertEqual(data.get("decision"), "block")
        self.assertIn("/clear", data["reason"])

    def test_ordinary_wellformed_prompt_still_passes(self):
        good = ("add a call and whatsapp icon next to the number, call should dial it "
                "and whatsapp should open their chat with that number")
        out = self._gate(good, tempfile.mkdtemp())
        self.assertEqual(out.stdout.strip(), "")  # no tip, well-formed -> silent


if __name__ == "__main__":
    unittest.main()
