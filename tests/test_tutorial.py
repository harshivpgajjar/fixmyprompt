"""Tutorial mode: coach EVERY real prompt regardless of size/vagueness; the
well-formed ones get an affirmation. Continuations/commands/pastes stay silent."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from whetstone import scorer

GATE = Path(__file__).resolve().parent.parent / "bin" / "coach_gate.py"


class TutorialGateUnitTest(unittest.TestCase):
    def test_should_coach_tutorial_coaches_everything_real(self):
        cfg = {"mode": "always", "tutorial": True, "min_words": 4,
               "coach_below_quality": 0.7}
        for p in ["fix it now please for the mobile view",  # short, would fail min_words normally
                  "add a call and whatsapp icon next to the number, call should dial it",  # well-formed
                  "blow me away with a hero section"]:  # explore
            self.assertTrue(scorer.should_coach(scorer.classify(p), cfg), p)

    def test_tutorial_still_silent_on_continuations(self):
        cfg = {"mode": "always", "tutorial": True, "min_words": 4}
        for p in ["yes", "continue", "/refine foo", "```code```"]:
            self.assertFalse(scorer.should_coach(scorer.classify(p), cfg), p)


class TutorialGateE2ETest(unittest.TestCase):
    def _run(self, prompt, home, extra=None):
        env = {**os.environ, "WHETSTONE_HOME": home, "PCOACH_MODE": "always",
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": "",
               "PATH": os.path.join(home, "nobin")}
        if extra:
            env.update(extra)
        return subprocess.run([sys.executable, str(GATE)],
                              input=json.dumps({"prompt": prompt, "session_id": "T"}),
                              capture_output=True, text=True, env=env)

    def test_wellformed_prompt_gets_affirmation_in_tutorial(self):
        home = tempfile.mkdtemp()
        good = ("add a call and whatsapp icon next to the number, call should dial that "
                "number and whatsapp should open their chat with that number")
        # tutorial ON -> even a well-formed prompt blocks with an affirmation
        out = self._run(good, home, {"PCOACH_TUTORIAL": "1"} if False else None)
        # PCOACH_TUTORIAL isn't an env var; set via config file instead:
        # write a config enabling tutorial
        (Path(home)).mkdir(parents=True, exist_ok=True)
        (Path(home) / "config.json").write_text(json.dumps({"tutorial": True}))
        out = self._run(good, home)
        data = json.loads(out.stdout)
        self.assertEqual(data.get("decision"), "block")
        self.assertIn("Well-specified", data["reason"])

    def test_model_suggestion_appears_in_banner(self):
        home = tempfile.mkdtemp()
        out = self._run("fix the mobile version of the whole site", home)
        data = json.loads(out.stdout)
        self.assertIn("suggested:", data["reason"])

    def test_tutorial_off_wellformed_passes(self):
        home = tempfile.mkdtemp()
        good = ("add a call and whatsapp icon next to the number, call should dial that "
                "number and whatsapp should open their chat with that number")
        out = self._run(good, home)  # tutorial off (default)
        self.assertEqual(out.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
