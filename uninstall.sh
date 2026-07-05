#!/usr/bin/env bash
# Whetstone uninstaller — removes the hook from settings.json and the CLI symlink.
# Leaves ~/.claude/whetstone (your config + prompt log) unless you pass --purge.
set -euo pipefail
SETTINGS="$HOME/.claude/settings.json"

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
