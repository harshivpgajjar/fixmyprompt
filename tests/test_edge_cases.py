"""Regression tests for edge cases surfaced by adversarial code review:
  - ReDoS: a large single-line paste must not stall the classifier (submit hook).
  - config.save() must not crash on a corrupt (non-dict) config.json.
  - Image preservation: a submission carrying an image must never be BLOCKED
    (blocking discards the attachment — the user would have to re-attach it).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fixmyprompt import config, scorer

GATE = Path(__file__).resolve().parent.parent / "bin" / "coach_gate.py"


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("coach_gate_mod", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LargePromptPerfTest(unittest.TestCase):
    """A long single-line paste (minified JS, base64, a JWT, a long dotted
    identifier or path chain) used to hit O(n²) backtracking in the lexicon
    regexes and freeze the submit hook. These tests exercise the pathological
    regexes DIRECTLY (bypassing classify's 8000-char cap) so they would fail if
    the bounding fix were reverted — plus the through-classify defense."""

    # --- direct-regex ReDoS guards (would catch a reverted bounding fix) ---
    def test_reference_regex_no_catastrophic_backtracking(self):
        evil = "a" * 100000 + ".x"   # long non-space run then a near-miss TLD dot
        self.assertLess(_timed(lambda: scorer._REFERENCE.search(evil)), 0.5)

    def test_constraint_regex_no_catastrophic_backtracking(self):
        for evil in ("a." * 6000, "aB3-_d." * 2000):  # dotted chain / JWT-shape
            dt = _timed(lambda e=evil: scorer._CONSTRAINT.search(e))
            self.assertLess(dt, 0.5, f"_CONSTRAINT backtracks on {evil[:8]}…: {dt:.2f}s")

    def test_classify_near_miss_tld_and_dotted_chain_are_fast(self):
        # realistic shapes THROUGH classify (exercises the 8000-char truncation
        # defense on inputs that actually enter the pathological branch)
        for blob in ("token " + "a" * 60000 + ".xyz", "attr " + "a." * 30000):
            self.assertLess(_timed(lambda b=blob: scorer.classify(b)), 0.6, blob[:20])

    def test_classify_large_single_line_is_fast(self):
        for blob in ("a=1;" * 30000, "x" * 120000, "/a/b.c/d" * 15000):
            dt = _timed(lambda b=blob: scorer.classify(b))
            self.assertLess(dt, 1.0, f"classify() took {dt:.2f}s on {len(blob)} chars")

    def test_true_word_count_survives_the_scan_bound(self):
        # the regexes scan a bounded copy, but word_count reflects the full text
        self.assertEqual(scorer.classify("word " * 5000)["word_count"], 5000)

    def test_reference_and_filename_still_detected_after_bounding(self):
        # bounding the pre-dot runs must not break legitimate detection
        self.assertTrue(scorer.classify("make it like apple.com")["has_reference"])
        self.assertTrue(scorer.classify("similar to the attached screenshot")["has_reference"])
        # _CONSTRAINT filename branch still fires on real paths
        self.assertTrue(scorer._CONSTRAINT.search("edit src/components/Hero.tsx"))
        self.assertTrue(scorer._CONSTRAINT.search("~/.config/app.py"))


class ConfigCorruptionTest(unittest.TestCase):
    """A hand-edited or half-written config.json that isn't a dict must not
    crash the CLI (on/off/mode/tutorial/daemon all call save())."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._orig = (config.RUNTIME_DIR, config.CONFIG_PATH)
        config.RUNTIME_DIR = Path(self._dir)
        config.CONFIG_PATH = Path(self._dir) / "config.json"

    def tearDown(self):
        config.RUNTIME_DIR, config.CONFIG_PATH = self._orig

    def test_save_survives_non_dict_config(self):
        config.CONFIG_PATH.write_text("[1, 2, 3]")  # a JSON array, not an object
        result = config.save({"mode": "off"})        # must not raise
        self.assertIsInstance(result, dict)
        self.assertEqual(result["mode"], "off")

    def test_save_survives_garbage_config(self):
        config.CONFIG_PATH.write_text("not json at all {{{")
        result = config.save({"mode": "always"})
        self.assertEqual(result["mode"], "always")

    def test_load_survives_non_dict_config(self):
        config.CONFIG_PATH.write_text("[1, 2, 3]")   # a JSON array, not an object
        cfg = config.load()                            # must not raise
        self.assertIsInstance(cfg, dict)
        self.assertEqual(cfg["mode"], config.DEFAULTS["mode"])  # falls back to defaults


class ScorelogCoercionTest(unittest.TestCase):
    """scorelog.read() must tolerate a hand-corrupted log line (non-numeric/null
    ts, a non-dict record, garbage) without crashing report/progress/streak."""

    def setUp(self):
        from fixmyprompt import scorelog
        self._sl = scorelog
        self._dir = tempfile.mkdtemp()
        self._orig = scorelog.LOG_PATH
        scorelog.LOG_PATH = Path(self._dir) / "prompt-log.jsonl"

    def tearDown(self):
        self._sl.LOG_PATH = self._orig

    def test_read_coerces_bad_ts_and_skips_garbage(self):
        self._sl.LOG_PATH.write_text(
            '{"ts": "not-a-number", "action": "coach"}\n'
            '{"ts": null, "action": "pass"}\n'
            'this is not json at all\n'
            '[1,2,3]\n'
            '{"ts": 1783398216.3, "action": "edit"}\n'
        )
        recs = self._sl.read()  # must not raise
        self.assertEqual(len(recs), 3)                 # 2 coerced + 1 valid; garbage/list skipped
        self.assertTrue(all(isinstance(r["ts"], float) for r in recs))


class ImageAttachmentTest(unittest.TestCase):
    """Blocking a submission discards it, and a hook cannot re-inject a pasted
    image — so image-bearing prompts must NEVER be blocked (else the user loses
    the image on resubmit). They are coached non-blockingly or passed through."""

    def _gate(self, prompt, mode="always"):
        home = tempfile.mkdtemp()
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_MODE": mode,
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": "",
               "PATH": os.path.join(home, "nobin")}
        out = subprocess.run([sys.executable, str(GATE)],
                             input=json.dumps({"prompt": prompt, "session_id": "img"}),
                             capture_output=True, text=True, env=env)
        return out.stdout.strip()

    def _gate_raw(self, payload, mode="always"):
        home = tempfile.mkdtemp()
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_MODE": mode,
               "PCOACH_COOLDOWN": "0", "ANTHROPIC_API_KEY": ""}
        return subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env).stdout.strip()

    def test_image_engaging_prompt_whispers_never_blocks(self):
        # a prompt that WOULD be coached, plus an image -> must ENGAGE (non-empty)
        # and be a non-blocking whisper, never a block. (Non-vacuous: asserts out.)
        out = self._gate("make the whole thing better and nicer somehow [Image #1]")
        self.assertTrue(out, "expected the gate to engage (whisper), not stay silent")
        data = json.loads(out)
        self.assertNotEqual(data.get("decision"), "block")
        self.assertIn("hookSpecificOutput", data)

    def test_image_never_blocks_across_realistic_markers(self):
        # includes the macOS screenshot filename shape that the old 24-char
        # marker bound missed, plus [Image: path] and <image>.
        for p in ["redo this whole thing better [Image #3]",
                  "improve all of it [pasted image 2]",
                  "match this [Screenshot 2026-07-07 at 10.14.32.png] exactly please",
                  "rebuild everything from [Image: /Users/x/shot.png] to spec",
                  "make it like the <image> mock, all of it"]:
            out = self._gate(p)
            if out:  # some may be silent (also image-safe); if engaged, never block
                self.assertNotEqual(json.loads(out).get("decision"), "block", p)

    def test_image_via_stdin_field_or_value_not_blocked(self):
        for payload in ({"prompt": "make this nicer overall and cleaner too",
                         "session_id": "z", "images": ["/tmp/x.png"]},
                        {"prompt": "improve the whole thing a lot",
                         "session_id": "z2", "attachment": "data:image/png;base64,iVBOR"}):
            out = self._gate_raw(payload)
            if out:
                self.assertNotEqual(json.loads(out).get("decision"), "block")

    def test_image_in_sigil_mode_not_blocked(self):
        out = self._gate("make this nicer [Image #4]", mode="sigil")
        if out:
            self.assertNotEqual(json.loads(out).get("decision"), "block")

    def test_textonly_vague_prompt_still_blocks(self):
        # regression: normal (no-image) coaching is unchanged
        out = self._gate("make the whole thing better and nicer somehow")
        self.assertTrue(out)
        self.assertEqual(json.loads(out).get("decision"), "block")

    def test_has_attachment_detection(self):
        cg = _load_gate_module()
        # markers, incl. long macOS screenshot filenames, [Image: path], <image>
        for p in ["do this [Image #2]", "see [Image]",
                  "[Screenshot 2026-07-07 at 10.14.32.png]",
                  "look at [screenshot from 2024-01-01 at 3.45.12 PM.png]",
                  "[Image: /Users/foo/bar/screenshot.png]", "here is an <image> tag"]:
            self.assertTrue(cg._has_attachment({}, p), p)
        # field keys and image-shaped field values
        self.assertTrue(cg._has_attachment({"images": ["a.png"]}, "go"))
        self.assertTrue(cg._has_attachment({"attachments": [1]}, "go"))
        self.assertTrue(cg._has_attachment({"foo": "data:image/png;base64,iVBOR"}, "go"))
        self.assertTrue(cg._has_attachment({"foo": "/tmp/shot.png"}, "go"))
        # negatives: prose mentioning images, empty fields
        for p in ["a prompt discussing images abstractly", "fix the login bug",
                  "make the navbar sticky on scroll"]:
            self.assertFalse(cg._has_attachment({}, p), p)
        self.assertFalse(cg._has_attachment({"images": []}, "no attachment here"))


