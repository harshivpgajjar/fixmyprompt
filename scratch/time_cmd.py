#!/usr/bin/env python3
"""Time a command N times, print each run + min/median. Usage: time_cmd.py N -- cmd args..."""
import subprocess, sys, time, statistics, os

n = int(sys.argv[1])
assert sys.argv[2] == "--"
cmd = sys.argv[3:]
times = []
for i in range(n):
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    dt = time.monotonic() - t0
    times.append(dt)
    out = (r.stdout or "").strip().replace("\n", " ")[:120]
    err = (r.stderr or "").strip().replace("\n", " ")[:120]
    print(f"run {i+1}: {dt:.2f}s rc={r.returncode} out={out!r} err={err!r}", flush=True)
print(f"MIN={min(times):.2f}s MEDIAN={statistics.median(times):.2f}s")
