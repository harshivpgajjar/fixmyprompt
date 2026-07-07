"""Tests for the warm-refine daemon (fixmyprompt/daemon.py).

No test spawns a real `claude` — CI/sandbox has none. Protocol behavior is
exercised against fake unix-socket servers speaking the daemon's line protocol.
The load-bearing property under test: refine() FAILS OPEN, fast, always.
"""
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

CANNED = {
    "needs_refinement": True,
    "mode": "execute",
    "refined": "Add retry with backoff to fetch_user(); done when test_retry passes.",
    "tip": "Name a checkable done-state.",
}


@unittest.skipUnless(hasattr(socket, "AF_UNIX"),
                     "the warm daemon uses AF_UNIX sockets — macOS/Linux only")
class DaemonTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fixmyprompt-test-")
        os.environ["FIXMYPROMPT_HOME"] = self.tmp
        import fixmyprompt.config as c
        importlib.reload(c)
        import fixmyprompt.daemon as d
        importlib.reload(d)
        self.d = d
        self._servers = []

    def tearDown(self):
        for srv in self._servers:
            try:
                srv.close()
            except Exception:
                pass
        os.environ.pop("FIXMYPROMPT_HOME", None)

    # -- fake-server plumbing -------------------------------------------------

    def start_fake_server(self, handler):
        """Accept-loop in a daemon thread; `handler(conn)` per connection."""
        sp = self.d.socket_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sp))
        srv.listen(4)
        self._servers.append(srv)

        def loop():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                try:
                    handler(conn)
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

        threading.Thread(target=loop, daemon=True).start()
        return srv

    @staticmethod
    def read_request_line(conn):
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\n", 1)[0]


class PathsTest(DaemonTestBase):
    def test_runtime_files_land_under_fixmyprompt_home_not_tmp(self):
        home = Path(self.tmp).resolve()
        for p in (self.d.socket_path(), self.d.pid_path(), self.d.log_path()):
            self.assertEqual(Path(p).resolve().parent, home,
                             f"{p} not directly under FIXMYPROMPT_HOME")
        # live module-level aliases too
        self.assertEqual(Path(self.d.SOCKET), self.d.socket_path())
        self.assertEqual(Path(self.d.PID), self.d.pid_path())
        self.assertEqual(Path(self.d.LOG), self.d.log_path())
        self.assertEqual(self.d.socket_path().name, "refine.sock")


class FailOpenClientTest(DaemonTestBase):
    def test_refine_returns_none_fast_when_no_daemon(self):
        """THE load-bearing behavior: daemon off => None, quickly, no raise."""
        t0 = time.monotonic()
        result = self.d.refine("make the login page less ugly pls")
        elapsed = time.monotonic() - t0
        self.assertIsNone(result)
        self.assertLess(elapsed, 1.0, f"fail-open took {elapsed:.2f}s")

    def test_refine_none_on_stale_socket_file(self):
        # daemon crashed leaving the socket file behind: connect is refused
        sp = self.d.socket_path()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sp))
        srv.close()  # file remains, nobody listening
        self.assertTrue(sp.exists())
        t0 = time.monotonic()
        self.assertIsNone(self.d.refine("hello"))
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_refine_none_on_empty_or_non_string_prompt(self):
        self.assertIsNone(self.d.refine(""))
        self.assertIsNone(self.d.refine("   "))
        self.assertIsNone(self.d.refine(None))


class LifecycleTest(DaemonTestBase):
    def test_status_and_is_running_clean_on_fresh_home(self):
        self.assertFalse(self.d.is_running())
        st = self.d.status()
        self.assertEqual(st["running"], False)
        self.assertIsNone(st["pid"])
        self.assertEqual(st["turns_served"], 0)
        self.assertTrue(st["socket"].startswith(self.tmp))

    def test_stop_when_nothing_runs_is_safe_noop(self):
        # must not raise, must not signal anything
        self.assertFalse(self.d.stop())
        self.assertFalse(self.d.is_running())

    def test_stop_cleans_stale_pid_and_socket(self):
        # a genuinely dead pid (spawned, exited, reaped)
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.d.pid_path().write_text(str(p.pid) + "\n")
        self.d.socket_path().touch()
        self.assertFalse(self.d.is_running())  # dead pid => not running
        self.assertFalse(self.d.stop())        # nothing signaled...
        self.assertFalse(self.d.pid_path().exists())   # ...but files cleaned
        self.assertFalse(self.d.socket_path().exists())


class ProtocolTest(DaemonTestBase):
    def test_refine_roundtrip_against_fake_daemon(self):
        received = []

        def handler(conn):
            line = self.read_request_line(conn)
            received.append(json.loads(line))
            conn.sendall((json.dumps(CANNED) + "\n").encode())

        self.start_fake_server(handler)
        out = self.d.refine("fix the flaky test", timeout=2.0, context="repo: fixmyprompt")
        self.assertEqual(out, CANNED)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["prompt"], "fix the flaky test")
        self.assertEqual(received[0]["context"], "repo: fixmyprompt")

    def test_refine_times_out_on_hanging_server(self):
        def handler(conn):
            self.read_request_line(conn)  # accept the request...
            time.sleep(5)                 # ...then never answer

        self.start_fake_server(handler)
        t0 = time.monotonic()
        out = self.d.refine("anything", timeout=0.8)
        elapsed = time.monotonic() - t0
        self.assertIsNone(out)
        self.assertLess(elapsed, 2.0, f"timeout overshot: {elapsed:.2f}s")

    def test_refine_none_on_garbage_response(self):
        def handler(conn):
            self.read_request_line(conn)
            conn.sendall(b"this is not json\n")

        self.start_fake_server(handler)
        self.assertIsNone(self.d.refine("anything", timeout=2.0))

    def test_refine_none_on_json_without_contract_key(self):
        def handler(conn):
            self.read_request_line(conn)
            conn.sendall(b'{"ok": true}\n')  # dict but not a refine answer

        self.start_fake_server(handler)
        self.assertIsNone(self.d.refine("anything", timeout=2.0))

    def test_status_reports_turns_via_socket(self):
        def handler(conn):
            req = json.loads(self.read_request_line(conn))
            if req.get("op") == "status":
                conn.sendall((json.dumps(
                    {"ok": True, "pid": os.getpid(), "turns_served": 7}) + "\n").encode())

        self.start_fake_server(handler)
        # a live pid (our own) + live socket => running; NEVER call stop() here
        self.d.pid_path().write_text(str(os.getpid()) + "\n")
        self.assertTrue(self.d.is_running())
        st = self.d.status()
        self.assertTrue(st["running"])
        self.assertEqual(st["pid"], os.getpid())
        self.assertEqual(st["turns_served"], 7)
        self.d.pid_path().unlink()  # so tearDown/other asserts see a clean home


if __name__ == "__main__":
    unittest.main()
