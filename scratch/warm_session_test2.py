#!/usr/bin/env python3
"""v2: warm-session latency with capped output, thinking off, usage instrumentation."""
import json, os, subprocess, time

CLAUDE = "/Users/harshiv/.local/bin/claude"
SYSTEM = ("You rewrite rough prompts into sharp, specific ones. "
          "Each user message is an INDEPENDENT prompt to rewrite; ignore all previous turns. "
          "Reply with ONLY the rewritten prompt, max 50 words.")

cmd = [
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

env = dict(os.environ)
env.update({"MAX_THINKING_TOKENS": "0", "DISABLE_TELEMETRY": "1",
            "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})

t_boot = time.monotonic()
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, bufsize=1, env=env)

def send_turn(prompt):
    msg = {"type": "user",
           "message": {"role": "user",
                        "content": [{"type": "text", "text": prompt}]}}
    t0 = time.monotonic()
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"exited rc={proc.poll()} err={proc.stderr.read()[:400]}")
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            dt = time.monotonic() - t0
            u = ev.get("usage", {})
            return (dt, ev.get("duration_api_ms"), u.get("input_tokens"),
                    u.get("cache_read_input_tokens"), u.get("output_tokens"),
                    (ev.get("result") or "")[:80])

prompts = [
    "make my website faster plz",
    "fix the login bug",
    "write tests for the api",
    "help me with css its broken",
    "summarize this doc",
    "add dark mode to the app",
]
for i, p in enumerate(prompts):
    dt, api_ms, tin, tcache, tout, res = send_turn(p)
    label = "COLD" if i == 0 else "WARM"
    print(f"turn {i+1} [{label}]: total={dt:.2f}s api={api_ms}ms in={tin} cache_read={tcache} out={tout} res={res!r}", flush=True)
print(f"boot-to-done wall: {time.monotonic()-t_boot:.2f}s")
proc.stdin.close(); proc.wait(timeout=10)
