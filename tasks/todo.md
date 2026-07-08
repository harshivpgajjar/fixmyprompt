# Cross-platform (Windows) port — DONE (shipped in fe42396)

Strategy: Node launcher shim for the hook; full port. Mac suite stayed green
throughout (283 tests as of this writing). See docs/WINDOWS_SUPPORT.md for the
original diagnosis, and README's Windows install section for the shipped result.

## Phase 0 — the hook runs on Windows
- [x] bin/coach_gate_launcher.mjs — node shim: resolve py/python3, spawn
      coach_gate.py with stdio inherit + PYTHONUTF8=1, fail-open
- [x] hooks/hooks.json → exec form: node + launcher
- [x] coach_gate.py: reconfigure stdin/stdout to utf-8 at startup (guarded)

## Phase 1 — coaching correct on Windows
- [x] _clipboard(): cross-platform (pbcopy / ctypes CF_UNICODETEXT + clip / xclip/xsel/wl-copy); return success
- [x] _banner()/_stage_for_resend: platform paste key (Ctrl+V vs ⌘V), only claim "copied" on success; _tmux_inject win32 no-op
- [x] context_hints.py: encoding=utf-8 on read/write_text
- [x] CLI main(): reconfigure stdout/stderr utf-8
- [x] refiner.py: encoding=utf-8 on read_text; shutil.which("claude")

## Phase 2 — install & CLI on Windows
- [x] install.py (cross-platform, --uninstall): copytree, json config, in-proc settings merge, per-OS CLI shim (symlink / .cmd + PATH), abort on malformed settings, node-launcher hook command
- [x] fixmyprompt.cmd shim generation
- [x] platform-guard daemon/digest CLI commands → clean no-op on Windows
- [x] tour.py: invoke CLI via sys.executable

## Phase 3 — daemon flag, hygiene, tests, docs
- [x] daemon.py: DAEMON_SUPPORTED flag; is_running/start/status short-circuit; SIGKILL getattr; liveness probe
- [x] .gitattributes (eol=lf)
- [x] tests: skipUnless AF_UNIX on daemon tests; platform-agnostic clipboard test; simulated-Windows degradation tests
- [x] README Windows section
- [x] final cross-platform review + Mac suite green + commit/push
