# Contributing to FixMyPrompt

Thanks for considering it. This is a small, focused tool — the bar for changes is "does this make the coach teach better or intrude less," not "is this a cool feature."

## Setup

```bash
git clone https://github.com/harshivpgajjar/fixmyprompt.git
cd fixmyprompt
python3 -m unittest discover -s tests -v
```

Pure Python 3 stdlib — no dependencies to install, no build step.

## Before opening a PR

- **Run the full suite** (`python3 -m unittest discover -s tests`) and add tests for any behavior change. The suite runs the real hook as a subprocess against a sandboxed `FIXMYPROMPT_HOME`, so most new behavior should be testable the same way — see `tests/test_coach_gate.py` for the pattern.
- **Preserve the invariants.** Two are load-bearing and have dedicated tests: the gate can never block twice in a row (loop-proof), and any internal error must fail open — the prompt sends normally rather than the coach breaking your turn. If you touch `bin/coach_gate.py`, keep both green.
- **Never ship your own data as a default.** `fixmyprompt/context_hints.py`'s criteria/project stores ship empty and are user-populated — don't reintroduce hardcoded personal seeds.
- **No secrets, ever.** If you touch anything that persists text (the prompt log, learned criteria), route it through the existing secret-detection filter (`scorelog._SECRET` / `context_hints._has_secret`) rather than writing a new one.
- **Mode-awareness is sacred.** Never make the coach intervene on an intentional "explore" prompt (`blow me away`, `give me options`, etc.) — that's the #1 way this tool could annoy someone into uninstalling it.

## Reporting bugs

Open an issue with: the prompt that triggered the bad behavior, your `fixmyprompt status` output, and (if it's a live-gate issue) whether `mode`/`tutorial`/`daemon` were on. `fixmyprompt try "<prompt>"` is a safe way to reproduce most gate behavior without live-firing.

## Design questions

Read [docs/DESIGN.md](docs/DESIGN.md) first — it covers why the tool is shaped the way it is (why there's no in-place edit, why mode-awareness exists, why the daemon exists at all). Many "why doesn't it just—" questions are answered there.
