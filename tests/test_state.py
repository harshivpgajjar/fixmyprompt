"""Tests for the one-shot bypass + cooldown + backstop (the loop-proof core)."""
import importlib
import os
import tempfile
import unittest


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["FIXMYPROMPT_HOME"] = self.tmp
        import fixmyprompt.config as c
        importlib.reload(c)
        import fixmyprompt.state as s
        importlib.reload(s)
        self.s = s

    def tearDown(self):
        os.environ.pop("FIXMYPROMPT_HOME", None)

    def test_pending_is_one_shot(self):
        self.s.set_pending("sess1", "refined text")
        first = self.s.take_pending("sess1")
        self.assertIsNotNone(first)
        self.assertEqual(first["refined"], "refined text")
        # consumed — a second take must be None (this is what makes double-block impossible)
        self.assertIsNone(self.s.take_pending("sess1"))

    def test_pending_expires(self):
        self.s.set_pending("sess2", "x")
        self.assertIsNone(self.s.take_pending("sess2", ttl=-1))

    def test_pending_is_session_scoped(self):
        self.s.set_pending("a", "ra")
        self.assertIsNone(self.s.take_pending("b"))
        self.assertIsNotNone(self.s.take_pending("a"))

    def test_cooldown(self):
        cfg = {"cooldown_sec": 100}
        self.assertFalse(self.s.cooldown_active("s", cfg))
        self.s.mark_coached("s")
        self.assertTrue(self.s.cooldown_active("s", cfg))
        self.assertFalse(self.s.cooldown_active("s", {"cooldown_sec": 0}))

    def test_backstop_overlap(self):
        self.assertGreater(self.s.token_overlap("build a red button", "build a red button now"), 0.6)
        self.assertLess(self.s.token_overlap("hello world", "totally different text here"), 0.6)

    def test_session_id_sanitized(self):
        # a hostile session id must not escape the pending dir
        self.s.set_pending("../../etc/passwd", "safe")
        got = self.s.take_pending("../../etc/passwd")
        self.assertEqual(got["refined"], "safe")


if __name__ == "__main__":
    unittest.main()
