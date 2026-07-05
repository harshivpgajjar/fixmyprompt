#!/usr/bin/env python3
"""One-time backfill: score the user's real prompt history into the Whetstone
log so `whetstone report` has a meaningful baseline on day one.

Reads ~/.claude/history.jsonl (typed prompts), scores each with the real
classifier, and appends historical records (action="pass") with their true
timestamps. Idempotent-ish: writes a marker so it won't double-backfill.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whetstone import config, scorelog, scorer  # noqa: E402

HISTORY = Path(os.path.expanduser("~")) / ".claude" / "history.jsonl"
MARKER = config.RUNTIME_DIR / ".backfilled"


def main(days: int = 30) -> None:
    if MARKER.exists():
        print("already backfilled; skipping")
        return
    if not HISTORY.exists():
        print("no history.jsonl found; skipping")
        return
    cfg = config.load()
    cutoff = time.time() - days * 86400
    n = 0
    for line in HISTORY.read_text(errors="ignore").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        text = (rec.get("display") or "").strip()
        ts = rec.get("timestamp")
        if not text or not ts:
            continue
        ts = ts / 1000.0 if ts > 1e12 else ts  # ms -> s
        if ts < cutoff:
            continue
        if text.startswith("/") or text.startswith("!"):
            continue
        feats = scorer.classify(text)
        # write directly with the historical timestamp
        try:
            config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": round(ts, 1),
                "action": "pass",
                "word_count": feats["word_count"],
                "mode": feats["mode"],
                "is_continuation": feats["is_continuation"],
                "has_constraints": feats["has_constraints"],
                "has_done_criteria": feats["has_done_criteria"],
                "has_reference": feats["has_reference"],
                "is_design": feats["is_design"],
                "quality": feats["quality"],
                "gaps": feats["gaps"],
                "preview": scorelog._preview(text, cfg),
                "backfill": True,
            }
            with scorelog.LOG_PATH.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
            n += 1
        except Exception:
            continue
    MARKER.write_text(str(time.time()))
    print(f"backfilled {n} historical prompts from the last {days} days")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
