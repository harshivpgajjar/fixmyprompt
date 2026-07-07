#!/usr/bin/env node
// Cross-platform launcher for the FixMyPrompt UserPromptSubmit hook.
//
// A hooks.json command is a single static string, and no bare Python interpreter
// name works on every OS (`python3` hits the Microsoft Store stub on Windows;
// `py` doesn't exist on Unix). Claude Code guarantees Node.js on every platform,
// so we invoke this tiny Node shim instead: it resolves a working Python 3 and
// runs coach_gate.py with stdio passed straight through — stdin is the hook
// payload, stdout is the protocol JSON.
//
// FAIL-OPEN is the invariant: if no Python is found or anything goes wrong, we
// exit 0 so the user's prompt is NEVER blocked. coach_gate.py itself always
// exits 0, so a non-zero child status means "the interpreter didn't run our
// script" (e.g. the Windows Store stub) — we treat that as not-found.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const gate = join(here, "coach_gate.py");

// Best-first interpreter candidates per platform. On Windows the `py` launcher
// is the only reliable option (it dodges the App Execution Alias stubs).
const candidates =
  process.platform === "win32"
    ? [["py", ["-3"]], ["python", []], ["python3", []]]
    : [["python3", []], ["python", []]];

// Force UTF-8 in the child so a Windows cp1252 locale can't corrupt the prompt
// (belt-and-suspenders with coach_gate's own stdin/stdout reconfigure).
const childEnv = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };

function resolveInterpreter() {
  // Probe with stdio ignored (does NOT touch our stdin) so we can try several
  // candidates without consuming the single hook payload on a failed one.
  for (const [cmd, pre] of candidates) {
    try {
      const probe = spawnSync(cmd, [...pre, "-c", "import sys; sys.exit(0)"], {
        stdio: "ignore",
        env: childEnv,
      });
      if (!probe.error && probe.status === 0) return [cmd, pre];
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

try {
  const found = resolveInterpreter();
  if (!found) process.exit(0); // no Python — fail open, never block the prompt
  const [cmd, pre] = found;
  const res = spawnSync(cmd, [...pre, gate], { stdio: "inherit", env: childEnv });
  process.exit(res.status == null ? 0 : res.status);
} catch {
  process.exit(0); // anything unexpected — fail open
}
