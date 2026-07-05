#!/usr/bin/env python3
"""Test warm-session latency: one long-lived `claude -p --input-format stream-json`
process fed multiple user turns. Prints boot time, per-turn latency."""
import json, subprocess, time, sys

CLAUDE = "/Users/harshiv/.local/bin/claude"
SYSTEM = ("You rewrite rough prompts into sharp, specific ones. "
          "Each user message is an INDEPENDENT prompt to rewrite; ignore all previous turns. "
          "Reply with only the rewritten prompt, nothing else.")

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

t_start = time.monotonic()
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, bufsize=1)

def send_turn(prompt):
    msg = {"type": "user",
           "message": {"role": "user",
                        "content": [{"type": "text", "text": prompt}]}}
    t0 = time.monotonic()
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    first_event = None
    while True:
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()
            raise RuntimeError(f"process exited rc={proc.poll()} stderr={err[:500]}")
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if first_event is None:
            first_event = time.monotonic() - t0
        if ev.get("type") == "result":
            dt = time.monotonic() - t0
            return dt, first_event, ev.get("result", "")[:100]

prompts = [
    "make my website faster plz",
    "fix the login bug",
    "write tests for the api",
    "help me with css its broken",
]
for i, p in enumerate(prompts):
    dt, tfe, result = send_turn(p)
    label = "COLD (incl. boot)" if i == 0 else "WARM"
    print(f"turn {i+1} [{label}]: total={dt:.2f}s first_event={tfe:.2f}s result={result!r}", flush=True)

proc.stdin.close()
proc.wait(timeout=10)
print(f"total wall time: {time.monotonic()-t_start:.2f}s")
