# Cross-platform (Windows) port

Strategy: Node launcher shim for the hook; full port. Keep the Mac suite green
(currently 248 tests) at every phase.

## Phase 0 — the hook runs on Windows
- [ ] bin/coach_gate_launcher.mjs — node shim: resolve py/python3, spawn
      coach_gate.py with stdio inherit + PYTHONUTF8=1, fail-open
- [ ] hooks/hooks.json → exec form: node + launcher
- [ ] coach_gate.py: reconfigure stdin/stdout to utf-8 at startup (guarded)

## Phase 1 — coaching correct on Windows
- [ ] _clipboard(): cross-platform (pbcopy / ctypes CF_UNICODETEXT + clip / xclip/xsel/wl-copy); return success
- [ ] _banner()/_stage_for_resend: platform paste key (Ctrl+V vs ⌘V), only claim "copied" on success; _tmux_inject win32 no-op
- [ ] context_hints.py: encoding=utf-8 on read/write_text
- [ ] CLI main(): reconfigure stdout/stderr utf-8
- [ ] refiner.py: encoding=utf-8 on read_text; shutil.which("claude")

## Phase 2 — install & CLI on Windows
- [ ] install.py (cross-platform, --uninstall): copytree, json config, in-proc settings merge, per-OS CLI shim (symlink / .cmd + PATH), abort on malformed settings, node-launcher hook command
- [ ] fixmyprompt.cmd shim generation
- [ ] platform-guard daemon/digest CLI commands → clean no-op on Windows
- [ ] tour.py: invoke CLI via sys.executable

## Phase 3 — daemon flag, hygiene, tests, docs
- [ ] daemon.py: DAEMON_SUPPORTED flag; is_running/start/status short-circuit; SIGKILL getattr; liveness probe
- [ ] .gitattributes (eol=lf)
- [ ] tests: skipUnless AF_UNIX on daemon tests; platform-agnostic clipboard test; simulated-Windows degradation tests
- [ ] README Windows section
- [ ] final cross-platform review + Mac suite green + commit/push
