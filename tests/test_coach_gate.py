"""Exhaustive tests for bin/coach_gate.py — the UserPromptSubmit hook.

Runs the REAL hook as a subprocess (stdin JSON in, stdout/exit-code out), so
these tests exercise the actual platform contract, not internals:

  - pass-through : empty stdout, exit 0
  - block        : {"decision": "block", "reason": ...}, exit 0
  - accept       : {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                    "additionalContext": ...}}, exit 0

Isolation: every scenario gets a fresh temp FIXMYPROMPT_HOME (state + logs) and a
minimal environment (stripped PATH so the real `claude` binary is unreachable,
empty ANTHROPIC_API_KEY, no TMUX). The network refiner is replaced through the
env-gated test seam FIXMYPROMPT_FAKE_REFINE (JSON result, or "RAISE" to simulate
a crash); the seam also tallies each consultation to fake-refine-calls so tests
can prove the refiner was NOT called on silence paths and that the sigil was
stripped before refinement.

The two load-bearing invariants get explicit tests:
  - LOOP-PROOF: two submissions in a row can never both be blocks
    (test_never_two_blocks_in_a_row, test_second_submission_never_blocks).
  - FAIL-OPEN: refiner crash / junk / decline, unreachable backends, malformed
    stdin -> pass-through, exit 0, never a block, never an error
    (test_refiner_* , test_real_refiner_unreachable_*, test_malformed_stdin_*).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = str(REPO / "bin" / "coach_gate.py")

# A realistic under-specified execute-mode prompt: >= 12 words, execute verb
# ("fix"), design-ish noun ("page"), no constraints / acceptance criteria ->
# quality well below the 0.7 coaching threshold.
ROUGH = "fix the login page it is broken somehow and users cannot get in"

# Deliberately low token overlap with ROUGH (~0.12) so the backstop never
# confounds block tests; verbatim resubmission of it overlaps 1.0.
REFINED = (
    "Debug and repair the authentication flow: reproduce the failure, "
    "add a regression test, confirm sign-in succeeds."
)
TIP = "Say what working means before asking for a fix."

FAKE_OK = json.dumps(
    {"needs_refinement": True, "mode": "execute", "refined": REFINED, "tip": TIP}
)
FAKE_DECLINE = json.dumps(
    {"needs_refinement": False, "mode": "execute", "refined": "", "tip": ""}
)

# Sentinel: distinguishes "no fake -> use the real refiner" from "fake armed".
REAL_REFINER = object()


class CoachGateTest(unittest.TestCase):
    maxDiff = None

    # ------------------------------------------------------------------ rig

    def setUp(self):
        self._dirs = []
        self.home = self.fresh_home()

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def fresh_home(self):
        """A brand-new isolated FIXMYPROMPT_HOME (also used as HOME and PATH base)."""
        d = tempfile.mkdtemp(prefix="fixmyprompt-gate-test-")
        os.makedirs(os.path.join(d, "nobin"), exist_ok=True)  # empty PATH dir
        self._dirs.append(d)
        return d

    def run_gate(
        self,
        prompt=None,
        session="s1",
        mode="always",
        fake=FAKE_OK,
        env_extra=None,
        stdin_raw=None,
        home=None,
    ):
        """Run the hook once. Returns parsed stdout JSON (dict) or None (empty).

        Asserts the universal contract on EVERY invocation: exit code 0, empty
        stderr, and stdout that is either empty or a single valid JSON object.
        """
        home = home or self.home
        env = {
            "HOME": home,  # keep refiner personalization away from real files
            "FIXMYPROMPT_HOME": home,
            "PCOACH_MODE": mode,
            "PCOACH_COOLDOWN": "0",  # cooldown gets its own dedicated test
            "ANTHROPIC_API_KEY": "",  # empty -> refiner API backend disabled
            "PATH": os.path.join(home, "nobin"),  # `claude` CLI unreachable
        }
        if fake is not REAL_REFINER and fake is not None:
            env["FIXMYPROMPT_FAKE_REFINE"] = fake
        if env_extra:
            env.update(env_extra)
        payload = (
            stdin_raw
            if stdin_raw is not None
            else json.dumps({"prompt": prompt, "session_id": session})
        )
        proc = subprocess.run(
            [sys.executable, GATE],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        # -- universal hook contract, asserted for every branch of every test
        self.assertEqual(
            proc.returncode, 0, f"hook must always exit 0; stderr:\n{proc.stderr}"
        )
        self.assertEqual(proc.stderr, "", f"hook must not write stderr:\n{proc.stderr}")
        if not proc.stdout.strip():
            return None
        parsed = json.loads(proc.stdout)  # raises -> test failure: invalid JSON
        self.assertIsInstance(parsed, dict, "stdout JSON must be a single object")
        return parsed

    # --------------------------------------------------------- outcome asserts

    def assertPassThrough(self, out):
        self.assertIsNone(out, f"expected pass-through (no output), got: {out}")

    def assertBlock(self, out):
        self.assertIsNotNone(out, "expected a block, got pass-through")
        self.assertEqual(out.get("decision"), "block", f"expected block, got: {out}")
        reason = out.get("reason")
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.strip(), "block reason must be non-empty")
        self.assertNotIn("hookSpecificOutput", out)
        return reason

    def assertAccept(self, out):
        self.assertIsNotNone(out, "expected additionalContext, got pass-through")
        self.assertNotIn("decision", out, f"accept must NOT be a block: {out}")
        hso = out.get("hookSpecificOutput")
        self.assertIsInstance(hso, dict, f"expected hookSpecificOutput, got: {out}")
        self.assertEqual(hso.get("hookEventName"), "UserPromptSubmit")
        ctx = hso.get("additionalContext")
        self.assertIsInstance(ctx, str)
        self.assertTrue(ctx.strip())
        return ctx

    # ------------------------------------------------------------- log helpers

    def actions(self, home=None):
        """The ACTION_* sequence recorded in prompt-log.jsonl, in order."""
        p = Path(home or self.home) / "prompt-log.jsonl"
        if not p.exists():
            return []
        return [
            json.loads(line)["action"]
            for line in p.read_text().splitlines()
            if line.strip()
        ]

    def refine_bodies(self, home=None):
        """Bodies the (fake) refiner was consulted with, in order."""
        p = Path(home or self.home) / "fake-refine-calls"
        if not p.exists():
            return []
        return p.read_text().splitlines()

    def arm_pending(self, session, refined, ts=None, home=None):
        """Write a pending flag directly (for TTL / malformed-content tests)."""
        d = Path(home or self.home) / "pending"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{session}.json").write_text(
            json.dumps({"ts": ts if ts is not None else time.time(), "refined": refined})
        )

    # =================================================================
    # Silence: pass-through with NO refiner call (invariant 4)
    # =================================================================

    def _assert_silent(self, prompts, mode="always", env_extra=None):
        """Each prompt passes through, logs exactly one 'pass', refiner untouched."""
        for i, prompt in enumerate(prompts, start=1):
            with self.subTest(prompt=prompt):
                out = self.run_gate(prompt, mode=mode, env_extra=env_extra)
                self.assertPassThrough(out)
                self.assertEqual(self.actions(), ["pass"] * i)
        self.assertEqual(self.refine_bodies(), [], "refiner must not be consulted")

    def test_continuations_pass_silently(self):
        self._assert_silent(["yes", "ok", "continue", "run it", "do that", "go"])

    def test_commands_pass_silently(self):
        self._assert_silent(
            [
                "/refine fix the login page it is broken somehow and users cannot get in",
                "!rm -rf the build cache because it is stale and breaking everything now",
                "#remember the deploy script lives in scripts and must never be renamed",
            ]
        )

    def test_short_prompt_passes_silently(self):
        # execute-mode but under min_words (default 4 -> ≤3-word asks stay silent)
        self._assert_silent(["fix the login"])

    def test_explore_prompt_passes_silently(self):
        self._assert_silent(
            [
                "give me some options for the hero section something different "
                "and creative would be amazing"
            ]
        )

    def test_paste_passes_silently(self):
        paste = (
            "fix the failing build pipeline for me right now please and thanks\n"
            "```\n"
            'Traceback (most recent call last):\n  File "app.py", line 1\n'
            "TypeError: boom\n"
            "```"
        )
        self._assert_silent([paste])

    def test_mode_off_passes_silently(self):
        self._assert_silent([ROUGH], mode="off")

    def test_unknown_mode_passes_silently(self):
        # A typo'd / future mode must fail quiet, not coach everything.
        self._assert_silent([ROUGH], mode="gentle")

    def test_cooldown_active_passes_silently(self):
        cd = Path(self.home) / "cooldown"
        cd.mkdir(parents=True, exist_ok=True)
        (cd / "s1").write_text(str(time.time()))  # coached moments ago
        out = self.run_gate(ROUGH, env_extra={"PCOACH_COOLDOWN": "90"})
        self.assertPassThrough(out)
        self.assertEqual(self.actions(), ["pass"])
        self.assertEqual(self.refine_bodies(), [])

    # =================================================================
    # Block path (mode=always, refinement available)
    # =================================================================

    def test_rough_execute_prompt_blocks(self):
        # Realistic stdin payload with the extra fields the platform sends.
        out = self.run_gate(
            stdin_raw=json.dumps(
                {
                    "prompt": ROUGH,
                    "session_id": "sess-abc",
                    "transcript_path": "/tmp/t.jsonl",
                    "cwd": "/tmp",
                    "hook_event_name": "UserPromptSubmit",
                    "permission_mode": "default",
                }
            )
        )
        reason = self.assertBlock(out)
        self.assertIn(REFINED, reason, "block reason must show the refined prompt")
        self.assertIn(TIP, reason, "block reason should carry the teaching tip")
        self.assertEqual(self.actions(), ["coach"])
        self.assertEqual(self.refine_bodies(), [ROUGH])

    # =================================================================
    # Invariant 2: block -> bare confirm -> ACCEPT (additionalContext)
    # =================================================================

    def test_block_then_accept_variants(self):
        # Cooldown is 0 in the rig, so each round can block again.
        for confirm in ["y", "Yes!", "OK.", "send it", "Ship it!", "yeah"]:
            with self.subTest(confirm=confirm):
                home = self.fresh_home()
                self.assertBlock(self.run_gate(ROUGH, home=home))
                out = self.run_gate(confirm, home=home)
                ctx = self.assertAccept(out)
                self.assertIn(REFINED, ctx)
                self.assertEqual(self.actions(home), ["coach", "accept"])

    def test_accept_context_instructs_model(self):
        self.assertBlock(self.run_gate(ROUGH))
        ctx = self.assertAccept(self.run_gate("y"))
        self.assertIn(REFINED, ctx)
        # It must carry an instruction, not just the bare refined text ...
        self.assertGreater(len(ctx), len(REFINED))
        # ... telling the model to treat the refined text as the actual request.
        self.assertIn("request", ctx.lower())
        self.assertIn("treat", ctx.lower())

    # =================================================================
    # Invariant 3: block -> anything else -> pass-through unchanged
    # =================================================================

    def test_block_then_edit_passes_through(self):
        edits = [
            "actually fix only the session cookie expiry in auth.py and add a test",
            REFINED,  # the cmd-V paste flow: pasted refined text sends as-is
            "no",  # a decline is not a confirm
            "n",
            "",  # empty resubmission
            "yes please do the whole thing",  # confirm-ish but not bare
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                home = self.fresh_home()
                self.assertBlock(self.run_gate(ROUGH, home=home))
                out = self.run_gate(edit, home=home)
                self.assertPassThrough(out)
                self.assertEqual(self.actions(home), ["coach", "edit"])

    # =================================================================
    # Invariant 1: LOOP-PROOF — never two blocks in a row
    # =================================================================

    def test_never_two_blocks_in_a_row(self):
        # Same rough prompt three times, same session, cooldown disabled:
        # the strongest possible provocation for a refine loop.
        outcomes = [self.run_gate(ROUGH) for _ in range(3)]
        is_block = [o is not None and o.get("decision") == "block" for o in outcomes]
        for i in range(len(is_block) - 1):
            self.assertFalse(
                is_block[i] and is_block[i + 1],
                f"submissions {i + 1} and {i + 2} were BOTH blocks: loop-proofing broken",
            )
        # And the exact expected shape: block, consume-and-pass, block again.
        self.assertEqual(is_block, [True, False, True])
        self.assertEqual(self.actions(), ["coach", "edit", "coach"])
        self.assertEqual(len(self.refine_bodies()), 2)  # refiner skipped on #2

    def test_second_submission_never_blocks(self):
        # Whatever follows a block — confirm, decline, edit, empty, or another
        # coachable rough prompt — the second submission can never be a block.
        seconds = [
            ROUGH,
            "y",
            "no",
            "",
            "build me another different widget that does many things quickly "
            "and cleanly today",
        ]
        for second in seconds:
            with self.subTest(second=second):
                home = self.fresh_home()
                self.assertBlock(self.run_gate(ROUGH, home=home))
                out = self.run_gate(second, home=home)
                if out is not None:
                    self.assertNotEqual(
                        out.get("decision"),
                        "block",
                        f"second submission {second!r} was blocked: loop-proofing broken",
                    )
                    self.assertAccept(out)  # only legal non-empty output: accept

    def test_pending_honored_even_after_mode_change(self):
        # Block in "always", then the user (or a race) flips mode to "off":
        # the armed confirm must still work — resubmit branch precedes mode checks.
        self.assertBlock(self.run_gate(ROUGH, mode="always"))
        ctx = self.assertAccept(self.run_gate("y", mode="off"))
        self.assertIn(REFINED, ctx)
        self.assertEqual(self.actions(), ["coach", "accept"])

    def test_pending_with_junk_refined_never_accepts(self):
        # A pending flag with nothing sendable must not emit an empty accept.
        for junk in ["", "   ", None, 42]:
            with self.subTest(junk=junk):
                home = self.fresh_home()
                self.arm_pending("s1", junk, home=home)
                out = self.run_gate("y", home=home)
                self.assertPassThrough(out)
                self.assertEqual(self.actions(home), ["edit"])

    def test_pending_expired_is_consumed_and_gate_resumes(self):
        self.arm_pending("s1", REFINED, ts=time.time() - 999999)
        out = self.run_gate("y")  # stale flag -> "y" is just a continuation
        self.assertPassThrough(out)
        self.assertEqual(self.actions(), ["pass"])
        self.assertFalse(
            (Path(self.home) / "pending" / "s1.json").exists(),
            "stale pending flag must still be consumed on read",
        )

    def test_hostile_session_id_roundtrip(self):
        sid = "../../etc/passwd"
        self.assertBlock(self.run_gate(ROUGH, session=sid))
        ctx = self.assertAccept(self.run_gate("y", session=sid))
        self.assertIn(REFINED, ctx)

    # =================================================================
    # Invariant 5: FAIL-OPEN — refiner trouble can never block or error
    # =================================================================

    def test_refiner_raises_fail_open(self):
        out = self.run_gate(ROUGH, fake="RAISE")
        self.assertPassThrough(out)
        self.assertEqual(self.actions(), ["pass"], "the pass must still be logged")

    def test_refiner_declines_fail_open(self):
        out = self.run_gate(ROUGH, fake=FAKE_DECLINE)
        self.assertPassThrough(out)
        self.assertEqual(self.actions(), ["pass"])

    def test_refiner_flags_but_empty_refined_fail_open(self):
        fake = json.dumps({"needs_refinement": True, "refined": "   ", "tip": "x"})
        out = self.run_gate(ROUGH, fake=fake)
        self.assertPassThrough(out)
        self.assertEqual(self.actions(), ["pass"])

    def test_refiner_junk_results_fail_open(self):
        junk = [
            "[1, 2, 3]",  # valid JSON, wrong shape
            '"just a string"',
            "{{{not json at all",
            json.dumps({"needs_refinement": True, "refined": 42}),  # wrong type
            "null",
        ]
        for fake in junk:
            with self.subTest(fake=fake):
                home = self.fresh_home()
                out = self.run_gate(ROUGH, fake=fake, home=home)
                self.assertPassThrough(out)
                self.assertEqual(self.actions(home), ["pass"])

    def test_real_refiner_no_key_uses_local_scaffold(self):
        # No test seam, empty ANTHROPIC_API_KEY, `claude` CLI unreachable: this
        # is LOCAL mode (the keyless default). A coachable execute prompt must
        # get an instant deterministic scaffold block (not an LLM call, never a
        # crash), and it must NOT be sendable via `y` (it has <placeholders>).
        out = self.run_gate(ROUGH, fake=REAL_REFINER)
        reason = self.assertBlock(out)
        self.assertIn("<", reason)  # scaffold placeholder present
        self.assertEqual(self.actions(), ["coach"])

    def test_malformed_stdin_fail_open(self):
        payloads = [
            "this is not json {{{",
            "",
            "[1, 2, 3]",  # valid JSON, not an object
            "null",
            json.dumps({"prompt": 42, "session_id": ["x"]}),  # wrong field types
            json.dumps({"no_prompt_key": True}),
        ]
        for raw in payloads:
            with self.subTest(stdin=raw):
                home = self.fresh_home()
                out = self.run_gate(stdin_raw=raw, home=home)
                self.assertPassThrough(out)  # exit 0 + JSON-or-empty via run_gate

    def test_nonstring_session_id_still_blocks_rough_prompt(self):
        out = self.run_gate(
            stdin_raw=json.dumps({"prompt": ROUGH, "session_id": {"weird": 1}})
        )
        self.assertBlock(out)  # falls back to "nosession", flow intact

    # =================================================================
    # Invariant 6: sigil mode
    # =================================================================

    def test_sigil_prompt_engages_and_sigil_is_stripped(self):
        out = self.run_gate("?? " + ROUGH, mode="sigil")
        reason = self.assertBlock(out)
        self.assertIn(REFINED, reason)
        self.assertEqual(
            self.refine_bodies(), [ROUGH], "sigil must be stripped before refine"
        )
        self.assertEqual(self.actions(), ["coach"])

    def test_sigil_without_sigil_passes_silently(self):
        out = self.run_gate(ROUGH, mode="sigil")
        self.assertPassThrough(out)
        self.assertEqual(self.actions(), ["pass"])
        self.assertEqual(self.refine_bodies(), [], "refiner must not be consulted")

    def test_sigil_custom_sigil(self):
        env = {"PCOACH_SIGIL": ">>"}
        out = self.run_gate(">>" + ROUGH, mode="sigil", env_extra=env)
        self.assertBlock(out)
        self.assertEqual(self.refine_bodies(), [ROUGH])
        # The default sigil no longer opts in once a custom one is configured.
        home2 = self.fresh_home()
        out2 = self.run_gate("?? " + ROUGH, mode="sigil", env_extra=env, home=home2)
        self.assertPassThrough(out2)
        self.assertEqual(self.refine_bodies(home2), [])

    def test_sigil_block_then_accept(self):
        self.assertBlock(self.run_gate("?? " + ROUGH, mode="sigil"))
        ctx = self.assertAccept(self.run_gate("y", mode="sigil"))
        self.assertIn(REFINED, ctx)
        self.assertEqual(self.actions(), ["coach", "accept"])

    # =================================================================
    # Invariant 7: backstop — late paste of refined text passes
    # =================================================================

    def test_backstop_late_paste_passes(self):
        # Block in one session; the refined text pasted in ANOTHER session
        # (pending is session-scoped, backstop is global) must pass through.
        self.assertBlock(self.run_gate(ROUGH, session="s1"))
        out = self.run_gate(REFINED, session="s2")
        self.assertPassThrough(out)
        # "edit" (not "pass") proves the BACKSTOP caught it, not the gate.
        self.assertEqual(self.actions(), ["coach", "edit"])
        self.assertEqual(len(self.refine_bodies()), 1, "no second refiner call")

    def test_backstop_expired_does_not_apply(self):
        (Path(self.home) / "backstop.json").write_text(
            json.dumps({"ts": time.time() - 999999, "refined": REFINED})
        )
        out = self.run_gate(REFINED)
        self.assertPassThrough(out)
        # Gate pass (quality is high for the refined text), not a backstop edit.
        self.assertEqual(self.actions(), ["pass"])

    # =================================================================
    # Invariant 8: exactly one log record per submission, right ACTION_*
    # =================================================================

    def test_full_cycle_logs_every_action_exactly_once(self):
        sequence = [
            (ROUGH, "coach"),  # block
            ("y", "accept"),  # confirm -> refined sent
            (ROUGH, "coach"),  # cooldown 0 -> coaches again
            ("hold on i want to rewrite this myself completely", "edit"),
            ("ok", "pass"),  # plain continuation, nothing pending
        ]
        for prompt, _ in sequence:
            self.run_gate(prompt)
        self.assertEqual(self.actions(), [a for _, a in sequence])


if __name__ == "__main__":
    unittest.main()
