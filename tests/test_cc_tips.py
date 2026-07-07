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

    def test_goal_condition_filled_from_prompt(self):
        # execution path: the /goal line is paste-ready, not a <placeholder>
        t = tip("fix the suite, keep going until all tests pass")
        self.assertIn("/goal all tests pass", t)
        self.assertNotIn("<condition>", t)

    def test_every_situational_tip_has_an_execution_path(self):
        # the whole point of the feedback: don't just say "you could use X" —
        # give the exact command/keystroke to act on. Each tip carries a "→" line.
        prompts = ["let's start a new feature: profiles",
                   "keep going until all tests pass",
                   "find all usages across the whole codebase",
                   "re-architect the reporting pipeline for scale"]
        for p in prompts:
            t = tip(p)
            self.assertIsNotNone(t, p)
            self.assertIn("→", t, p)  # an explicit do-this line
            # and it names a concrete command/keyword, not just prose
            self.assertTrue(any(tok in t for tok in
                                ("/clear", "/goal", "/effort", "Shift+Tab", "subagents", "ultrathink")), p)

    def test_hard_task_suggests_ultrathink_and_planning(self):
        # ultrathink is a REAL inline keyword (deeper reasoning this turn) — the
        # hard-task tip puts it right in the prompt, plus plan mode for big changes.
        t = tip("refactor and re-architect the reporting pipeline for scale")
        self.assertIsNotNone(t)
        self.assertIn("ultrathink", t)
        self.assertIn("Shift+Tab", t)

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

    def test_ultrathink_is_real_never_debunked(self):
        # ultrathink is a real inline keyword (deeper reasoning this turn), NOT
        # folklore — we must never tell users it doesn't exist.
        for p in ["ultrathink the migration", "re-architect the whole pipeline",
                  "debug this gnarly race condition"]:
            t = tip(p) or ""
            self.assertNotIn("folklore", t.lower())
            self.assertNotIn("no \"ultrathink\"", t.lower())
            self.assertNotIn("isn't real", t.lower())


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

    def test_catalog_includes_ultrathink_and_ultracode(self):
        # both are REAL inline keywords — they must be discoverable in the catalog,
        # and must never be described as fake/folklore.
        names = " | ".join(f["name"].lower() for f in cc_tips.FEATURES)
        self.assertIn("ultrathink", names)
        self.assertIn("ultracode", names)
        blob = cc_tips.catalog().lower()
        self.assertNotIn("folklore", blob)
        self.assertNotIn("no \"ultrathink\"", blob)
        # ultrathink is a Reasoning feature; ultracode is a Delegation feature
        by = {f["name"].lower(): f["category"] for f in cc_tips.FEATURES}
        self.assertEqual(next(v for k, v in by.items() if "ultrathink" in k), "Reasoning")
        self.assertEqual(next(v for k, v in by.items() if "ultracode" in k), "Delegation")

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
