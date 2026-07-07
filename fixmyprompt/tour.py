"""Interactive onboarding tour for FixMyPrompt.

`fixmyprompt tour` (and `fixmyprompt help`) walk a new user through what the
coach does and how to drive it — the features catalog, teach-mode, the optional
daemon, token-usage warnings, and (the thing people trip on) how to send a
prompt as-is and why images are never intercepted.

Interactive when attached to a TTY (press Enter between steps, y/n to apply a
setting); linear and non-blocking when piped, so it's safe in scripts/tests.
"""
from __future__ import annotations

import os
import subprocess
import sys

from . import config, daemon

_TOURED_MARKER = config.RUNTIME_DIR / ".toured"


# --- tiny presentation helpers -------------------------------------------

def _tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _tty() else s


def _header(n: int, total: int, title: str) -> None:
    bar = _c("2", "─" * 58)
    print(f"\n{bar}")
    print(f"{_c('1;36', f'  {title}')}   {_c('2', f'({n}/{total})')}")
    print(bar)


def _pause(interactive: bool) -> None:
    if interactive:
        try:
            input(_c("2", "\n    press ⏎ to continue…"))
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)


def _ask_yn(interactive: bool, question: str, default: bool) -> bool:
    if not interactive:
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"    {question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not ans:
        return default
    return ans[0] == "y"


# --- the tour ------------------------------------------------------------

def run(cli_path: str | None = None, interactive: bool | None = None) -> None:
    """Walk the onboarding steps. `cli_path` is the `fixmyprompt` executable, used
    to apply optional settings (daemon on) via the real CLI."""
    if interactive is None:
        interactive = _tty()
    cfg = config.load()
    total = 6

    coaching = cfg.get("mode", "off")
    teach = "on" if cfg.get("tutorial") else "off"
    dmon = "on" if daemon.is_running() else "off"
    print(_c("1;35", "\n  👋  Welcome to FixMyPrompt"))
    print("  A prompt coach for Claude Code: it sharpens a rough prompt before it")
    print("  sends, and teaches you the built-in features that save tokens and time.")
    print(_c("2", f"  status: coaching={coaching}  ·  teach-mode={teach}  ·  daemon={dmon}"))
    _pause(interactive)

    # 1. The flow — the part people trip on (send-as-is + images)
    _header(1, total, "How the coach works")
    print("  When you press ⏎, FixMyPrompt may step in with a sharper rewrite or a")
    print("  short scaffold of what's missing. Two things to know:")
    print(f"    • {_c('1', 'Sending as-is is one paste away.')} Claude Code clears the box")
    print("      on a coach step, so FixMyPrompt copies your prompt to the clipboard —")
    print("      press ⌘V then ⏎ to send it unchanged, or type your own edit instead.")
    print(f"    • {_c('1', 'Images are never intercepted.')} If your prompt has a")
    print("      screenshot/image attached, it always goes straight through —")
    print("      you never lose an attachment to the coach.")
    _pause(interactive)

    # 2. Features catalog
    _header(2, total, "Discover Claude Code features")
    print("  FixMyPrompt catalogs the built-in Claude Code features that make you")
    print("  efficient — each with when to use it and its token/time trade-off:")
    print(_c("2", "    /clear · /compact · /effort · ultrathink · /model · subagents"))
    print(_c("2", "    /goal · @file · vision · artifacts · /rewind · /usage …"))
    print(f"  Browse them all:  {_c('1;36', 'fixmyprompt features')}   (filter: features reasoning)")
    _pause(interactive)

    # 3. Teach-mode
    _header(3, total, "Teach-mode (learn as you go)")
    print("  With teach-mode ON, FixMyPrompt coaches EVERY prompt — and affirms the")
    print("  good ones (\"well-specified ✓\") so you learn what strong prompts look")
    print("  like, not just what's broken. It's on by default while you're learning.")
    print(f"  {_c('2', 'Turn it off anytime:  fixmyprompt tutorial off')}")
    if interactive:  # only the interactive walkthrough changes settings
        want = None
        if not cfg.get("tutorial"):
            want = _ask_yn(interactive, "Turn teach-mode ON now?", True)
        elif not _ask_yn(interactive, "Keep teach-mode ON?", True):
            want = False
        if want is not None:
            try:
                config.save({"tutorial": bool(want)})
                print(_c("32", "    ✓ teach-mode ON (new sessions)") if want
                      else _c("33", "    • teach-mode OFF — only under-specified prompts get coached"))
            except Exception:
                print(_c("33", "    • couldn't save that setting (config not writable)"))
    _pause(interactive)

    # 4. Daemon
    _header(4, total, "Faster rewrites (optional daemon)")
    print("  By default you get instant local scaffolds — no API key, ever. Turn on")
    print("  the daemon for ~1.5s AI-written rewrites on your subscription (still no")
    print("  key): a warm background process does the rewrite before you send.")
    print(f"  {_c('2', 'Toggle:  fixmyprompt daemon on   /   fixmyprompt daemon off')}")
    if interactive and cli_path and not daemon.is_running():
        if _ask_yn(interactive, "Enable the daemon now?", False):
            try:
                # via sys.executable — the CLI is an extensionless Python script
                # that Windows can't run directly.
                subprocess.run([sys.executable, cli_path, "daemon", "on"], timeout=30)
            except Exception:
                print(_c("33", "    • couldn't start it here — run `fixmyprompt daemon on` yourself"))
    _pause(interactive)

    # 5. Token-usage warnings
    _header(5, total, "Token-usage warnings")
    print("  A bloated context wastes tokens and degrades output. FixMyPrompt warns")
    print("  you at the moment it matters — e.g. starting NEW work in an old session:")
    print(_c("2", "    💡 run /clear first (or /compact) — a fresh start saves tokens"))
    print("  And Claude Code itself lets you watch spend directly:")
    print(f"    {_c('1;36', '/context')} (what's filling the window)   {_c('1;36', '/usage')} (token cost)")
    _pause(interactive)

    # 6. Wrap
    _header(6, total, "You're set")
    print(f"  Start coaching:      {_c('1;36', 'fixmyprompt on')}")
    print(f"  Try it risk-free:    {_c('1;36', 'fixmyprompt try \"fix the login page\"')}")
    print(f"  See this tour again: {_c('1;36', 'fixmyprompt help')}")
    print(f"  Full command list:   {_c('1;36', 'fixmyprompt help --commands')}\n")

    _mark_toured()


def _mark_toured() -> None:
    try:
        config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        _TOURED_MARKER.write_text("1")
    except Exception:
        pass


def has_toured() -> bool:
    return _TOURED_MARKER.exists()


def first_run_hint() -> str | None:
    """One-line nudge shown before a command's output on first ever use."""
    if has_toured():
        return None
    return _c("1;33", "👋 New to FixMyPrompt? Run `fixmyprompt tour` for a 60-second walkthrough.")
