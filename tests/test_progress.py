"""Tests for the progress tracker (sparkline, streaks, daily rates, progress()).

Mirrors test_report.py's temp-WHETSTONE_HOME + reload pattern. Records are
seeded at local *midday* of each target day so the rolling epoch windows in
scorelog.read/progress and the calendar-day bucketing never disagree at the
boundaries, regardless of what time of day the suite runs.
"""
import importlib
import json
import os
import tempfile
import unittest
from datetime import date, datetime, time as dtime, timedelta

BLOCKS = "▁▂▃▄▅▆▇█"


def _ts(days_ago: int) -> float:
    d = date.today() - timedelta(days=days_ago)
    return datetime.combine(d, dtime(12, 0)).timestamp()


def _rec(days_ago, action="pass", mode="execute", wc=15, cont=False,
         constraints=True, done=True, ref=False, design=False,
         quality=0.9, gaps=None):
    return {
        "ts": _ts(days_ago), "action": action, "word_count": wc,
        "mode": mode, "is_continuation": cont, "has_constraints": constraints,
        "has_done_criteria": done, "has_reference": ref, "is_design": design,
        "quality": quality, "gaps": gaps or [], "preview": "seeded",
    }


def _bad(days_ago, gaps=None, **kw):
    """A substantive execute prompt that is NOT self-sufficient."""
    return _rec(days_ago, action="coach", constraints=False, done=False,
                quality=0.3, gaps=gaps or ["no acceptance criteria"], **kw)


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["WHETSTONE_HOME"] = self.tmp
        # import fresh so RUNTIME_DIR picks up the temp home
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

    def _seed(self, records):
        self.sl.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self.sl.LOG_PATH.open("a") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    # ---------------- sparkline (pure) ----------------

    def test_sparkline_maps_values_to_blocks(self):
        self.assertEqual(self.rp.sparkline([0.0]), "▁")
        self.assertEqual(self.rp.sparkline([1.0]), "█")
        self.assertEqual(self.rp.sparkline([0.5]), "▅")
        # a full ramp hits every block exactly once
        self.assertEqual(self.rp.sparkline([i / 7 for i in range(8)]), BLOCKS)

    def test_sparkline_empty_and_none_and_clamping(self):
        self.assertEqual(self.rp.sparkline([]), "")
        self.assertEqual(self.rp.sparkline([None]), "·")
        self.assertEqual(self.rp.sparkline([1.5, -0.2]), "█▁")

    # ---------------- streak_info (pure) ----------------

    def test_streak_info_empty(self):
        self.assertEqual(self.rp.streak_info([]), {"current": 0, "best": 0})

    def test_streak_info_counts_and_breaks(self):
        recs = (
            [_rec(5), _rec(4), _rec(3)]      # 3 good days
            + [_rec(2), _bad(2)]             # day 2 has one failure -> breaks
            + [_rec(1), _rec(0)]             # 2 good days since
        )
        self.assertEqual(self.rp.streak_info(recs), {"current": 2, "best": 3})

    def test_streak_info_current_zero_when_latest_day_bad(self):
        recs = [_rec(2), _rec(1), _bad(0)]
        self.assertEqual(self.rp.streak_info(recs), {"current": 0, "best": 2})

    def test_streak_info_ignores_non_execute_and_tiny_prompts(self):
        recs = [
            _rec(1),
            _bad(0, mode="other"),          # not execute -> ignored
            _bad(0, wc=3),                  # not substantive -> ignored
        ]
        self.assertEqual(self.rp.streak_info(recs), {"current": 1, "best": 1})

    # ---------------- daily_rates (pure) ----------------

    def test_daily_rates_buckets_and_rates(self):
        recs = [_rec(2), _rec(2), _bad(2), _bad(2), _rec(0)]  # day1 empty
        out = self.rp.daily_rates(recs, 3)
        self.assertEqual(len(out), 3)
        today = date.today()
        self.assertEqual(out[0], ((today - timedelta(days=2)).isoformat(), 0.5))
        self.assertEqual(out[1], ((today - timedelta(days=1)).isoformat(), None))
        self.assertEqual(out[2], (today.isoformat(), 1.0))

    def test_daily_rates_explore_counts_other_excluded(self):
        recs = [
            _rec(0, mode="explore", done=False, constraints=False),  # self-sufficient
            _bad(0),
            _bad(0, mode="other"),  # excluded entirely
        ]
        out = self.rp.daily_rates(recs, 1)
        self.assertEqual(out[0][1], 0.5)

    # ---------------- progress() ----------------

    def _seed_two_weeks(self):
        this_week = (
            [_rec(d) for d in (0, 1, 2, 3, 4, 5)]        # 6 good execute
            + [_bad(1), _bad(3)]                          # 2 coached execute
            + [_rec(2, mode="explore", done=False, constraints=False,
                    action="accept")]                     # 1 explore, accepted
        )
        # previous week seeded on days 8-12 (day 7 skipped: rolling-window buffer)
        prev_week = (
            [_rec(8), _rec(10)]
            + [_bad(d, gaps=["no acceptance criteria", "missing constraints"])
               for d in (9, 10, 11, 12)]
        )
        self._seed(this_week + prev_week)

    def test_progress_week_contains_all_sections(self):
        self._seed_two_weeks()
        out = self.rp.progress("week")
        # headline: 7/9 self-sufficient this week vs 2/6 previous -> 78%, +44 pts, up
        self.assertIn("Execute-mode self-sufficiency: 78%", out)
        self.assertIn("↑", out)
        self.assertIn("+44 pts", out)
        # sparkline present with real block chars and endpoint date labels
        self.assertTrue(any(ch in out for ch in BLOCKS))
        self.assertIn(date.today().strftime("%b %d"), out)
        # streak + volume + gaps + axis sections
        self.assertIn("Streak:", out)
        self.assertIn("9 substantive prompts", out)
        self.assertIn("8 execute / 1 explore", out)
        self.assertIn("coach fired 2×", out)
        self.assertIn("accepted 1×", out)
        self.assertIn("no acceptance criteria", out)
        self.assertIn("▼", out)  # gap fell 4 -> 2 vs previous week
        self.assertIn("Most improved:", out)
        # scannable: stays within ~30 lines
        self.assertLessEqual(len(out.splitlines()), 30)

    def test_progress_all_periods_run(self):
        self._seed_two_weeks()
        for period in ("day", "week", "month"):
            out = self.rp.progress(period)
            self.assertIsInstance(out, str)
            self.assertIn("Whetstone — prompt progress", out)

    def test_progress_rejects_unknown_period(self):
        with self.assertRaises(ValueError):
            self.rp.progress("year")

    def test_progress_empty_is_graceful(self):
        out = self.rp.progress("week")
        self.assertIn("Not enough data yet, keep going", out)

    def test_progress_no_previous_period_data(self):
        self._seed([_rec(0), _rec(1)])
        out = self.rp.progress("week")
        self.assertIn("Execute-mode self-sufficiency: 100%", out)
        self.assertIn("no previous-week data", out)

    # ---------------- summarize() regression ----------------

    def _feat(self, **kw):
        base = dict(
            word_count=15, mode="execute", is_continuation=False,
            has_constraints=True, has_done_criteria=True, has_reference=False,
            is_design=False, quality=0.9, gaps=[],
        )
        base.update(kw)
        return base

    def test_summarize_still_works(self):
        for _ in range(6):
            self.sl.log("build the thing so that tests pass and it is done",
                        self._feat(), "pass")
        for _ in range(4):
            self.sl.log("fix the mobile version", self._feat(
                word_count=15, has_done_criteria=False, has_constraints=False,
                quality=0.3, gaps=["no acceptance criteria"]), "coach")
        out = self.rp.summarize(days=7)
        self.assertIn("## Prompting (Whetstone)", out)
        self.assertIn("self-sufficiency", out)
        self.assertIn("no acceptance criteria", out)

    def test_summarize_empty_still_graceful(self):
        out = self.rp.summarize(days=7)
        self.assertIn("No substantive prompts", out)


if __name__ == "__main__":
    unittest.main()
