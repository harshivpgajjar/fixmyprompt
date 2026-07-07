"""Whisper mode: fully subscription, no key, no extra model call. On a vague
execute prompt it does NOT block — it injects an additionalContext coaching note
so the main session model asks for the missing piece. Silent on everything else."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "bin" / "coach_gate.py"
ROUGH = "build me a full analytics dashboard for the sales team so they can see it all"


def run(prompt, session, home, mode="whisper", cooldown="0"):
    env = {
        **os.environ,
        "FIXMYPROMPT_HOME": home,
        "PCOACH_MODE": mode,
        "PCOACH_COOLDOWN": cooldown,
        "ANTHROPIC_API_KEY": "",
        "PATH": os.path.join(home, "nobin"),
    }
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps({"prompt": prompt, "session_id": session}),
        capture_output=True, text=True, env=env,
    )


class WhisperTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def _actions(self):
        log = Path(self.home) / "prompt-log.jsonl"
        return [json.loads(line)["action"] for line in log.read_text().splitlines()] if log.exists() else []

    def test_whisper_injects_not_blocks(self):
        out = run(ROUGH, "W1", self.home)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stderr, "")
        data = json.loads(out.stdout)
        self.assertNotIn("decision", data)  # NOT a block
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("under-specified", ctx)
        self.assertIn("FixMyPrompt", ctx)
        self.assertEqual(self._actions(), ["coach"])

    def test_whisper_never_calls_llm(self):
        # PATH is stripped and no key: if whisper tried an LLM it'd stall/fail.
        # It must return instantly with the injected note regardless.
        out = run(ROUGH, "W2", self.home)
        self.assertIn("hookSpecificOutput", json.loads(out.stdout))

    def test_whisper_silent_on_continuation(self):
        out = run("yes do it", "W3", self.home)
        self.assertEqual(out.stdout.strip(), "")

    def test_whisper_silent_on_explore(self):
        out = run("blow me away with a landing page, go wild", "W4", self.home)
        self.assertEqual(out.stdout.strip(), "")

    def test_whisper_silent_on_wellformed(self):
        good = ("add a call and whatsapp icon next to it, call should dial that number "
                "and whatsapp should open their chat with that number")
        self.assertEqual(run(good, "W5", self.home).stdout.strip(), "")

    def test_whisper_respects_cooldown(self):
        run(ROUGH, "W6", self.home, cooldown="300")
        out2 = run(ROUGH, "W6", self.home, cooldown="300")
        self.assertEqual(out2.stdout.strip(), "")  # cooled down -> silent


if __name__ == "__main__":
    unittest.main()
