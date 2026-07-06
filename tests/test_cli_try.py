"""Smoke tests for `fixmyprompt try` — the safe live-gate simulator."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "bin" / "fixmyprompt"


def run_try(prompt, min_words=None):
    # isolate from the user's real config (which may be whisper/daemon) and from
    # any running daemon, so `try` previews the default 'always' block behavior.
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "",
        "FIXMYPROMPT_HOME": tempfile.mkdtemp(),  # fresh: mode defaults off -> preview 'always'
    }
    if min_words is not None:
        env["PCOACH_MIN_WORDS"] = str(min_words)
    return subprocess.run(
        [sys.executable, str(CLI), "try", prompt],
        capture_output=True, text=True, env=env,
    ).stdout


class CliTryTest(unittest.TestCase):
    def test_vague_execute_would_block(self):
        out = run_try("build me a full analytics dashboard for the sales team so they can see it all")
        self.assertIn("would BLOCK", out)
        self.assertIn("Done means", out)

    def test_continuation_passes(self):
        out = run_try("yes do it")
        self.assertIn("would PASS silently", out)
        self.assertIn("continuation", out)

    def test_explore_passes(self):
        out = run_try("blow me away with a landing page, go wild")
        self.assertIn("would PASS silently", out)
        self.assertIn("explore", out)

    def test_word_gate_knob(self):
        # 5-word vague prompt: coached at the default gate (4), silent when raised
        self.assertIn("would BLOCK", run_try("make this website mobile responsive"))
        self.assertIn("would PASS silently",
                      run_try("make this website mobile responsive", min_words=8))

    def test_usage_without_arg(self):
        out = subprocess.run([sys.executable, str(CLI), "try"], capture_output=True, text=True).stdout
        self.assertIn("usage:", out)


if __name__ == "__main__":
    unittest.main()
