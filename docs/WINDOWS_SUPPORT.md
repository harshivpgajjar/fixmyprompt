# Cross-platform (Windows) support — diagnosis & plan

FixMyPrompt was built and tested on macOS. A real Windows install attempt (ASUS
ExpertBook, Python 3.13) surfaced that the plugin is effectively **non-functional
on Windows**. This doc is the verified diagnosis and the fix plan.

## TL;DR

- **One thing makes the product 100% inert on Windows:** the hook command
  `python3 …` in `hooks/hooks.json`. `python3` doesn't exist on stock Windows, so
  the coach hook never runs.
- **The code itself imports fine on Windows** — no rearchitecture needed. Every
  unix-specific call (`AF_UNIX`, `os.getuid`, `os.fork`, `launchctl`, `pbcopy`,
  tmux) lives inside functions, and the default hook path never reaches them.
- The rest is a **tractable set of guarded-call-site fixes**: encoding, clipboard,
  a cross-platform installer, and platform guards on the opt-in daemon/scheduling.

## Verified facts (drive the design)

1. **Hook execution model.** A `type:"command"` hook with **no `args`** (FixMyPrompt's
   case) is *shell form*: Claude Code runs it via `sh -c` on macOS/Linux and
   **Git Bash on Windows — or PowerShell if Git Bash isn't installed** (never
   cmd.exe). Adding an `args` array makes it *exec form* (spawned directly, no
   shell). `${CLAUDE_PLUGIN_ROOT}` is substituted by Claude Code on every OS
   (safe); `$HOME`/`~` are **not** and should be avoided in the command.
2. **No per-OS command selector**, and **no single bare interpreter name works on
   both** platforms: `python3` → Microsoft Store stub on Windows (python.org does
   *not* create `python3.exe`); `py` → Windows-only; `python` → removed on modern
   macOS. So Python-by-name is not portable.
3. **The cross-platform pattern** (used by real Windows-compatible plugins, e.g.
   `r1di/claude-code-plugins-windows`, whose changelog is literally "Bash scripts
   with python3 → Node.js scripts"): invoke via **`node`** (guaranteed present on
   every platform by Claude Code) in exec form, and do OS logic inside the script.
   The alternative for script-installs is an installer that bakes the resolved
   absolute interpreter path (`sys.executable`) into `settings.json`.
4. **`socket.AF_UNIX` is not exposed by CPython on Windows** through 3.13 (added
   only in 3.14). The warm daemon can't use its transport on Windows → disable it
   there and use the keyless local-scaffold path (already the default,
   `use_daemon=False`).
5. **No import-time crash on Windows.** `import`-ing the package, the CLI, and the
   hook all succeed. Failures are all at *call time*.
6. **Windows stdin/stdout default to cp1252, not UTF-8**, when redirected (which is
   exactly how a hook is invoked). Non-ASCII → `UnicodeDecodeError`/`UnicodeEncodeError`.

## Diagnosis — what breaks, by tier

### Tier 0 — Blocker: the hook never runs
| # | File | Issue | Windows failure |
|---|---|---|---|
| 0.1 | `hooks/hooks.json:9` | command is `python3 ${CLAUDE_PLUGIN_ROOT}/bin/coach_gate.py` | `python3` absent → Store stub → coach_gate.py never executes. **Core feature 100% dead.** |
| 0.2 | `bin/coach_gate.py:_read_stdin` | `sys.stdin.read()` uses cp1252 | non-ASCII prompt → `UnicodeDecodeError` → `{}` → coaching silently skipped, or mojibake |

