#!/usr/bin/env python3
"""Client for refine_daemon.py — what a UserPromptSubmit hook would call.

Usage: python3 refine_client.py "rough prompt here"
Exits 0 with refined prompt on stdout; exits 2 (silently) if daemon unavailable
so the hook degrades gracefully.
"""
import socket, sys, time

SOCK_PATH = "/tmp/whetstone-refine.sock"
TIMEOUT_S = 8.0

def refine(prompt: str) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT_S)
    s.connect(SOCK_PATH)
    s.sendall(prompt.encode("utf-8"))
    s.shutdown(socket.SHUT_WR)
    chunks = []
    while True:
        b = s.recv(65536)
        if not b:
            break
        chunks.append(b)
    s.close()
    return b"".join(chunks).decode("utf-8")

if __name__ == "__main__":
    t0 = time.monotonic()
    try:
        out = refine(sys.argv[1])
    except (OSError, socket.timeout):
        sys.exit(2)  # daemon down — hook should no-op
    if out.startswith("__ERROR__"):
        sys.exit(2)
    print(out)
    print(f"[client round-trip: {time.monotonic()-t0:.2f}s]", file=sys.stderr)
