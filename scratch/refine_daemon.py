#!/usr/bin/env python3
"""Whetstone refine daemon (prototype).

Holds ONE long-lived `claude -p --input-format stream-json` process (subscription
OAuth, no API key) and serves refine requests over a unix socket. Per-request
latency is pure inference (~1-1.5s on Haiku), not the ~6-10s CLI boot.

Protocol: client connects, sends UTF-8 prompt terminated by EOF (shutdown write),
daemon replies with refined text, closes. One request per connection.

Hygiene:
- Session recycled after MAX_TURNS to bound context growth / contamination.
- claude process auto-respawned if it dies.
- Stale socket cleaned up on start.

Run:  python3 refine_daemon.py [--socket /tmp/whetstone-refine.sock]
"""
import json, os, socket, subprocess, sys, time

CLAUDE = os.path.expanduser("~/.local/bin/claude")
SOCK_PATH = sys.argv[sys.argv.index("--socket") + 1] if "--socket" in sys.argv else "/tmp/whetstone-refine.sock"
MAX_TURNS = 50  # recycle session after this many refines
SYSTEM = ("You rewrite rough prompts into sharp, specific ones. "
          "Each user message is an INDEPENDENT prompt to rewrite; ignore all previous turns. "
          "Reply with ONLY the rewritten prompt, max 60 words.")

CMD = [
    CLAUDE, "-p",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--model", "claude-haiku-4-5",
    "--strict-mcp-config",
    "--setting-sources", "",
    "--tools", "",
    "--no-session-persistence",
    "--system-prompt", SYSTEM,
    "--verbose",
]

ENV = dict(os.environ)
ENV.update({"MAX_THINKING_TOKENS": "0", "DISABLE_TELEMETRY": "1",
            "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})


class ClaudeSession:
    def __init__(self):
        self.proc = None
        self.turns = 0

    def ensure(self):
        if self.proc is None or self.proc.poll() is not None or self.turns >= MAX_TURNS:
            self.respawn()

    def respawn(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        t0 = time.monotonic()
        self.proc = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, env=ENV)
        self.turns = 0
        print(f"[daemon] spawned claude pid={self.proc.pid} in {time.monotonic()-t0:.2f}s", flush=True)

    def refine(self, prompt: str) -> str:
        self.ensure()
        msg = {"type": "user",
               "message": {"role": "user",
                            "content": [{"type": "text", "text": prompt}]}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("claude process died mid-request")
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                self.turns += 1
                if ev.get("is_error"):
                    raise RuntimeError(f"claude error result: {ev.get('result')!r}")
                return ev.get("result") or ""
        raise TimeoutError("no result within 30s")


def main():
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)
    srv.listen(4)
    session = ClaudeSession()
    session.ensure()  # pre-warm at startup
    print(f"[daemon] listening on {SOCK_PATH}", flush=True)
    while True:
        conn, _ = srv.accept()
        try:
            chunks = []
            while True:
                b = conn.recv(65536)
                if not b:
                    break
                chunks.append(b)
            prompt = b"".join(chunks).decode("utf-8").strip()
            if not prompt:
                conn.sendall(b"")
                continue
            if prompt == "__PING__":
                conn.sendall(b"PONG")
                continue
            t0 = time.monotonic()
            try:
                out = session.refine(prompt)
            except Exception as e:
                # one retry on a fresh process
                print(f"[daemon] error ({e}); respawning and retrying", flush=True)
                session.respawn()
                out = session.refine(prompt)
            print(f"[daemon] refined in {time.monotonic()-t0:.2f}s (turn {session.turns})", flush=True)
            conn.sendall(out.encode("utf-8"))
        except Exception as e:
            print(f"[daemon] request failed: {e}", flush=True)
            try:
                conn.sendall(f"__ERROR__ {e}".encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()


if __name__ == "__main__":
    main()
