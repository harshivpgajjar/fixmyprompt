#!/usr/bin/env bash
# Whetstone installer — installs the runtime into ~/.claude/whetstone (stable,
# git-backed, OFF the iCloud-synced Desktop) and wires the UserPromptSubmit hook
# into ~/.claude/settings.json. Idempotent. Prints exactly what it did.
#
# Coaching installs DISABLED by default (mode=off) so nothing changes until you
# opt in with `whetstone on`. The /refine command and logging work immediately.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude/whetstone"
SETTINGS="$HOME/.claude/settings.json"
BIN_LINK="$HOME/.local/bin/whetstone"

echo "Whetstone installer"
echo "  source : $SRC"
echo "  runtime: $DEST"

# 1. Copy runtime (code only; logs/config/state stay in $DEST across reinstalls)
mkdir -p "$DEST"
rsync -a --delete \
  --exclude 'prompt-log.jsonl' --exclude 'config.json' \
  --exclude 'pending/' --exclude 'cooldown/' --exclude 'backstop.json' \
  --exclude '.backfilled' --exclude 'criteria.json' --exclude 'projects.json' \
  --exclude 'refine.sock' --exclude 'daemon.pid' --exclude 'daemon*.log' \
  --exclude 'alerts.log' --exclude 'RESEARCH-*' --exclude 'scratch/' \
  --exclude '.git/' --exclude 'tests/' --exclude '__pycache__/' \
  "$SRC"/ "$DEST"/
echo "  ✓ runtime synced"

# 2. Default config (only written if absent — never clobbers your settings)
if [ ! -f "$DEST/config.json" ]; then
  cat > "$DEST/config.json" <<'JSON'
{
  "mode": "off",
  "inject": true,
  "min_words": 4,
  "cooldown_sec": 90,
  "model": "claude-haiku-4-5"
}
JSON
  echo "  ✓ default config written (mode=off — opt in with: whetstone on)"
else
  echo "  • existing config kept"
fi

# 3. CLI on PATH
mkdir -p "$HOME/.local/bin"
ln -sf "$DEST/bin/whetstone" "$BIN_LINK"
chmod +x "$DEST/bin/whetstone" "$DEST/bin/coach_gate.py" "$DEST/bin/backfill_log.py"
echo "  ✓ CLI: $BIN_LINK  (ensure ~/.local/bin is on PATH)"

# 3b. Install the /refine slash command into the user's global skills.
mkdir -p "$HOME/.claude/skills/refine"
cp "$SRC/skills/refine/SKILL.md" "$HOME/.claude/skills/refine/SKILL.md"
echo "  ✓ /refine skill installed"

# 3c. Backfill the prompt log from real history so `whetstone report` has data.
python3 "$DEST/bin/backfill_log.py" 30 | sed 's/^/  • /'

# 4. Wire the hook into settings.json (Python does the JSON merge safely)
python3 - "$SETTINGS" "$DEST/bin/coach_gate.py" <<'PY'
import json, sys, os
settings_path, hook_cmd = sys.argv[1], sys.argv[2]
cmd = f'python3 "{hook_cmd}"'
try:
    with open(settings_path) as f: s = json.load(f)
except Exception:
    s = {}
hooks = s.setdefault("hooks", {})
ups = hooks.setdefault("UserPromptSubmit", [])
# already installed?
def installed(blocks):
    for b in blocks:
        for h in b.get("hooks", []):
            if "coach_gate.py" in h.get("command", ""):
                return True
    return False
if installed(ups):
    print("  • hook already wired in settings.json")
else:
    ups.append({"matcher": "", "hooks": [
        {"type": "command", "command": cmd, "timeout": 20}]})
    with open(settings_path, "w") as f: json.dump(s, f, indent=2)
    print("  ✓ UserPromptSubmit hook wired into settings.json")
PY

echo
echo "Installed. Coaching is OFF by default."
echo "  whetstone status      # see config"
echo "  whetstone on          # turn on live coaching (applies to NEW sessions)"
echo "  whetstone refine \"...\" # try it right now, no session needed"
echo "  whetstone report      # your prompting trend"
echo "To fully remove: run ./uninstall.sh"
