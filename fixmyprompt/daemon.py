"""Warm-refine daemon: one long-lived subscription `claude` child behind a unix socket.

Why: a cold `claude -p` costs ~6-10s; a warm stream-json session answers in
~1.5s median on the user's subscription with NO API key (measured — see
RESEARCH-subscription-speed.md). This module holds exactly one such child and
serves refine requests over RUNTIME_DIR/refine.sock.

Design rules, in priority order:
  1. Fail-open is sacred — a caller must never hang or crash because of us.
     Every server-side error path answers {"needs_refinement": false}; the
     client-side `refine()` returns None on any trouble, fast.
  2. Zero credential handling — the child `claude` authenticates itself
     (OAuth/keychain) exactly like an interactive session. Nothing here reads,
     stores, or forwards a token or key.
  3. One child, serialized — a request that finds the child busy answers
     fail-open immediately instead of queueing forever.

Wire protocol (one request per connection):
  client sends one JSON line  {"prompt": str, "context": str (optional)}
  server sends one JSON line  {"needs_refinement": bool,
                               "mode": "explore"|"execute"|"other",
                               "refined": str, "tip": str}
  ops: {"op": "status"} -> {"ok": true, "pid": int, "turns_served": int}

CLI:  python3 -m fixmyprompt.daemon start|stop|status|run|refine <prompt...>
      (`run` serves in the foreground — handy for launchd/debugging.)
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

# ------------------------------------------------------------------ paths
# Computed live from config.RUNTIME_DIR so a config reload (tests set
# FIXMYPROMPT_HOME per-test) is always honored without reloading this module.


def socket_path() -> Path:
    return Path(config.RUNTIME_DIR) / "refine.sock"


def pid_path() -> Path:
    return Path(config.RUNTIME_DIR) / "daemon.pid"


def log_path() -> Path:
    return Path(config.RUNTIME_DIR) / "daemon.log"


def __getattr__(name: str):
    # Live module "constants": daemon.SOCKET / daemon.PID / daemon.LOG always
    # reflect the *current* config.RUNTIME_DIR.
    if name == "SOCKET":
        return socket_path()
    if name == "PID":
        return pid_path()
    if name == "LOG":
        return log_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ------------------------------------------------------------------ tunables

RECYCLE_TURNS = 50            # fresh child after this many turns (bounds context growth)
REQUEST_BUDGET_SEC = 8.0      # hard per-request budget on the daemon side
CONNECT_TIMEOUT_SEC = 0.3     # client-side connect timeout (fast to fail)
BUSY_WAIT_SEC = 0.25          # how long a request waits for the child lock before failing open
_MAX_LINE_BYTES = 1_000_000   # cap on a single protocol line

FAIL_OPEN = {"needs_refinement": False, "mode": "other", "refined": "", "tip": ""}

SYSTEM_PROMPT = (
    "You refine a developer's rough coding prompt into a sharp, send-ready one. "
    'Return ONLY strict JSON {"needs_refinement":bool,"mode":"explore"|"execute"|"other",'
    '"refined":str,"tip":str}. '
    "Preserve the user's voice and casual/Hinglish tone; never 'correct' typos. "
    "If the prompt is clearly voice-dictated and rambling (um/uh fillers, run-ons, "
    "mid-sentence self-corrections), tighten it into a crisp version keeping every "
    "real requirement and their voice — strip disfluencies, resolve self-corrections "
    "to what they landed on. That is de-rambling, not corporate-izing. "
    "Be mode-aware: NEVER refine an intentional explore/ideation prompt "
    "(return needs_refinement=false). For execute prompts add a concrete checkable "
    "done-state. NEVER include the user's personal details (name, email, etc.) in the "
    "output — the CLI leaks identity into context, so explicitly exclude it. "
    "Keep refined under ~60 words. "
    "Each user message is an INDEPENDENT prompt to evaluate; ignore all previous turns."
)


def _claude_exe() -> str:
    exe = shutil.which("claude")
    if exe:
        return exe
    # PATH may be minimal (launchd) — check the common install locations so a
    # homebrew / npm-global / ~/.local install still works.
    home = os.path.expanduser("~")
    for cand in (f"{home}/.local/bin/claude", "/opt/homebrew/bin/claude",
                 "/usr/local/bin/claude", f"{home}/.npm-global/bin/claude"):
        if os.path.exists(cand):
            return cand
    # last resort; Popen raises FileNotFoundError if absent -> fail-open answer.
    return f"{home}/.local/bin/claude"


def _claude_cmd(model: str) -> list:
    """The exact argv of the long-lived child (mirrors the proven prototype)."""
    return [
        _claude_exe(), "-p",
        "--model", model,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--tools", "",
        "--no-session-persistence",
        "--system-prompt", SYSTEM_PROMPT,
        "--verbose",  # required by the CLI for stream-json output in -p mode
    ]


def _child_env() -> dict:
    env = dict(os.environ)
    env.update({
        "MAX_THINKING_TOKENS": "0",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        # the nested claude session must never trigger fixmyprompt's own hook
        "FIXMYPROMPT_IN_REFINER": "1",
    })
    return env


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
    except Exception:
        pass


# ------------------------------------------------------------------ child JSON parsing


def _balanced_json_blocks(text: str) -> list:
    """Top-level {...} substrings with balanced, string-aware braces — so trailing
    chatter containing a brace can't corrupt the candidate (greedy `\\{.*\\}` did)."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "{":
            depth = 0
            in_str = esc = False
            j = i
            while j < n:
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[i:j + 1])
                        i = j
                        break
                j += 1
        i += 1
    return out


