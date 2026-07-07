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
        # accuracy guard: never ship unverified command names
        for prompt in ["let's start a new feature", "keep going until it works",
                       "re-architect the whole thing", "audit every file"]:
            t = tip(prompt) or ""
            self.assertNotIn("ultrathink", t.lower())
            self.assertNotIn("/plan ", t)  # plan mode is Shift+Tab, not a /plan command


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