class ResendStagingTest(unittest.TestCase):
    """Claude Code CLEARS the input box on a block — it does not restore the
    typed text. So a banner that says "press Enter to send it as-is" is only
    true if something was actually staged. The clipboard backend is
    cross-platform (pbcopy / Win32 ctypes+clip / xclip-xsel-wl-copy)."""

    def test_copy_via_pipes_the_exact_text(self):
        # platform-agnostic: _copy_via runs the first available command and pipes
        # the EXACT text (no mangling). Stub shutil.which so a fake tool "exists".
        import shutil
        cg = _load_gate_module()
        calls, orig_run, orig_which = [], subprocess.run, shutil.which
        cg.subprocess.run = lambda cmd, **kw: calls.append((cmd, kw.get("input")))
        shutil.which = lambda name: "/fake/" + name
        try:
            ok = cg._copy_via([["mycopy", "-x"]], "exact text ✓ café")
        finally:
            cg.subprocess.run, shutil.which = orig_run, orig_which
        self.assertTrue(ok)
        self.assertEqual(calls[0][0], ["mycopy", "-x"])
        self.assertEqual(calls[0][1], "exact text ✓ café")

    def test_copy_via_skips_missing_tools_and_reports_failure(self):
        import shutil
        cg = _load_gate_module()
        orig_which = shutil.which
        shutil.which = lambda name: None  # nothing available
        try:
            self.assertFalse(cg._copy_via([["nope1"], ["nope2"]], "x"))
        finally:
            shutil.which = orig_which

    def test_clipboard_returns_bool_and_never_raises(self):
        # on any platform, _clipboard returns a bool and doesn't blow up
        cg = _load_gate_module()
        self.assertIsInstance(cg._clipboard("hello"), bool)

    def test_win_clipboard_false_off_windows(self):
        # ctypes.windll doesn't exist off Windows -> graceful False, no raise
        cg = _load_gate_module()
        if sys.platform != "win32":
            self.assertFalse(cg._win_clipboard("x"))

    def test_stage_for_resend_is_a_noop_under_the_test_seam(self):
        # never touch the real clipboard during automated tests; returns True
        cg = _load_gate_module()
        calls, orig_run = [], subprocess.run
        cg.subprocess.run = lambda *a, **k: calls.append(1)
        os.environ["FIXMYPROMPT_FAKE_REFINE"] = "1"
        try:
            self.assertTrue(cg._stage_for_resend("anything", {"inject": False}))
        finally:
            del os.environ["FIXMYPROMPT_FAKE_REFINE"]
            cg.subprocess.run = orig_run
        self.assertEqual(calls, [])

    def test_all_no_rewrite_banners_offer_send_original(self):
        # affirm, tip, AND scaffold must all offer `[n ⏎]` to send the original —
        # never a bare "press Enter" that does nothing (the box is cleared).
        cg = _load_gate_module()
        for kind in ("affirm", "tip", "scaffold"):
            for staged in (True, False):
                self.assertIn("[n ", cg._banner("body", "", kind=kind, staged=staged).lower(), (kind, staged))

    def test_refined_banner_offers_send_refined_and_original(self):
        cg = _load_gate_module()
        b = cg._banner("REWRITE", "", kind="refined", staged=True).lower()
        self.assertIn("[y ", b)   # send refined
        self.assertIn("[n ", b)   # send yours unchanged

    def test_paste_key_is_platform_appropriate(self):
        cg = _load_gate_module()
        expected = "⌘V" if sys.platform == "darwin" else "Ctrl+V"
        self.assertEqual(cg._PASTE, expected)


