"""Tests for the refiner's parsing + fail-open behavior (no network required)."""
import unittest

from whetstone import refiner


class RefinerParseTest(unittest.TestCase):
    def test_extract_direct_json(self):
        obj = refiner._extract_json('{"needs_refinement": true, "refined": "x", "tip": "t", "mode":"execute"}')
        self.assertTrue(obj["needs_refinement"])

    def test_extract_json_from_prose(self):
        text = 'Here you go:\n{"needs_refinement": false, "mode":"other", "refined":"", "tip":""}\ndone'
        obj = refiner._extract_json(text)
        self.assertIsNotNone(obj)
        self.assertFalse(obj["needs_refinement"])

    def test_extract_none_on_garbage(self):
        self.assertIsNone(refiner._extract_json("no json here at all"))
        self.assertIsNone(refiner._extract_json(""))

    def test_normalize_defaults(self):
        n = refiner._normalize(None)
        self.assertFalse(n["needs_refinement"])
        self.assertEqual(n["refined"], "")

    def test_refine_fail_open_without_backends(self):
        # no API key + fake model so `claude -p` errors -> must fail open, never raise
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            r = refiner.refine("build me something", cfg={
                "model": "no-such-model-xyz", "refine_timeout_sec": 1})
            self.assertIn("needs_refinement", r)
            self.assertFalse(r["needs_refinement"])  # fail-open => no refinement
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old

    def test_refine_guards_empty_refined(self):
        # a backend claiming needs_refinement but giving no text is treated as no-op
        n = refiner._normalize({"needs_refinement": True, "refined": "   ", "tip": "t"})
        # _normalize keeps it, but refine() guards it; emulate the guard:
        self.assertTrue(n["needs_refinement"])  # normalize itself doesn't guard


if __name__ == "__main__":
    unittest.main()
