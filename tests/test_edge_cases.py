"""Regression tests for edge cases surfaced by adversarial code review:
  - ReDoS: a large single-line paste must not stall the classifier (submit hook).
  - config.save() must not crash on a corrupt (non-dict) config.json.
  - Image preservation: a submission carrying an image must never be BLOCKED
    (blocking discards the attachment — the user would have to re-attach it).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fixmyprompt import config, scorer

GATE = Path(__file__).resolve().parent.parent / "bin" / "coach_gate.py"


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("coach_gate_mod", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LargePromptPerfTest(unittest.TestCase):
    """A long single-line paste (minified JS, base64, a JWT, a long path list)
    used to hit O(n²) backtracking in the reference regex and freeze the hook
    for up to its 20s timeout. classify() must now stay fast."""

    def test_classify_large_single_line_is_fast(self):
        for blob in ("a=1;" * 30000, "x" * 120000, "/very/long/path" * 8000,
                     "aGVsbG8" * 20000):  # base64-ish single run
            dt = _timed(lambda b=blob: scorer.classify(b))
            self.assertLess(dt, 1.0, f"classify() took {dt:.2f}s on {len(blob)} chars")

    def test_true_word_count_survives_the_scan_bound(self):
        # the regexes scan a bounded copy, but word_count reflects the full text
        self.assertEqual(scorer.classify("word " * 5000)["word_count"], 5000)

    def test_reference_still_detected_after_bounding(self):
        # bounding the pre-dot run must not break normal reference detection
        self.assertTrue(scorer.classify("make it like apple.com")["has_reference"])
        self.assertTrue(scorer.classify("similar to the attached screenshot")["has_reference"])


class ConfigCorruptionTest(unittest.TestCase):
    """A hand-edited or half-written config.json that isn't a dict must not
    crash the CLI (on/off/mode/tutorial/daemon all call save())."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig = (config.RUNTIME_DIR, config.CONFIG_PATH)
        config.RUNTIME_DIR = Path(self._dir)
        config.CONFIG_PATH = Path(self._dir) / "config.json"

    def tearDown(self):
        config.RUNTIME_DIR, config.CONFIG_PATH = self._orig

    def test_save_survives_non_dict_config(self):
        config.CONFIG_PATH.write_text("[1, 2, 3]")  # a JSON array, not an object
        result = config.save({"mode": "off"})        # must not raise
        self.assertIsInstance(result, dict)
        self.assertEqual(result["mode"], "off")

    def test_save_survives_garbage_config(self):
        config.CONFIG_PATH.write_text("not json at all {{{")
        result = config.save({"mode": "always"})
        self.assertEqual(result["mode"], "always")


class ImageAttachmentTest(unittest.TestCase):
    """Blocking a submission discards it, and a hook cannot re-inject a pasted
    image — so image-bearing prompts must NEVER be blocked (else the user loses
    the image on resubmit). They are coached non-blockingly or passed through."""

    def _gate(self, prompt, mode="always"):
        home = tempfile.mkdtemp()
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_MODE": mode,
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": "",
               "PATH": os.path.join(home, "nobin")}
        out = subprocess.run([sys.executable, str(GATE)],
                             input=json.dumps({"prompt": prompt, "session_id": "img"}),
                             capture_output=True, text=True, env=env)
        return out.stdout.strip()

    def _gate_raw(self, payload, mode="always"):
        home = tempfile.mkdtemp()
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_MODE": mode,
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": ""}
        return subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env).stdout.strip()

    def test_image_vague_prompt_is_not_blocked(self):
        out = self._gate("make this better and cleaner please [Image #1]")
        if out:  # if it engaged at all, it must be a whisper, never a block
            data = json.loads(out)
            self.assertNotEqual(data.get("decision"), "block")
            self.assertIn("hookSpecificOutput", data)

    def test_image_never_blocks_across_shapes(self):
        for p in ["redo this [Image #3]", "match this design [Image]",
                  "fix per [pasted image 2]"]:
            out = self._gate(p)
            if out:
                self.assertNotEqual(json.loads(out).get("decision"), "block", p)

    def test_image_via_stdin_field_not_blocked(self):
        # image conveyed as a separate stdin field (not a text marker)
        out = self._gate_raw({"prompt": "make this nicer overall",
                              "session_id": "z", "images": ["/tmp/x.png"]})
        if out:
            self.assertNotEqual(json.loads(out).get("decision"), "block")

    def test_textonly_vague_prompt_still_blocks(self):
        # regression: normal (no-image) coaching is unchanged
        out = self._gate("make the whole thing better and nicer somehow")
        self.assertTrue(out)
        self.assertEqual(json.loads(out).get("decision"), "block")

    def test_has_attachment_detection(self):
        cg = _load_gate_module()
        self.assertTrue(cg._has_attachment({}, "do this [Image #2]"))
        self.assertTrue(cg._has_attachment({}, "see [Image]"))
        self.assertTrue(cg._has_attachment({"images": ["a.png"]}, "go"))
        self.assertTrue(cg._has_attachment({"attachments": [1]}, "go"))
        self.assertFalse(cg._has_attachment({}, "a prompt discussing images abstractly"))
        self.assertFalse(cg._has_attachment({"images": []}, "no attachment here"))


def _timed(fn) -> float:
    t = time.time()
    fn()
    return time.time() - t


if __name__ == "__main__":
    unittest.main()
