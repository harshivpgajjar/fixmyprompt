"""Tests for the weekly report + scorelog round-trip (privacy + trend math)."""
import json
import os
import tempfile
import unittest
from pathlib import Path


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["WHETSTONE_HOME"] = self.tmp
        # import fresh so RUNTIME_DIR picks up the temp home
        import importlib
        import whetstone.config as c
        importlib.reload(c)
        import whetstone.scorelog as sl
        import whetstone.report as rp
        importlib.reload(sl)
        importlib.reload(rp)
        self.sl = sl
        self.rp = rp

    def tearDown(self):
        os.environ.pop("WHETSTONE_HOME", None)

    def _feat(self, **kw):
        base = dict(
            word_count=15, mode="execute", is_continuation=False,
            has_constraints=True, has_done_criteria=True, has_reference=False,
            is_design=False, quality=0.9, gaps=[],
        )
        base.update(kw)
        return base

    def test_secret_is_redacted_in_preview(self):
        self.sl.log("my key is sk-ABCD1234EFGH5678 use it", self._feat(), "pass")
        rec = self.sl.read()[-1]
        self.assertNotIn("sk-ABCD1234", rec["preview"])
        self.assertIn("redacted", rec["preview"])

    def test_report_counts_and_trend(self):
        for _ in range(6):
            self.sl.log("build the thing so that tests pass and it is done", self._feat(), "pass")
        for _ in range(4):
            self.sl.log("fix the mobile version", self._feat(
                word_count=15, has_done_criteria=False, has_constraints=False,
                quality=0.3, gaps=["no acceptance criteria"]), "coach")
        out = self.rp.summarize(days=7)
        self.assertIn("Prompting (Whetstone)", out)
        self.assertIn("self-sufficiency", out)
        self.assertIn("no acceptance criteria", out)

    def test_empty_report_is_graceful(self):
        out = self.rp.summarize(days=7)
        self.assertIn("No substantive prompts", out)

    def test_explore_counts_as_self_sufficient(self):
        for _ in range(3):
            self.sl.log("blow me away with five hero directions please", self._feat(
                mode="explore", has_done_criteria=False, has_constraints=False,
                quality=1.0, gaps=[]), "pass")
        out = self.rp.summarize(days=7)
        self.assertIn("100%", out)


if __name__ == "__main__":
    unittest.main()