class DaemonCapabilityTest(unittest.TestCase):
    def test_daemon_supported_flag_matches_platform(self):
        from fixmyprompt import daemon
        self.assertEqual(daemon.DAEMON_SUPPORTED,
                         hasattr(__import__("socket"), "AF_UNIX") and hasattr(os, "fork"))

    def test_lifecycle_is_a_safe_noop_when_unsupported(self):
        # simulate an unsupported platform: start/stop/is_running must not raise
        from fixmyprompt import daemon
        orig = daemon.DAEMON_SUPPORTED
        daemon.DAEMON_SUPPORTED = False
        try:
            self.assertFalse(daemon.is_running())
            self.assertFalse(daemon.start())
            self.assertFalse(daemon.stop())
        finally:
            daemon.DAEMON_SUPPORTED = orig


class SystemInjectedTest(unittest.TestCase):
    """Background-agent task-notifications, system-reminders, and slash-command
    output reach the UserPromptSubmit hook as "prompts" the user never typed.
    The coach must pass them straight through — never block/coach them."""

    def _gate(self, prompt):
        home = tempfile.mkdtemp()
        (Path(home) / "config.json").write_text('{"mode":"always","tutorial":true}')
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_COOLDOWN": "0",
               "ANTHROPIC_API_KEY": "", "PATH": os.path.join(home, "nobin")}
        return subprocess.run([sys.executable, str(GATE)],
                              input=json.dumps({"prompt": prompt, "session_id": "s"}),
                              capture_output=True, text=True, env=env).stdout.strip()

    def test_system_injected_prompts_pass_through(self):
        for p in [
            "<task-notification>\n<task-id>abc</task-id>\n<status>completed</status>\n"
            "<result>Build the app yourself in Next.js. Here is the report.</result>\n</task-notification>",
            "<system-reminder>\nUse this context. build a dashboard.\n</system-reminder>",
            "[SYSTEM NOTIFICATION - NOT USER INPUT]\nAutomated event. make me a thing.",
            "<command-name>/model</command-name>\n<command-message>model</command-message>",
        ]:
            self.assertEqual(self._gate(p), "", f"system prompt was coached: {p[:40]}")

    def test_real_prompt_that_merely_mentions_the_words_still_coaches(self):
        # a genuine build prompt using 'notification'/'task' in prose is NOT a
        # system message — it must still be coached.
        out = self._gate("build a system that sends a notification when a task finishes")
        self.assertTrue(out)
        self.assertEqual(json.loads(out).get("decision"), "block")

    def test_is_system_injected_unit(self):
        cg = _load_gate_module()
        self.assertTrue(cg._is_system_injected("<task-notification>\n..."))
        self.assertTrue(cg._is_system_injected("  <system-reminder> ..."))
        self.assertTrue(cg._is_system_injected("[SYSTEM NOTIFICATION - NOT USER INPUT]"))
        self.assertFalse(cg._is_system_injected("send a task-notification email to the user"))
        self.assertFalse(cg._is_system_injected("build me a dashboard"))