### Tier 1 — Coaching correctness (silent failures)
| # | File | Issue | Windows failure |
|---|---|---|---|
| 1.1 | `bin/coach_gate.py:_clipboard` | hardcoded `pbcopy` | `FileNotFoundError` swallowed → nothing copied. **Breaks the send-as-is fix we just shipped** — banner claims "copied to clipboard / ⌘V" but clipboard is empty and Claude Code already cleared the box → the user's prompt is lost with no recovery. |
| 1.2 | `bin/coach_gate.py:_banner` | footers say "copied to clipboard", "⌘V" | Windows paste is Ctrl+V; misleading |
| 1.3 | `fixmyprompt/context_hints.py:96,104` | `read/write_text` with `ensure_ascii=False` + `≥` in DEFAULT_CRITERIA, no `encoding=` | `UnicodeEncodeError` on cp1252 (swallowed) → criteria/project-hint **memory silently dead on Windows** |
| 1.4 | CLI-wide (`report.py`, `tour.py`, `cc_tips.py`, `suggest.py`, `bin/fixmyprompt`) | print sparklines/arrows/emoji/box-drawing | `fixmyprompt report/progress/tour/... > file` (any redirect/pipe) → `UnicodeEncodeError` → command crashes |
| 1.5 | `fixmyprompt/refiner.py:174` | bare `claude` in subprocess | CreateProcess ignores PATHEXT → `claude.cmd` not found → the opt-in `claude -p` backend silently unavailable |

### Tier 2 — Install & CLI (no working Windows path today)
| # | File | Issue | Windows failure |
|---|---|---|---|
| 2.1 | `install.sh` / `uninstall.sh` | bash + rsync + `ln -sf` + chmod + heredocs + `~/.local/bin` + `python3` baked into settings.json | **Cannot run on Windows at all**; even under Git Bash it writes the broken `python3` hook |
| 2.2 | `bin/fixmyprompt` (extensionless) | put on PATH via symlink | Windows can't run an extensionless shebang script; `fixmyprompt <cmd>` → "not recognized". **Whole CLI unavailable.** |
| 2.3 | `bin/fixmyprompt` daemon/digest cmds | `os.getuid()` + `launchctl` **unguarded** | `fixmyprompt daemon on/off/start/stop` and `digest-schedule on/off` → **unhandled traceback** (hard crash, NOT fail-safe) |

### Tier 3 — Opt-in daemon subsystem (macOS/unix-only)
`fixmyprompt/daemon.py`: `socket.AF_UNIX` (464, 533), `os.fork`/`os.setsid` (637–651),
`os.kill(pid,0)` liveness (wrong semantics on Windows), `signal.SIGKILL` (undefined
on Windows), launchd `.plist` + `launchctl`. → **disable on Windows**; the default
local-scaffold path needs none of it.

### Tier 4 — Hygiene
No `.gitattributes` (CRLF can break shebang scripts under Git Bash/WSL); daemon
tests hardcode `AF_UNIX` (un-runnable on Windows CI); `context_hints` path matching
uses hardcoded `/`.

## Already cross-platform — do NOT touch
`scorer.py`, `outcome.py`, `suggest.py`, `cc_tips.py`, `report.py` logic,
`__init__.py` (pure/regex, no I/O); `config.py`/`state.py`/`scorelog.py` path
handling (pathlib + `expanduser`, ASCII-only JSON via `ensure_ascii` default);
`refiner.py` API path (urllib, prompt via stdin); `bin/fixmyprompt cmd_try` (uses
`sys.executable` + tempfile — the gold pattern to mirror).

## The one architectural decision: how the hook invokes Python

A single static `hooks.json` command can't name a Python interpreter that works on
both OSes. Two viable strategies:

- **A — Node launcher shim (recommended).** Ship `bin/coach_gate_launcher.mjs` (~20
  lines, zero deps): it resolves Python (`py -3`/`python3`/`python`), spawns
  `coach_gate.py` with stdio inherited, and propagates the exit code (fail-open on
  no-Python). `hooks.json` becomes exec form: `{"command":"node","args":["${CLAUDE_PLUGIN_ROOT}/bin/coach_gate_launcher.mjs"]}`.
  Node is guaranteed by Claude Code on every OS. **This makes both the marketplace
  `/plugin install` AND the script install work with zero per-machine config, on
  all three OSes.** Keeps 100% of the Python logic; the shim is only a launcher.
- **B — Pure Python, installer-resolved path.** The `install.py` writes the resolved
  absolute `sys.executable` into `settings.json` (`"C:\\…\\python.exe" "…coach_gate.py"`).
  Simpler, no Node — but it only fixes the **script-install** path. The
  **marketplace `/plugin install`** path still ships `hooks.json` with a static
  command, so Windows marketplace users would still hit the `python3` break unless
  they hand-edit. Good enough if we don't care about zero-config marketplace on Windows.

