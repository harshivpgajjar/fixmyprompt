"""Regression tests for edge cases surfaced by adversarial code review:
  - ReDoS: a large single-line paste must not stall the classifier (submit hook).
  - config.save() must not crash on a corrupt (non-dict) config.json.
"""
import tempfile
import time
import unittest
from pathlib import Path

from fixmyprompt import config, scorer


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


def _timed(fn) -> float:
    t = time.time()
    fn()
    return time.time() - t


if __name__ == "__main__":
    unittest.main()