class ScaffoldStagingTest(unittest.TestCase):
    """A scaffold block must offer a REAL send affordance (clipboard, or an
    honest 'retype') — never a bare 'press ⏎ to send' that does nothing because
    Claude Code cleared the input box. This was the live regression: scaffolds
    were the one banner kind that never staged the prompt anywhere."""

    def test_scaffold_footer_offers_a_real_affordance(self):
        home = tempfile.mkdtemp()
        (Path(home) / "config.json").write_text('{"mode":"always","tutorial":false}')
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_COOLDOWN": "0",
               "ANTHROPIC_API_KEY": "", "PATH": os.path.join(home, "nobin")}
        out = subprocess.run(
            [sys.executable, str(GATE)],
            input=json.dumps({"prompt": "make the whole dashboard thing better somehow",
                              "session_id": "s"}),
            capture_output=True, text=True, env=env).stdout.strip()
        self.assertTrue(out)
        reason = json.loads(out)["reason"].lower()
        self.assertIn("make this sharper", reason)                 # it IS a scaffold
        self.assertIn("[n ", reason)                               # a real send affordance
        self.assertNotIn("then press ⏎ to send", reason)           # never the dead instruction


class ClarifyingQuestionTest(unittest.TestCase):
    """Live regression: for "make it better", the refiner returned a pure
    clarifying question ("Which 'it'? ... Be specific.") in the `refined`
    field, and the banner presented it as a "refined prompt (copied to
    clipboard)" with a [y] send option — nonsensical, since sending a question
    back to Claude isn't a prompt. A genuine rewrite must still offer [y]."""

    def _gate(self, prompt, refined_text):
        home = tempfile.mkdtemp()
        (Path(home) / "config.json").write_text('{"mode":"always","tutorial":true}')
        fake = json.dumps({"needs_refinement": True, "mode": "execute", "refined": refined_text})
        env = {**os.environ, "FIXMYPROMPT_HOME": home, "PCOACH_COOLDOWN": "0",
               "ANTHROPIC_API_KEY": "sk-x", "FIXMYPROMPT_FAKE_REFINE": fake}
        out = subprocess.run([sys.executable, str(GATE)],
                             input=json.dumps({"prompt": prompt, "session_id": "s"}),
                             capture_output=True, text=True, env=env).stdout.strip()
        return json.loads(out)["reason"]

    def test_pure_question_response_becomes_a_scaffold_not_a_fake_refined_prompt(self):
        reason = self._gate("make it better",
                            "Which 'it'? Medicoz dashboard, GoaSorted accounting tool, "
                            "Slacker, design system? What's broken or missing? Be specific.")
        self.assertIn("make this sharper", reason.lower())   # scaffold framing, not "refined prompt"
        self.assertNotIn("[y ", reason)                      # never offer to "send" a question
        self.assertNotIn("copied to clipboard", reason.lower())

    def test_rewrite_that_trails_into_subquestions_still_offers_y(self):
        # leads with an actual imperative restatement -> still a real, sendable rewrite
        reason = self._gate("Refactor and re-architect the reporting pipeline for scale.",
                            "Refactor reporting pipeline for scale. Which pipeline? "
                            "Medicoz analytics or GoaSorted reports? Target: throughput or cost?")
        self.assertIn("[y ", reason)
        self.assertIn("copied to clipboard", reason.lower())

    def test_is_clarifying_question_unit(self):
        cg = _load_gate_module()
        self.assertTrue(cg._is_clarifying_question("Which 'it'? Be specific."))
        self.assertTrue(cg._is_clarifying_question("What is the expected input format?"))
        self.assertFalse(cg._is_clarifying_question("Refactor the pipeline. Which one?"))
        self.assertFalse(cg._is_clarifying_question("Add a dark-mode toggle that persists."))
        self.assertFalse(cg._is_clarifying_question(""))


def _timed(fn) -> float:
    t = time.time()
    fn()
    return time.time() - t


if __name__ == "__main__":
    unittest.main()