## Fix plan (phased)

**Phase 0 — Unblock Windows (the hook runs):**
1. Add the hook-interpreter fix (Strategy A: Node shim + exec-form `hooks.json`; or
   B: install-resolved path).
2. `coach_gate.py`: at startup, `sys.stdin.reconfigure(encoding="utf-8", errors="replace")`
   and `sys.stdout.reconfigure(encoding="utf-8")` (guarded for older Pythons). Fixes
   the stdin decode bug and hardens the protocol stream.

**Phase 1 — Coaching correct on Windows:**
3. Cross-platform `_clipboard()`: dispatch by `sys.platform` — `pbcopy` (mac);
   Windows via **ctypes `CF_UNICODETEXT`** (fast + correct Unicode; `clip.exe`
   fallback; NOT PowerShell — 200–600ms startup blows the hook latency budget);
   Linux `wl-copy`/`xclip`/`xsel` (probe with `shutil.which`). Return success.
4. `_banner()`: platform-aware paste key (Ctrl+V vs ⌘V), only claim "copied" on a
   real success; `_tmux_inject()` explicit `win32` no-op.
5. `context_hints.py`: `encoding="utf-8"` on read/write_text (revives criteria/hints).
6. CLI: `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")` at the
   top of `main()` — fixes every command's Unicode on redirect at once.
7. `refiner.py`: `encoding="utf-8"` on read_text; `shutil.which("claude")`.

**Phase 2 — Install & CLI on Windows:**
8. Cross-platform **`install.py`** (`python install.py` / `--uninstall`), canonical
   for all OSes: `shutil.copytree(ignore=…)` copy; `json.dump` config; in-process
   `settings.json` merge; per-OS CLI entry (POSIX symlink → `~/.local/bin`; Windows
   `fixmyprompt.cmd` shim in `%LOCALAPPDATA%\Programs\fixmyprompt` + PATH via
   registry/`setx`); interpreter resolution; **abort (don't clobber) on a malformed
   `settings.json`** (already fixed in install.sh — mirror it). Keep `install.sh` as
   a thin POSIX wrapper or retire it.
9. Platform-guard the daemon/digest CLI commands → on Windows print "daemon &
   scheduling are macOS/Linux-only; the keyless local-scaffold coach is active" and
   no-op, instead of crashing on `getuid`/`launchctl`.
10. `tour.py`: invoke the CLI via `sys.executable` (not the extensionless path).

**Phase 3 — Hygiene & tests:**
11. `daemon.py`: `DAEMON_SUPPORTED = hasattr(socket,"AF_UNIX") and hasattr(os,"fork")`;
    `is_running()/start()/status()` short-circuit to stopped/no-op when unsupported;
    `getattr(signal,"SIGKILL",signal.SIGTERM)`; cross-platform liveness probe.
12. `.gitattributes`: `* text=auto`, `*.sh/*.py/bin/* text eol=lf`.
13. Tests: `@unittest.skipUnless(hasattr(socket,"AF_UNIX"), …)` on daemon tests;
    make the clipboard test platform-agnostic; add Windows-degradation tests
    (mock `sys.platform`/cp1252) that assert no-crash + correct fallback.
14. README: a real Windows install section; note daemon/inject are POSIX-only.

## Testing strategy

I can't run Windows in this environment, so:
- **Mac/Linux regression:** the full suite (currently 248 tests) must stay green —
  guards the "don't break the working platform" direction.
- **Simulated-Windows unit tests:** mock `sys.platform == "win32"`, force cp1252
  encoding, and assert: clipboard dispatch picks the Windows backend; daemon/digest
  commands no-op cleanly; encoding reconfigure prevents decode/encode errors;
  `_has_attachment`/banner behave.
- **Real-Windows checklist:** a `docs/` checklist you run on the ASUS box (mirrors
  the existing `MANUAL_TESTING.md`) — install via `install.py`, confirm the hook
  fires (vague prompt → coach), non-ASCII prompt coached correctly, Ctrl+V restores
  the prompt after a block, `fixmyprompt report`/`tour` render, daemon command
  degrades gracefully.
- **CI (optional):** add a Windows runner to prove the suite + a smoke install.
