# Lessons

Durable lessons for anyone working on FixMyPrompt. Add one whenever something
bites twice or a wrong assumption ships.

## Verify feature claims against the live product, not just docs

**What happened:** A research pass sourced only from `code.claude.com/docs`
concluded that `ultrathink` was "folklore / not a real feature" and shipped a tip
that *debunked* it. In reality, typing `ultrathink` in a Claude Code prompt shows
"Deeper reasoning requested for this turn", and `ultracode` shows "Dynamic
workflow requested for this turn" — both are real inline keywords. The docs
lagged the product.

**Root cause:** Over-trusting a single confident "VERIFIED AS NOT REAL" from
doc-scraping over direct product behavior.

**Rule:** For claims about what a keyword/command *does*, prefer live evidence
(a screenshot / an actual run) over docs, and never ship a confident "X isn't
real" without reproducing its absence. When the two disagree, the running
product wins. Keep the accuracy guard tests, but assert what's *true*, not what a
doc said.

## A feature tip must carry an execution path, not just an explanation

**What happened:** Tips said "you could use `/goal <condition>`" — correct, but
the user still had to figure out and type the actual command. Feedback: *"put it
in the prompt for the user to ACTUALLY use it… the execution path should be
there."*

**Rule:** Every situational tip ends with a concrete `→ do this` line naming the
exact command/keystroke, and fills placeholders from the user's own words where
possible (e.g. `/goal all tests pass`, derived from "keep going until all tests
pass"). Explanation earns attention; the execution path earns the action.

## The submit hook's stdout is a protocol — guard it and its regexes

**What happened:** Adversarial review found the classifier's `_REFERENCE` regex
(`\S+\.(tld)`) backtracked O(n²) on a long single-line paste (50k chars → 4.4s,
120k → 28.7s), exceeding the 20s hook timeout and freezing input.

**Rules:**
- Bound every regex run that touches raw prompt text (`[^\s.]{1,40}`, not `\S+`),
  and cap what the lexicon regexes scan (`classify()` truncates > 8000 chars).
  A submit-time hook must be fast on the silent path.
- Only the `_emit_*` helpers may write to stdout, they must write
  all-or-nothing (`json.dumps` then one `write`), and any subprocess in the hook
  path (`pbcopy`) must have stdout/stderr sent to `DEVNULL`. The invariant is
  "stdout is only ever valid-JSON-or-empty."
- Any code that reads persisted state (config, logs) must tolerate a corrupt/
  non-dict file without crashing.
