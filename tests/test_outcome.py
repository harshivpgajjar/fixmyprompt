"""Tests for outcome tracking — does coaching reduce follow-up corrections?"""
import json
import os
import tempfile
import time
import unittest


def _rec(preview="", ts=0.0, action="pass", wc=10, cont=False, sid=None, **kw):
    base = dict(
        ts=ts, action=action, word_count=wc, mode="execute",
        is_continuation=cont, has_constraints=False, has_done_criteria=False,
        has_reference=False, is_design=False, quality=0.5, gaps=[],
        preview=preview, session_id=sid, cwd="/tmp/proj",
    )
    base.update(kw)
    return base


class OutcomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["WHETSTONE_HOME"] = self.tmp
        # import fresh so RUNTIME_DIR picks up the temp home
        import importlib
        import whetstone.config as c
        importlib.reload(c)
        import whetstone.scorelog as sl
        import whetstone.outcome as oc
        importlib.reload(sl)
        importlib.reload(oc)
        self.sl = sl
        self.oc = oc

    def tearDown(self):
        os.environ.pop("WHETSTONE_HOME", None)

    def _write(self, records):
        self.sl.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self.sl.LOG_PATH.open("a") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    # ------------------------------------------------------------------
    # is_correction
    # ------------------------------------------------------------------

    def test_is_correction_positive(self):
        for p in (
            "no, I meant the signup page",
            "actually make it blue",
            "still broken",
            "that's not what I wanted",
        ):
            self.assertTrue(self.oc.is_correction(_rec(preview=p, wc=5)), p)

    def test_is_correction_negative(self):
        build = _rec(
            preview="build the settings page with save and cancel buttons and loading states",
            wc=12,
        )
        explore = _rec(
            preview="give me five bold hero directions for the landing page",
            wc=10, mode="explore",
        )
        cont = _rec(preview="yes", wc=1, cont=True)
        for r in (build, explore, cont):
            self.assertFalse(self.oc.is_correction(r), r["preview"])

    def test_is_correction_short_soon_heuristic(self):
        short = _rec(preview="make the header sticky", wc=4)
        # timing verdict comes from the caller: no timing -> not a correction
        self.assertFalse(self.oc.is_correction(short))
        self.assertTrue(self.oc.is_correction(short, soon_after_prev=True))
        # continuations and long prompts are never rapid re-tries
        cont = _rec(preview="yes go on", wc=3, cont=True)
        self.assertFalse(self.oc.is_correction(cont, soon_after_prev=True))
        long_ = _rec(
            preview="add pagination to the results table with page size options",
            wc=10,
        )
        self.assertFalse(self.oc.is_correction(long_, soon_after_prev=True))

    def test_is_correction_never_crashes(self):
        self.assertFalse(self.oc.is_correction({}))
        self.assertFalse(self.oc.is_correction({"preview": None}))
        self.assertFalse(
            self.oc.is_correction(_rec(preview="[redacted: possible secret]", wc=20))
        )

    # ------------------------------------------------------------------
    # analyze
    # ------------------------------------------------------------------

    def test_analyze_counts_and_rates(self):
        recs = [
            # session s1: coached trigger -> corrected; uncoached trigger ->
            # corrected; uncoached trigger -> clean.
            _rec("build the login page with oauth and error states",
                 ts=1000, action="coach", wc=9, sid="s1"),
            _rec("no, I meant the signup page",
                 ts=1100, action="pass", wc=6, sid="s1"),
            _rec("add a footer with contact details and social links",
                 ts=5000, action="pass", wc=9, sid="s1"),
            _rec("that's not what I wanted",
                 ts=5200, action="pass", wc=5, sid="s1"),
            _rec("write unit tests for the auth module and run them",
                 ts=9000, action="pass", wc=10, sid="s1"),
            # session s2: accepted-rewrite trigger -> clean (continuation and a
            # later short prompt outside the 120s re-try window don't count).
            _rec("redesign the pricing page with three clear tiers",
                 ts=1000, action="accept", wc=8, sid="s2"),
            _rec("yes", ts=1200, action="pass", wc=1, cont=True, sid="s2"),
            _rec("run the full test suite",
                 ts=1400, action="pass", wc=5, sid="s2"),
            # backfill records without session ids — must be ignored entirely
            _rec("build the admin panel with role based access control",
                 ts=1000, action="coach", wc=9, sid=None),
            _rec("no, I meant the customer panel",
                 ts=1050, action="pass", wc=6, sid=None),
        ]
        out = self.oc.analyze(recs)
        self.assertEqual(out["coached_n"], 2)
        self.assertEqual(out["coached_corrected"], 1)
        self.assertAlmostEqual(out["coached_rate"], 0.5)
        self.assertEqual(out["uncoached_n"], 3)
        self.assertEqual(out["uncoached_corrected"], 1)
        self.assertAlmostEqual(out["uncoached_rate"], 1 / 3)
        self.assertEqual(out["sessions_analyzed"], 2)

    def test_late_or_distant_corrections_do_not_count(self):
        # correction is the 4th prompt after the trigger -> beyond K=3
        far = [
            _rec("build a dashboard with charts and filters for sales",
                 ts=100, action="coach", wc=9, sid="a"),
            _rec("keep going", ts=300, wc=2, cont=True, sid="a"),
            _rec("keep going", ts=600, wc=2, cont=True, sid="a"),
            _rec("keep going", ts=900, wc=2, cont=True, sid="a"),
            _rec("actually make it blue", ts=1000, action="pass", wc=4, sid="a"),
        ]
        out = self.oc.analyze(far)
        self.assertEqual(out["coached_n"], 1)
        self.assertEqual(out["coached_corrected"], 0)
        # correction 16 minutes later -> outside the 15-minute window
        late = [
            _rec("refactor the payment service into smaller focused modules",
                 ts=100, action="pass", wc=8, sid="b"),
            _rec("still broken", ts=100 + 16 * 60, action="pass", wc=2, sid="b"),
        ]
        out = self.oc.analyze(late)
        self.assertEqual(out["uncoached_n"], 1)
        self.assertEqual(out["uncoached_corrected"], 0)

    def test_analyze_ignores_none_sessions_and_empty(self):
        out = self.oc.analyze([])
        self.assertEqual(out["coached_n"], 0)
        self.assertEqual(out["uncoached_n"], 0)
        self.assertIsNone(out["coached_rate"])
        self.assertIsNone(out["uncoached_rate"])
        self.assertEqual(out["sessions_analyzed"], 0)
        recs = [
            _rec("build the admin panel with role based access",
                 ts=10, action="coach", wc=8, sid=None),
            _rec("no, I meant the customer panel",
                 ts=60, action="pass", wc=6, sid=None),
        ]
        out = self.oc.analyze(recs)
        self.assertEqual(out["coached_n"], 0)
        self.assertEqual(out["uncoached_n"], 0)
        self.assertEqual(out["sessions_analyzed"], 0)

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------

    def test_summary_with_enough_data(self):
        now = time.time()
        recs = []
        # 5 coached triggers, 1 followed by a correction -> 20%
        for k in range(5):
            t = now - 86400 + k * 2000
            sid = f"c{k}"
            recs.append(_rec(f"build feature number {k} with tests and docs",
                             ts=t, action="coach", wc=9, sid=sid))
            if k == 0:
                recs.append(_rec("no, I meant the signup page",
                                 ts=t + 60, action="pass", wc=6, sid=sid))
        # 5 uncoached triggers, 3 followed by a correction -> 60%
        for k in range(5):
            t = now - 43200 + k * 2000
            sid = f"u{k}"
            recs.append(_rec(f"ship feature number {k} with tests and docs",
                             ts=t, action="pass", wc=9, sid=sid))
            if k < 3:
                recs.append(_rec("that's not what I wanted",
                                 ts=t + 60, action="pass", wc=5, sid=sid))
        self._write(recs)
        out = self.oc.summary(days=30)
        self.assertIn("20%", out)
        self.assertIn("60%", out)
        self.assertIn("N=5", out)
        self.assertIn("1/5", out)
        self.assertIn("3/5", out)
        self.assertNotIn("Not enough", out)

    def test_summary_sparse_data_is_honest(self):
        now = time.time()
        recs = [
            _rec("build the onboarding flow with three steps and tests",
                 ts=now - 3600, action="coach", wc=9, sid="s1"),
            _rec("build the billing page with stripe and error states",
                 ts=now - 1800, action="pass", wc=9, sid="s2"),
        ]
        self._write(recs)
        out = self.oc.summary(days=30)
        self.assertIn("Not enough", out)
        self.assertIn("1 coached / 1 uncoached", out)
        self.assertNotIn("%", out.split("Not enough")[1].split("So far")[0])

    def test_summary_empty_is_graceful(self):
        out = self.oc.summary(days=30)
        self.assertIsInstance(out, str)
        self.assertIn("Not enough", out)


if __name__ == "__main__":
    unittest.main()
