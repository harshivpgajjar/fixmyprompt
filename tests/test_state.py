"""Tests for the one-shot bypass + cooldown + backstop (the loop-proof core)."""
import importlib
import os
import stat
import sys
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

    def test_pending_stores_and_returns_original(self):
        self.s.set_pending("so", "REFINED", "my ORIGINAL prompt")
        got = self.s.take_pending("so")
        self.assertEqual(got["original"], "my ORIGINAL prompt")

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_state_files_are_owner_only(self):
        # the pending/backstop/cooldown files hold the user's raw prompt text —
        # they must not be world-readable on a shared host, and the runtime dir
        # is 0700 so other users can't traverse into it at all.
        self.s.set_pending("perm", "refined secret", "original secret")
        self.s.mark_coached("perm")

        def mode(p):
            return stat.S_IMODE(os.stat(p).st_mode)
        self.assertEqual(mode(self.s.PENDING_DIR / "perm.json"), 0o600)
        self.assertEqual(mode(self.s.BACKSTOP_PATH), 0o600)
        self.assertEqual(mode(self.s.COOLDOWN_DIR / "perm"), 0o600)
        import fixmyprompt.config as c
        self.assertEqual(mode(c.RUNTIME_DIR), 0o700)
        self.assertEqual(mode(self.s.PENDING_DIR), 0o700)

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_secure_write_is_atomic_no_toctou_window(self):
        # a separate write-then-chmod briefly creates the file at the default
        # umask before narrowing it — a real (if brief) exposure window on a
        # shared host. The mode must be 0600 from the very first os.open(),
        # even under a permissive umask, with no chmod call in between.
        old_umask = os.umask(0o022)
        try:
            p = self.s.PENDING_DIR
            p.mkdir(parents=True, exist_ok=True)
            target = p / "atomic.json"
            observed = []
            real_open = os.open

            def spy(path, flags, mode=0o777):
                fd = real_open(path, flags, mode)
                observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
                return fd
            os.open = spy
            try:
                self.s._secure_write(target, "secret prompt text")
            finally:
                os.open = real_open
        finally:
            os.umask(old_umask)
        self.assertEqual(observed, [0o600])  # 0600 at the OPEN call itself, not after

    def test_secure_write_tightens_a_preexisting_looser_file(self):
        p = self.s.PENDING_DIR
        p.mkdir(parents=True, exist_ok=True)
        target = p / "existing.json"
        target.write_text("old")
        os.chmod(target, 0o644)
        self.s._secure_write(target, "new secret")
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        self.assertEqual(target.read_text(), "new secret")

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
