#!/usr/bin/env bash
# Whetstone uninstaller — removes the hook from settings.json and the CLI symlink.
# Leaves ~/.claude/whetstone (your config + prompt log) unless you pass --purge.
set -euo pipefail
SETTINGS="$HOME/.claude/settings.json"
UID_N=$(id -u)

# 0. Tear down the daemon + digest LaunchAgents BEFORE removing code, so nothing
# is left running or respawn-looping (KeepAlive) against a deleted module.
for label in com.whetstone.daemon com.whetstone.digest; do
  launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
# stop any manually-started daemon too (best-effort; module may still exist)
python3 -c "import sys; sys.path.insert(0, '$HOME/.claude/whetstone'); from whetstone import daemon; daemon.stop()" 2>/dev/null || true
echo "✓ daemon + LaunchAgents stopped/removed"

python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
try:
    s = json.load(open(p))
except Exception:
    sys.exit(0)
ups = s.get("hooks", {}).get("UserPromptSubmit", [])
kept = []
for b in ups:
    b["hooks"] = [h for h in b.get("hooks", []) if "coach_gate.py" not in h.get("command", "")]
    if b["hooks"]:
        kept.append(b)
if "hooks" in s:
    s["hooks"]["UserPromptSubmit"] = kept
    if not kept:
        s["hooks"].pop("UserPromptSubmit", None)
json.dump(s, open(p, "w"), indent=2)
print("✓ hook removed from settings.json")
PY

rm -f "$HOME/.local/bin/whetstone" && echo "✓ CLI symlink removed"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.claude/whetstone" && echo "✓ purged ~/.claude/whetstone (config + logs)"
else
  echo "• kept ~/.claude/whetstone (config + prompt log). Pass --purge to delete."
fi
echo "Uninstalled. Live coaching stops on your next new session."
