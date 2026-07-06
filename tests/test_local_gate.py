"""The keyless LOCAL gate: with no ANTHROPIC_API_KEY, a coachable execute prompt
gets an instant deterministic scaffold block — no LLM, no network — and the flow
stays loop-proof and fail-open. This is the default experience on any Claude
subscription with zero setup.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "bin" / "coach_gate.py"

ROUGH = "build me a full analytics dashboard for the sales team so they can see it all"


def run(prompt, session, home, mode="always"):
    env = {
        **os.environ,
        "FIXMYPROMPT_HOME": home,
        "PCOACH_MODE": mode,
        "PCOACH_COOLDOWN": "0",
        "ANTHROPIC_API_KEY": "",          # local mode
        "PATH": os.path.join(home, "nobin"),  # claude CLI unreachable
    }
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps({"prompt": prompt, "session_id": session}),
        capture_output=True, text=True, env=env,
    )


class LocalGateTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def _actions(self):
        log = Path(self.home) / "prompt-log.jsonl"
        if not log.exists():
            return []
        return [json.loads(l)["action"] for l in log.read_text().splitlines() if l.strip()]

    def test_scaffold_block_no_network(self):
        out = run(ROUGH, "L1", self.home)
        self.assertEqual(out.returncode, 0)
        p = json.loads(out.stdout)
        self.assertEqual(p["decision"], "block")
        self.assertIn("Done means", p["reason"])
        self.assertIn("make this sharper", p["reason"])  # non-sendable banner
        self.assertNotIn("[y ⏎] send refined", p["reason"])  # nothing to auto-send

    def test_loop_proof_local(self):
        # block, then the same prompt again must PASS (one-shot bypass), never
        # two blocks in a row even in local mode.
        first = run(ROUGH, "L2", self.home)
        second = run(ROUGH, "L2", self.home)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        self.assertEqual(second.stdout.strip(), "")  # passthrough

    def test_bare_y_after_local_block_passes_not_accepts(self):
        # in local mode the scaffold isn't sendable, so `y` must pass through
        # (become a normal 'y'), NOT emit an accept with placeholder text.
        run(ROUGH, "L3", self.home)
        out = run("y", "L3", self.home)
        self.assertEqual(out.stdout.strip(), "")  # plain passthrough

    def test_wellformed_prompt_silent_in_local_mode(self):
        good = ("add a call and whatsapp icon next to it, call should dial that "
                "number and whatsapp should open their chat with that number")
        out = run(good, "L4", self.home)
        self.assertEqual(out.stdout.strip(), "")  # already good -> silent

    def test_continuation_silent_in_local_mode(self):
        self.assertEqual(run("yes", "L5", self.home).stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
