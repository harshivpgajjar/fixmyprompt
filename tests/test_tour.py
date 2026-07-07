"""Tests for the interactive onboarding tour (`fixmyprompt tour` / `help`)."""
import builtins
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "bin" / "fixmyprompt"


def run_cli(args, home, stdin=""):
    env = {**os.environ, "FIXMYPROMPT_HOME": home, "NO_COLOR": "1"}
    return subprocess.run([sys.executable, str(CLI), *args], input=stdin,
                          capture_output=True, text=True, env=env)


class TourTest(unittest.TestCase):
    def test_tour_shows_all_steps_and_marks_toured(self):
        home = tempfile.mkdtemp()
        out = run_cli(["tour"], home).stdout
        for needle in ["Welcome to FixMyPrompt", "How the coach works",
                       "Discover Claude Code features", "Teach-mode",
                       "Faster rewrites", "Token-usage warnings", "You're set"]:
            self.assertIn(needle, out, needle)
        self.assertTrue((Path(home) / ".toured").exists())

    def test_tour_teaches_send_as_is_and_image_safety(self):
        # the tour must fix the confusion the user hit: how to send as-is, and
        # that images are never lost to the coach.
        out = run_cli(["tour"], tempfile.mkdtemp()).stdout.lower()
        self.assertIn("as-is", out)
        self.assertIn("stays in the box", out)
        self.assertIn("never intercepted", out)

    def test_tour_covers_the_four_topics(self):
        out = run_cli(["tour"], tempfile.mkdtemp()).stdout.lower()
        self.assertIn("fixmyprompt features", out)     # features
        self.assertIn("daemon", out)                    # daemon toggle
        self.assertIn("teach-mode", out)                # teach-mode
        self.assertIn("token", out)                     # token-usage warnings

    def test_noninteractive_tour_does_not_mutate_config(self):
        home = tempfile.mkdtemp()
        run_cli(["tour"], home)
        self.assertFalse((Path(home) / "config.json").exists())

    def test_help_reinvokes_the_tour(self):
        out = run_cli(["help"], tempfile.mkdtemp()).stdout
        self.assertIn("Welcome to FixMyPrompt", out)

    def test_help_commands_lists_the_command_set(self):
        out = run_cli(["help", "--commands"], tempfile.mkdtemp()).stdout
        for c in ("tour", "features", "daemon", "tutorial", "suggest", "on", "off"):
            self.assertIn(c, out, c)

    def test_first_run_hint_appears_then_disappears(self):
        home = tempfile.mkdtemp()
        self.assertIn("tour", run_cli(["status"], home).stderr.lower())
        run_cli(["tour"], home)  # consumes first-run state
        self.assertNotIn("60-second", run_cli(["status"], home).stderr)

    def test_unknown_command_prints_usage(self):
        r = run_cli(["boguscmd"], tempfile.mkdtemp())
        self.assertEqual(r.returncode, 1)
        self.assertIn("usage: fixmyprompt", r.stdout)


class InteractiveTourTest(unittest.TestCase):
    """The interactive apply-path (config.save on y/n) — never exercised by the
    piped CLI tests, so cover it directly with a scripted input() stream."""

    def setUp(self):
        from fixmyprompt import config, tour
        self.config, self.tour = config, tour
        self._dir = tempfile.mkdtemp()
        self._orig = (config.RUNTIME_DIR, config.CONFIG_PATH, tour._TOURED_MARKER)
        config.RUNTIME_DIR = Path(self._dir)
        config.CONFIG_PATH = Path(self._dir) / "config.json"
        tour._TOURED_MARKER = Path(self._dir) / ".toured"

    def tearDown(self):
        self.config.RUNTIME_DIR, self.config.CONFIG_PATH, self.tour._TOURED_MARKER = self._orig

    def _run(self, answers):
        it = iter(answers)
        orig = builtins.input
        builtins.input = lambda *a, **k: next(it, "")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.tour.run(cli_path=None, interactive=True)
        finally:
            builtins.input = orig

    def test_yes_at_teach_question_enables_teach_mode(self):
        # 4th input() is the "Turn teach-mode ON now?" question (teach-mode off)
        self._run(["", "", "", "y", "", "", "", ""])
        self.assertTrue(self.config.load().get("tutorial"))

    def test_no_when_already_on_disables_teach_mode(self):
        self.config.save({"tutorial": True})
        # already-on -> "Keep teach-mode ON?"; answer n -> disabled
        self._run(["", "", "", "n", "", "", "", ""])
        self.assertFalse(self.config.load().get("tutorial"))

    def test_interactive_marks_toured(self):
        self._run(["", "", "", "y", "", "", "", ""])
        self.assertTrue(self.tour.has_toured())


if __name__ == "__main__":
    unittest.main()
