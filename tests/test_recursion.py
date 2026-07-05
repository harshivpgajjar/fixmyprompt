"""The refiner shells out to `claude -p`, whose own UserPromptSubmit hook would
re-enter the gate. This proves the WHETSTONE_IN_REFINER guard makes nested
invocations an instant no-op passthrough — no double-block, no infinite loop."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "bin" / "coach_gate.py"


class RecursionGuardTest(unittest.TestCase):
    def _run(self, payload, env):
        full = {**os.environ, **env}
        proc = subprocess.run(
            [sys.executable, str(GATE)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=full,
        )
        return proc

    def test_in_refiner_forces_passthrough(self):
        tmp = tempfile.mkdtemp()
        # a prompt that WOULD be coached in always-mode...
        payload = {
            "prompt": "build me a full analytics dashboard for the sales team so they can see it all",
            "session_id": "R1",
        }
        proc = self._run(payload, {
            "WHETSTONE_HOME": tmp,
            "PCOACH_MODE": "always",
            "WHETSTONE_IN_REFINER": "1",  # simulate the nested claude -p call
            "ANTHROPIC_API_KEY": "",
        })
        # ...must pass straight through (empty stdout), exit 0, no block.
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_guard_absent_still_works(self):
        # sanity: without the guard var, the same prompt is processed (may block
        # or pass depending on the refiner, but must not crash).
        tmp = tempfile.mkdtemp()
        proc = self._run(
            {"prompt": "yes", "session_id": "R2"},
            {"WHETSTONE_HOME": tmp, "PCOACH_MODE": "always", "ANTHROPIC_API_KEY": ""},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")  # 'yes' is a continuation -> silent


if __name__ == "__main__":
    unittest.main()
