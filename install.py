#!/usr/bin/env python3
"""FixMyPrompt cross-platform installer.

  python install.py            # install
  python install.py --uninstall

Installs the runtime into ~/.claude/fixmyprompt, wires the UserPromptSubmit hook
into ~/.claude/settings.json (via the Node launcher shim so it works on macOS,
Linux, AND Windows), installs the /refine skill, and puts the `fixmyprompt` CLI
on PATH (a symlink on POSIX, a .cmd shim + user PATH entry on Windows).

Coaching ships DISABLED (mode=off); teach-mode ships ON. Nothing intercepts your
prompts until you run `fixmyprompt on`. Idempotent — re-running never clobbers
your config, log, or learned criteria/hints. Pure stdlib.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
SRC = Path(__file__).resolve().parent
HOME = Path.home()
CLAUDE = HOME / ".claude"
DEST = CLAUDE / "fixmyprompt"
SETTINGS = CLAUDE / "settings.json"
LAUNCHER = DEST / "bin" / "coach_gate_launcher.mjs"

# Runtime state that must survive a reinstall (never copied over from source).
_STATE_EXCLUDES = {
    "prompt-log.jsonl", "config.json", "backstop.json", ".backfilled",
    "criteria.json", "projects.json", "refine.sock", "daemon.pid",
    "alerts.log", ".toured", "stdin-keys.log", "fake-refine-calls",
}
_STATE_DIR_EXCLUDES = {"pending", "cooldown", "prompts", "scratch", ".git",
                       "tests", "__pycache__", "cache", "data"}


def _ignore(dirname, names):
    drop = set()
    for n in names:
        if n in _STATE_DIR_EXCLUDES or n in _STATE_EXCLUDES:
            drop.add(n)
        elif n.startswith("RESEARCH-") or n.startswith("daemon") and n.endswith(".log"):
            drop.add(n)
        elif n.endswith(".pyc"):
            drop.add(n)
    return drop


def _copy_runtime():
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, DEST, ignore=_ignore, dirs_exist_ok=True)
    print(f"  ok  runtime synced -> {DEST}")


def _default_config():
    cfg = DEST / "config.json"
    if cfg.exists():
        print("  --  existing config kept")
        return
    cfg.write_text(json.dumps({
        "mode": "off", "tutorial": True, "inject": True,
        "min_words": 4, "cooldown_sec": 90, "model": "claude-haiku-4-5",
    }, indent=2) + "\n", encoding="utf-8")
    print("  ok  default config written (mode=off, teach-mode on — opt in with: fixmyprompt on)")


def _install_skill():
    skill_src = SRC / "skills" / "refine" / "SKILL.md"
    if not skill_src.exists():
        return
    dst = CLAUDE / "skills" / "refine"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_src, dst / "SKILL.md")
    print("  ok  /refine skill installed")


def _backfill():
    bf = DEST / "bin" / "backfill_log.py"
    if not bf.exists():
        return
    try:
        subprocess.run([sys.executable, str(bf), "30"], timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("  ok  prompt log backfilled from history (if any)")
    except Exception:
        pass


def _hook_command():
    """The cross-platform hook: invoke the Node launcher (Node is guaranteed on
    every platform by Claude Code) with an absolute path to the shim. Exec form
    (command + args) so no shell is involved."""
    return {"type": "command", "command": "node",
            "args": [str(LAUNCHER)], "timeout": 20}


def _load_settings():
    if not SETTINGS.exists():
        return {}
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        sys.stderr.write(
            f"  !!  {SETTINGS} exists but is not valid JSON — refusing to overwrite it.\n"
            f"      Fix or move it, then re-run.  (Nothing else was changed.)\n")
        sys.exit(1)
    if not isinstance(data, dict):
        sys.stderr.write(f"  !!  {SETTINGS} is not a JSON object — refusing to overwrite it.\n")
        sys.exit(1)
    return data


def _installed(blocks):
    for b in blocks:
        for h in (b.get("hooks") or []):
            cmd = h.get("command", "")
            if "coach_gate" in cmd or "coach_gate" in " ".join(h.get("args") or []):
                return True
    return False


def _wire_hook():
    if shutil.which("node") is None:
        print("  !!  Node.js not found on PATH. The hook is wired, but Claude Code "
              "provides Node at runtime; if coaching never fires, install Node.")
    s = _load_settings()
    hooks = s.setdefault("hooks", {})
    ups = hooks.setdefault("UserPromptSubmit", [])
    if _installed(ups):
        # refresh the command (e.g. path moved) by replacing our entry
        for b in ups:
            b["hooks"] = [h for h in (b.get("hooks") or [])
                          if "coach_gate" not in (h.get("command", "") + " ".join(h.get("args") or []))]
        ups[:] = [b for b in ups if b.get("hooks")]
    ups.append({"matcher": "", "hooks": [_hook_command()]})
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    print("  ok  UserPromptSubmit hook wired into settings.json (Node launcher)")


# --- CLI on PATH -----------------------------------------------------------

def _cli_on_path_posix():
    bindir = HOME / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / "fixmyprompt"
    target = DEST / "bin" / "fixmyprompt"
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
    except Exception:
        shutil.copy2(target, link)
    for f in ("fixmyprompt", "coach_gate.py", "backfill_log.py"):
        p = DEST / "bin" / f
        if p.exists():
            p.chmod(0o755)
    print(f"  ok  CLI: {link}  (ensure {bindir} is on your PATH)")


def _cli_on_path_windows():
    # A .cmd shim that runs the extensionless Python CLI via the py launcher
    # (fallback: the interpreter running this installer), placed in a per-user
    # dir that we add to the user PATH.
    progdir = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "Programs" / "fixmyprompt"
    progdir.mkdir(parents=True, exist_ok=True)
    launcher = "py -3" if shutil.which("py") else f'"{sys.executable}"'
    cli = DEST / "bin" / "fixmyprompt"
    shim = progdir / "fixmyprompt.cmd"
    shim.write_text(f'@echo off\r\n{launcher} "{cli}" %*\r\n', encoding="utf-8")
    print(f"  ok  CLI shim: {shim}")
    _add_to_user_path_windows(str(progdir))


def _add_to_user_path_windows(new_dir: str):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                cur, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                cur = ""
            parts = [p for p in cur.split(os.pathsep) if p]
            if any(os.path.normcase(p) == os.path.normcase(new_dir) for p in parts):
                print(f"  --  {new_dir} already on user PATH")
                return
            newval = os.pathsep.join(parts + [new_dir]) if parts else new_dir
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, newval)
        # Broadcast so new shells see it without a logout.
        try:
            import ctypes
            HWND_BROADCAST, WM_SETTINGCHANGE = 0xFFFF, 0x1A
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 0, 5000, None)
        except Exception:
            pass
        print(f"  ok  added {new_dir} to your user PATH (open a NEW terminal to pick it up)")
    except Exception as e:
        print(f"  !!  couldn't update PATH automatically ({e}). Add this dir to PATH: {new_dir}")


def install():
    print("FixMyPrompt installer")
    print(f"  source : {SRC}")
    print(f"  runtime: {DEST}")
    _copy_runtime()
    _default_config()
    _install_skill()
    _backfill()
    _wire_hook()
    (_cli_on_path_windows if IS_WIN else _cli_on_path_posix)()
    print()
    print("Installed. Coaching is OFF by default.")
    print("  fixmyprompt tour        # 60-second walkthrough")
    print("  fixmyprompt on          # turn on live coaching (applies to NEW sessions)")
    print('  fixmyprompt try "..."   # preview the coach safely')
    print("Restart Claude Code (or start a new session) for the hook to take effect.")


# --- uninstall -------------------------------------------------------------

def _unwire_hook():
    if not SETTINGS.exists():
        return
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        print("  --  settings.json unreadable; left untouched")
        return
    ups = (s.get("hooks") or {}).get("UserPromptSubmit") or []
    for b in ups:
        b["hooks"] = [h for h in (b.get("hooks") or [])
                      if "coach_gate" not in (h.get("command", "") + " ".join(h.get("args") or []))]
    s.get("hooks", {})["UserPromptSubmit"] = [b for b in ups if b.get("hooks")]
    if not s["hooks"]["UserPromptSubmit"]:
        del s["hooks"]["UserPromptSubmit"]
    SETTINGS.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    print("  ok  hook removed from settings.json (other hooks left intact)")


def _teardown_daemon():
    """macOS only: stop the warm daemon + digest and remove their LaunchAgents
    (launchd is macOS-specific; nothing to do on Windows/Linux)."""
    if sys.platform != "darwin" or not hasattr(os, "getuid"):
        return
    try:
        uid = os.getuid()
        la = HOME / "Library" / "LaunchAgents"
        for label in ("com.fixmyprompt.daemon", "com.fixmyprompt.digest"):
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
            (la / f"{label}.plist").unlink(missing_ok=True)
    except Exception:
        pass
    try:  # stop any manually-started daemon too
        sys.path.insert(0, str(DEST))
        from fixmyprompt import daemon
        daemon.stop()
    except Exception:
        pass


def uninstall():
    print("FixMyPrompt uninstaller")
    _teardown_daemon()
    _unwire_hook()
    if IS_WIN:
        shim = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "Programs" / "fixmyprompt"
        shutil.rmtree(shim, ignore_errors=True)
    else:
        (HOME / ".local" / "bin" / "fixmyprompt").unlink(missing_ok=True)
    print("  ok  CLI shim removed")
    if "--purge" in sys.argv[1:]:
        shutil.rmtree(DEST, ignore_errors=True)
        print(f"  ok  purged {DEST} (config + logs)")
    else:
        print(f"  --  runtime left at {DEST}  (pass --purge to delete logs/config too)")
    print("Restart Claude Code for the hook removal to take effect.")


def main():
    if "--uninstall" in sys.argv[1:] or "-u" in sys.argv[1:]:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
