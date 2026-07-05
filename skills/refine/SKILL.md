---
name: refine
description: Refine a rough prompt before you send it — get a sharper, send-ready version plus one teaching point, without leaving your seat. Use when the user types "/refine <rough prompt>", asks to improve/sharpen a prompt, or says "how should I have asked this".
---

# Refine this prompt

The user handed you a rough draft of a prompt they were about to send (as the argument). Do NOT execute it — coach it.

Read `~/.claude/memory/core.md` and `~/.claude/memory/design-taste.md` if present, so your refinement respects their stack, voice, and taste. Then:

1. **Detect the mode.**
   - **Explore** (open-ended ideation: "blow me away", "give me options", "I don't know what I want"): this is valid discovery — do NOT force a spec. At most, turn it into a *productive* exploration ("5 distinct hero directions, one screen each, maximally different, seeded from my taste file"). If it's already a good explore ask, say so and stop.
   - **Execute** (concrete build/fix): check for the gaps that cause rework — a concrete "done means…", the target file/surface, scope boundaries, constraints they clearly hold but omitted.

2. **Produce a refined, send-ready prompt** that preserves their voice and language (keep Hinglish / casual tone; never "correct" typos). Add only scaffolding their words imply — never invent product decisions; surface those as a question instead.

3. **Give exactly one teaching point** — a specific, transferable prompting lesson tied to THIS prompt, not generic advice.

Format your reply:
```
mode: <explore|execute>

── refined ──
<the send-ready prompt>

why: <one teaching point>
```

If the draft is already strong, say so plainly and don't pad it. End by reminding them they can paste the refined version and send, or tell you to run it directly.
