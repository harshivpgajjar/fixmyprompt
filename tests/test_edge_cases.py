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
    true if something was actually staged for the user to send. _stage_for_resend
    (used for the affirm/tip banners' original prompt, and for an AI rewrite)
    copies the text to the clipboard so the instruction is real."""

    def test_stage_for_resend_copies_the_exact_text(self):
        # cg.subprocess IS the shared global `subprocess` module (not a private
        # copy), so patching .run on it is process-global — must save/restore.
        cg = _load_gate_module()
        calls = []
        orig_run = subprocess.run
        cg.subprocess.run = lambda cmd, **kw: calls.append((cmd, kw.get("input")))
        try:
            cg._stage_for_resend("is fixmyprompt active?", {"inject": False})
        finally:
            cg.subprocess.run = orig_run
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ["pbcopy"])
        self.assertEqual(calls[0][1], "is fixmyprompt active?")

    def test_stage_for_resend_is_a_noop_under_the_test_seam(self):
        # never touch the real clipboard during automated tests
        cg = _load_gate_module()
        calls = []
        orig_run = subprocess.run
        cg.subprocess.run = lambda *a, **k: calls.append(1)
        os.environ["FIXMYPROMPT_FAKE_REFINE"] = "1"
        try:
            cg._stage_for_resend("anything", {"inject": False})
        finally:
            del os.environ["FIXMYPROMPT_FAKE_REFINE"]
            cg.subprocess.run = orig_run
        self.assertEqual(calls, [])

    def test_affirm_and_tip_banners_promise_the_clipboard(self):
        # the exact regression: these footers used to say "press Enter to send"
        # without ever staging anything — now they must say clipboard, and only
        # after _stage_for_resend has actually been called (verified above).
        cg = _load_gate_module()
        self.assertIn("clipboard", cg._banner("looks fine", "", kind="affirm").lower())
        self.assertIn("clipboard", cg._banner("some tip", "", kind="tip").lower())


def _timed(fn) -> float:
    t = time.time()
    fn()
    return time.time() - t


if __name__ == "__main__":
    unittest.main()