def _extract_json(text: str):
    """Direct parse, else each balanced {...} block (handles fences / chatter)."""
    if not text:
        return None
    for candidate in (text, *_balanced_json_blocks(text)):
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict) and "needs_refinement" in obj:
            return obj
    return None


def _normalize(obj) -> dict:
    if not isinstance(obj, dict) or "needs_refinement" not in obj:
        return dict(FAIL_OPEN)
    mode = str(obj.get("mode") or "other").strip().lower()
    if mode not in ("explore", "execute", "other"):
        mode = "other"
    out = {
        "needs_refinement": bool(obj.get("needs_refinement")),
        "mode": mode,
        "refined": str(obj.get("refined") or ""),
        "tip": str(obj.get("tip") or ""),
    }
    if out["needs_refinement"] and not out["refined"].strip():
        return dict(FAIL_OPEN)
    return out


# ------------------------------------------------------------------ the claude child


class _Timeout(Exception):
    """Per-request hard budget exhausted."""


def _pump(proc: subprocess.Popen, q: "queue.Queue") -> None:
    """Reader thread: child stdout lines -> queue; None sentinel on EOF/death."""
    try:
        for line in proc.stdout:
            q.put(line)
    except Exception:
        pass
    finally:
        q.put(None)


class _ClaudeSession:
    """Owns the one long-lived stream-json `claude` child."""

    def __init__(self, model: str):
        self.model = model
        self.proc = None
        self.q = None
        self.turns = 0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure(self) -> None:
        if not self.alive() or self.turns >= RECYCLE_TURNS:
            self.respawn()

    def respawn(self) -> None:
        self.close()
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            _claude_cmd(self.model),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=_child_env(),
        )
        self.q = queue.Queue()
        threading.Thread(target=_pump, args=(self.proc, self.q), daemon=True).start()
        self.turns = 0
        _log(f"spawned claude pid={self.proc.pid} model={self.model} "
             f"in {time.monotonic() - t0:.2f}s")

    def close(self) -> None:
        proc, self.proc, self.q = self.proc, None, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def request(self, text: str, budget: float) -> dict:
        """One user turn -> normalized refine dict.

        Raises _Timeout when the budget runs out, RuntimeError if the child
        died or errored. Callers decide the recycle/retry policy.
        """
        if not self.alive():
            raise RuntimeError("child not running")
        q = self.q
        # Drain any stale lines from a previous turn so we can't misread an
        # old event as this turn's result.
        while True:
            try:
                stale = q.get_nowait()
            except queue.Empty:
                break
            if stale is None:
                raise RuntimeError("child died")
        msg = {"type": "user",
               "message": {"role": "user",
                           "content": [{"type": "text", "text": text}]}}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            raise RuntimeError(f"write to child failed: {e}")
        deadline = time.monotonic() + budget
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Timeout()
            try:
                line = q.get(timeout=remaining)
            except queue.Empty:
                raise _Timeout()
            if line is None:
                raise RuntimeError("child died mid-request")
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict) or ev.get("type") != "result":
                continue
            self.turns += 1
            if ev.get("is_error"):
                raise RuntimeError(f"error result: {str(ev.get('result'))[:200]}")
            return _normalize(_extract_json(ev.get("result") or ""))


