"""Tests for `fixmyprompt project add|list|remove` — the CLI for managing
per-project clarifying hints (the generic, user-populated replacement for what
used to be a hardcoded personal seed)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "bin" / "fixmyprompt"


def run(args, home):
    env = {**os.environ, "FIXMYPROMPT_HOME": home}
    return subprocess.run([sys.executable, str(CLI), "project", *args],
                          capture_output=True, text=True, env=env)


class CliProjectTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_list_empty_shows_usage_hint(self):
        out = run(["list"], self.home)
        self.assertIn("No project hints yet", out.stdout)
        self.assertIn("fixmyprompt project add", out.stdout)

    def test_add_then_list(self):
        add = run(["add", "acme", "which client brief?"], self.home)
        self.assertIn("added", add.stdout)
        out = run(["list"], self.home)
        self.assertIn("acme", out.stdout)
        self.assertIn("which client brief?", out.stdout)

    def test_add_missing_args(self):
        out = run(["add", "acme"], self.home)
        self.assertIn("usage:", out.stdout)

    def test_remove(self):
        run(["add", "acme", "which client brief?"], self.home)
        rm = run(["remove", "acme"], self.home)
        self.assertIn("removed", rm.stdout)
        out = run(["list"], self.home)
        self.assertIn("No project hints yet", out.stdout)

    def test_remove_unknown_key(self):
        out = run(["remove", "nope"], self.home)
        self.assertIn("no such hint", out.stdout)

    def test_default_sub_is_list(self):
        out = run([], self.home)
        self.assertIn("No project hints yet", out.stdout)


if __name__ == "__main__":
    unittest.main()
