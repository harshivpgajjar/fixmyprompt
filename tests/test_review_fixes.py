"""Regression tests for the xhigh code-review fixes (2026-07-06)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixmyprompt import daemon, refiner

GATE = Path(__file__).resolve().parent.parent / "bin" / "coach_gate.py"
VAGUE = "just make the whole thing better somehow you know what i mean man"
GOOD = ("add a call and whatsapp icon next to the number, call should dial that number "
        "and whatsapp should open their chat with that number")


class JsonExtractionTest(unittest.TestCase):
    def test_trailing_brace_chatter_does_not_break_parse(self):
        good = '{"needs_refinement": true, "mode": "execute", "refined": "do X", "tip": "t"}'
        text = good + "\n\nNote: replace {file} with your actual path."
        for mod in (refiner, daemon):
            obj = mod._extract_json(text)
            self.assertIsNotNone(obj, mod.__name__)
            self.assertTrue(obj["needs_refinement"], mod.__name__)
            self.assertEqual(obj["refined"], "do X")

    def test_fenced_json(self):
        text = '```json\n{"needs_refinement": false, "mode":"other","refined":"","tip":""}\n```'
        self.assertIsNotNone(refiner._extract_json(text))

    def test_nested_braces(self):
        text = '{"needs_refinement": true, "mode":"execute", "refined":"use {a:1}", "tip":""}'
        obj = refiner._extract_json(text)
        self.assertEqual(obj["refined"], "use {a:1}")


class TutorialFailOpenTest(unittest.TestCase):
    """A vague prompt whose refiner CRASHES must, in tutorial mode, show the
    scaffold — never a false 'looks good ✓' affirmation."""
    def _gate(self, prompt, home, fake=None):
        (Path(home)).mkdir(parents=True, exist_ok=True)
        (Path(home) / "config.json").write_text(json.dumps({"tutorial": True}))
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_MODE": "always",
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": "sk-fake-for-llm-mode"}
        if fake:
            env["FIXMYPROMPT_FAKE_REFINE"] = fake
        return subprocess.run([sys.executable, str(GATE)],
                              input=json.dumps({"prompt": prompt, "session_id": "R"}),
                              capture_output=True, text=True, env=env)

    def test_vague_prompt_crash_shows_scaffold_not_affirmation(self):
        out = self._gate(VAGUE, tempfile.mkdtemp(), fake="RAISE")
        data = json.loads(out.stdout)
        self.assertEqual(data.get("decision"), "block")
        self.assertIn("make this sharper", data["reason"])
        self.assertNotIn("looks good", data["reason"])

    def test_wellformed_prompt_still_affirms(self):
        out = self._gate(GOOD, tempfile.mkdtemp(), fake="RAISE")
        data = json.loads(out.stdout)
        self.assertIn("looks good", data["reason"])


class WhisperTutorialTest(unittest.TestCase):
    """whisper + tutorial must NOT tell the model a well-formed/explore prompt is
    'under-specified' — those pass through silently."""
    def _gate(self, prompt, home):
        (Path(home)).mkdir(parents=True, exist_ok=True)
        (Path(home) / "config.json").write_text(json.dumps({"tutorial": True, "mode": "whisper"}))
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_COOLDOWN": "0",
               "ANTHROPIC_API_KEY": "", "PATH": os.path.join(home, "nobin")}
        return subprocess.run([sys.executable, str(GATE)],
                              input=json.dumps({"prompt": prompt, "session_id": "W"}),
                              capture_output=True, text=True, env=env)

    def test_wellformed_passes_silently(self):
        self.assertEqual(self._gate(GOOD, tempfile.mkdtemp()).stdout.strip(), "")

    def test_explore_passes_silently(self):
        out = self._gate("blow me away with a hero, go wild", tempfile.mkdtemp())
        self.assertEqual(out.stdout.strip(), "")

    def test_vague_still_whispers(self):
        out = self._gate("fix the whole mobile thing it is broken somehow", tempfile.mkdtemp())
        data = json.loads(out.stdout)
        self.assertIn("hookSpecificOutput", data)


if __name__ == "__main__":
    unittest.main()