def _refine_via_child(session: _ClaudeSession, prompt: str, context: str) -> dict:
    """Fail-open wrapper around one child turn: timeout -> recycle; death -> one retry."""
    if context:
        text = ("Context about this user/session (for your understanding only — "
                f"never echo it):\n{context}\n\n---\nPrompt to refine:\n{prompt}")
    else:
        text = prompt
    try:
        session.ensure()
    except Exception as e:
        _log(f"spawn failed: {e}")
        return dict(FAIL_OPEN)
    try:
        return session.request(text, REQUEST_BUDGET_SEC)
    except _Timeout:
        _log(f"request exceeded {REQUEST_BUDGET_SEC}s budget — recycling child")
        try:
            session.respawn()
        except Exception as e:
            _log(f"recycle failed: {e}")
            session.close()
        return dict(FAIL_OPEN)
    except Exception as e:
        _log(f"child error ({e}) — respawning for one retry")
        try:
            session.respawn()
            return session.request(text, REQUEST_BUDGET_SEC)
        except Exception as e2:
            _log(f"retry failed: {e2}")
            session.close()  # ensure() respawns on the next request
            return dict(FAIL_OPEN)


# ------------------------------------------------------------------ server


def _read_line(sock: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf and len(buf) < _MAX_LINE_BYTES:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf.split(b"\n", 1)[0].strip()


def _reply(conn: socket.socket, payload: dict) -> None:
    conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def _handle(conn: socket.socket, session: _ClaudeSession,
            lock: threading.Lock, state: dict) -> None:
    """One connection = one request. Must never raise out; always answers."""
    try:
        conn.settimeout(2.0)  # a wedged client can't pin a handler thread
        line = _read_line(conn)
        req = {}
        if line:
            try:
                obj = json.loads(line.decode("utf-8", "replace"))
                if isinstance(obj, dict):
                    req = obj
            except Exception:
                req = {}
        if req.get("op") == "status":
            _reply(conn, {"ok": True, "pid": os.getpid(),
                          "turns_served": state["turns_served"]})
            return
        prompt = str(req.get("prompt") or "").strip()
        context = str(req.get("context") or "")
        if not prompt:
            _reply(conn, dict(FAIL_OPEN))
            return
        if not lock.acquire(timeout=BUSY_WAIT_SEC):
            _log("child busy — answered fail-open")
            _reply(conn, dict(FAIL_OPEN))
            return
        try:
            t0 = time.monotonic()
            result = _refine_via_child(session, prompt, context)
            state["turns_served"] += 1
            _log(f"served turn {state['turns_served']} in "
                 f"{time.monotonic() - t0:.2f}s "
                 f"(needs_refinement={result.get('needs_refinement')})")
        finally:
            lock.release()
        _reply(conn, result)
    except Exception as e:
        _log(f"request failed: {e}")
        try:
            _reply(conn, dict(FAIL_OPEN))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _serve() -> None:
    """Bind the socket and serve forever. Runs in the daemonized grandchild
    (via start()) or in the foreground (via `python3 -m fixmyprompt.daemon run`)."""
    cfg = config.load()
    rt = Path(config.RUNTIME_DIR)
    rt.mkdir(parents=True, exist_ok=True)
    # Owner-only on the dir that holds the refine socket: on macOS, AF_UNIX
    # connect permission is governed by the directory, so this (with the socket's
    # own 0600) keeps other local users off the daemon (and off your quota).
    try:
        os.chmod(rt, 0o700)
    except OSError:
        pass
    sp = socket_path()
    if sp.exists():
        # live daemon already on this socket? then this instance must bow out
        # WITHOUT touching the other's files.
        if _request({"op": "status"}, timeout=0.5) is not None:
            _log("another daemon is already serving; exiting")
            return
        try:
            sp.unlink()
        except OSError:
            pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sp))
    except OSError as e:
        _log(f"cannot bind {sp}: {e}")
        return
    os.chmod(sp, 0o600)
    srv.listen(8)
    try:
        pid_path().write_text(str(os.getpid()) + "\n")
    except Exception as e:
        _log(f"could not write pid file: {e}")

    session = _ClaudeSession(str(cfg.get("model") or "claude-haiku-4-5"))
    lock = threading.Lock()
    state = {"turns_served": 0}

    def _on_term(signum, frame):
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except ValueError:
        pass  # not the main thread — cleanup still runs via finally

    try:
        session.ensure()  # pre-warm so the first request is fast
    except Exception as e:
        _log(f"pre-warm spawn failed (will retry per request): {e}")
    _log(f"listening on {sp} (pid {os.getpid()})")
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError as e:
                _log(f"accept failed: {e}")
                break
            threading.Thread(target=_handle, args=(conn, session, lock, state),
                             daemon=True).start()
    except (SystemExit, KeyboardInterrupt):
        _log("shutting down")
    finally:
        try:
            srv.close()
        except Exception:
            pass
        session.close()
        try:
            sp.unlink()
        except OSError:
            pass
        try:  # remove the pid file only if it is ours
            if pid_path().read_text().strip() == str(os.getpid()):
                pid_path().unlink()
        except Exception:
            pass


# ------------------------------------------------------------------ client


def _request(payload: dict, timeout: float):
    """One JSON line out, one JSON line back. None on ANY failure, within
    `timeout` seconds total (connect capped at CONNECT_TIMEOUT_SEC)."""
    try:
        timeout = max(float(timeout), 0.05)
    except Exception:
        timeout = 2.0
    deadline = time.monotonic() + timeout
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(min(CONNECT_TIMEOUT_SEC, timeout))
        s.connect(str(socket_path()))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        s.settimeout(remaining)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf and len(buf) < _MAX_LINE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            s.settimeout(remaining)
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0].strip()
        if not line:
            return None
        obj = json.loads(line.decode("utf-8", "replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def refine(prompt: str, timeout: float = 2.0, context: str = ""):
    """Hook/refiner-side entry point. Returns the daemon's refine dict
    ({needs_refinement, mode, refined, tip}) or None if the daemon is down,
    busy at the wire level, slow, or answers garbage. NEVER raises; total
    wall time is bounded by `timeout` (connect fails within ~0.3s)."""
    try:
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        payload = {"prompt": prompt}
        if context:
            payload["context"] = str(context)
        resp = _request(payload, timeout)
        if isinstance(resp, dict) and "needs_refinement" in resp:
            return resp
        return None
    except Exception:
        return None


# ------------------------------------------------------------------ lifecycle


def is_running() -> bool:
    try:
        pid = int(pid_path().read_text().strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return socket_path().exists()


def status() -> dict:
    pid = None
    try:
        pid = int(pid_path().read_text().strip())
    except Exception:
        pass
    running = is_running()
    st = {"running": running,
          "pid": pid if running else None,
          "socket": str(socket_path()),
          "turns_served": 0}
    if running:
        resp = _request({"op": "status"}, timeout=1.0)
        if isinstance(resp, dict):
            try:
                st["turns_served"] = int(resp.get("turns_served") or 0)
            except Exception:
                pass
    return st


def _remove_runtime_files() -> None:
    for p in (socket_path(), pid_path()):
        try:
            p.unlink()
        except OSError:
            pass


def start() -> bool:
    """Daemonize (double-fork + setsid) and serve. No-op if already running.
    Returns True once a daemon is confirmed up (socket present)."""
    if is_running():
        return True
    _remove_runtime_files()  # stale leftovers from a crashed instance
    rt = Path(config.RUNTIME_DIR)
    rt.mkdir(parents=True, exist_ok=True)
    pid = os.fork()
    if pid > 0:  # original process: reap the intermediate child, wait for the socket
        try:
            os.waitpid(pid, 0)
        except Exception:
            pass
        for _ in range(60):  # up to ~3s for bind (child boot continues async)
            if socket_path().exists():
                return True
            time.sleep(0.05)
        return is_running()
    # ---- intermediate child
    try:
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
        # ---- grandchild: the daemon proper
        os.chdir("/")
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.close(devnull)
        logfd = os.open(str(log_path()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(logfd, 1)
        os.dup2(logfd, 2)
        os.close(logfd)
        sys.stdout = os.fdopen(1, "w", buffering=1)
        sys.stderr = os.fdopen(2, "w", buffering=1)
        _serve()
    except Exception:
        pass
    finally:
        os._exit(0)


def stop() -> bool:
    """Terminate the daemon if one is running; always clean up socket + pid.
    Safe no-op when nothing runs. Never raises. Returns True if a process
    was signaled."""
    signaled = False
    pid = None
    try:
        pid = int(pid_path().read_text().strip())
    except Exception:
        pid = None
    if pid and pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
            signaled = True
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                os.kill(pid, 0)  # raises ProcessLookupError once it exits
                time.sleep(0.1)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass  # already gone (or not ours) — cleanup below either way
    _remove_runtime_files()
    return signaled


# ------------------------------------------------------------------ CLI

_USAGE = "usage: python3 -m fixmyprompt.daemon start|stop|status|run|refine <prompt...>"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    if cmd == "start":
        ok = start()
        print(json.dumps(status()))
        return 0 if ok else 1
    if cmd == "stop":
        stop()
        print(json.dumps(status()))
        return 0
    if cmd == "status":
        st = status()
        print(json.dumps(st))
        return 0 if st["running"] else 1
    if cmd == "run":  # foreground server (debugging / launchd KeepAlive)
        _serve()
        return 0
    if cmd == "refine":
        prompt = " ".join(args[1:]).strip()
        if not prompt and not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        out = refine(prompt, timeout=10.0)
        if out is None:
            print(json.dumps(dict(FAIL_OPEN)))
            return 2
        print(json.dumps(out))
        return 0
    print(_USAGE, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
