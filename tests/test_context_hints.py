"""Tests for context hints — criteria memory + per-project tuning (seeding,
matching, learning heuristics, secret hygiene, and the composed blocks)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SHOP_HINT = "which storefront — web or mobile app?"


class ContextHintsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["FIXMYPROMPT_HOME"] = self.tmp
        # import fresh so RUNTIME_DIR picks up the temp home
        import importlib
        import fixmyprompt.config as c
        importlib.reload(c)
        import fixmyprompt.context_hints as ch
        importlib.reload(ch)
        self.ch = ch

    def tearDown(self):
        os.environ.pop("FIXMYPROMPT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- seeding ---------------------------------------------------------

    def test_seeding_creates_stores_with_defaults(self):
        crits = self.ch.known_criteria()
        self.assertEqual(crits, self.ch.DEFAULT_CRITERIA)
        self.assertIn("no horizontal scroll at 390px", crits)
        self.assertTrue((Path(self.tmp) / "criteria.json").exists())
        # projects.json ships empty — it's a learned store, not a preset list
        self.ch.project_hint("/anywhere")  # first read seeds projects.json
        self.assertTrue((Path(self.tmp) / "projects.json").exists())
        seeded = json.loads((Path(self.tmp) / "projects.json").read_text())
        self.assertEqual(seeded, {})
        self.assertEqual(self.ch.DEFAULT_PROJECTS, {})

    def test_seeding_is_idempotent_and_preserves_edits(self):
        self.ch.known_criteria()
        path = Path(self.tmp) / "criteria.json"
        custom = {"criteria": ["my own bar"]}
        path.write_text(json.dumps(custom))
        # a valid store is respected, not re-seeded over
        self.assertEqual(self.ch.known_criteria(), ["my own bar"])

    def test_corrupt_criteria_store_reseeds_to_defaults(self):
        (Path(self.tmp) / "criteria.json").write_text("{not json")
        self.assertEqual(self.ch.known_criteria(), self.ch.DEFAULT_CRITERIA)

    def test_corrupt_projects_store_reseeds_to_empty(self):
        self.ch.add_project_hint("shop", SHOP_HINT)
        self.assertEqual(self.ch.project_hint("/Users/x/Desktop/Shop"), SHOP_HINT)
        (Path(self.tmp) / "projects.json").write_text("[]")  # wrong shape
        # honest behavior: a corrupt store re-seeds to the empty default —
        # the previously-learned hint is gone, not silently recovered.
        self.assertEqual(self.ch.list_project_hints(), {})
        self.assertIsNone(self.ch.project_hint("/Users/x/Desktop/Shop"))

    # --- project hint management (add/remove/list) --------------------------

    def test_add_list_remove_project_hint(self):
        self.assertEqual(self.ch.list_project_hints(), {})
        self.assertTrue(self.ch.add_project_hint("shop", SHOP_HINT))
        self.assertEqual(self.ch.list_project_hints(), {"shop": SHOP_HINT})
        self.assertTrue(self.ch.remove_project_hint("shop"))
        self.assertEqual(self.ch.list_project_hints(), {})
        self.assertFalse(self.ch.remove_project_hint("shop"))  # already gone

    def test_add_project_hint_rejects_blank_input(self):
        self.assertFalse(self.ch.add_project_hint("", "a hint"))
        self.assertFalse(self.ch.add_project_hint("key", ""))
        self.assertFalse(self.ch.add_project_hint("  ", "  "))

    # --- project_hint matching -----------------------------------------------

    def test_project_hint_matches_added_dirs(self):
        self.ch.add_project_hint("shop", SHOP_HINT)
        self.assertEqual(
            self.ch.project_hint("/Users/x/Desktop/Shop Redesign"),
            SHOP_HINT,
        )

    def test_project_hint_separator_folding(self):
        # dir name with dashes matches a spaced multi-word key
        self.ch.add_project_hint("acme studio", "which client brief?")
        self.assertEqual(
            self.ch.project_hint("/Users/x/Desktop/Acme-Studio"),
            "which client brief?",
        )

    def test_project_hint_compact_camelcase_match(self):
        # compact CamelCase dir matches a multi-word key
        self.ch.add_project_hint("acme studio", "which client brief?")
        self.assertEqual(
            self.ch.project_hint("/Users/x/Desktop/AcmeStudio"),
            "which client brief?",
        )

    def test_project_hint_unknown_and_empty(self):
        self.assertIsNone(self.ch.project_hint("/Users/x/Desktop/some-unknown-dir"))
        self.assertIsNone(self.ch.project_hint(None))
        self.assertIsNone(self.ch.project_hint(""))

    def test_project_hint_word_boundaries_prevent_false_positives(self):
        # a single-word key like "shop" needs a real word boundary: it must
        # match its own path segment but never fire inside a compound word
        # like "Workshop" (no boundary between "work" and "shop").
        self.ch.add_project_hint("shop", SHOP_HINT)
        self.assertIsNone(self.ch.project_hint("/Users/x/Desktop/Workshop-notes"))
        self.assertEqual(
            self.ch.project_hint("/Users/x/Desktop/Shop"),
            SHOP_HINT,
        )

    def test_project_hint_absolute_path_key(self):
        self.ch.add_project_hint("/Users/x/Clients/acme", "which campaign?")
        self.assertEqual(
            self.ch.project_hint("/Users/x/Clients/acme/site"), "which campaign?"
        )
        self.assertIsNone(self.ch.project_hint("/Users/x/Clients/other"))

    def test_project_hint_longest_key_wins(self):
        self.ch.add_project_hint("acme", "generic acme question")
        self.ch.add_project_hint("acme studio", "specific acme-studio question")
        self.assertEqual(
            self.ch.project_hint("/Users/x/Desktop/Acme Studio Site"),
            "specific acme-studio question",
        )

    # --- learn_criteria ------------------------------------------------------

    def test_learn_adds_new_criteria_and_dedups_repeat(self):
        before = list(self.ch.known_criteria())
        text = "the header should have no horizontal scroll at 390px and console clean"
        self.ch.learn_criteria(text)
        after = self.ch.known_criteria()
        self.assertGreater(len(after), len(before))
        self.assertIn("the header should have no horizontal scroll at 390px", after)
        # repeat learns nothing new
        self.ch.learn_criteria(text)
        self.assertEqual(self.ch.known_criteria(), after)

    def test_learn_skips_secret_bearing_clauses(self):
        self.ch.learn_criteria(
            "the password should be hunter2 and tests should pass on CI"
        )
        crits = self.ch.known_criteria()
        joined = " | ".join(crits)
        self.assertNotIn("hunter2", joined)
        self.assertNotIn("password", joined)
        self.assertIn("tests should pass on ci", crits)
        # api-key shapes are skipped too
        self.ch.learn_criteria("deploy should use sk-ABC123SECRETXYZ for auth")
        self.assertNotIn("sk-abc123secretxyz", " | ".join(self.ch.known_criteria()))

    def test_learn_ignores_non_criteria_and_bad_input(self):
        before = list(self.ch.known_criteria())
        self.ch.learn_criteria("hello there my friend")  # no criteria signal
        self.ch.learn_criteria("")
        self.ch.learn_criteria(None)
        self.assertEqual(self.ch.known_criteria(), before)

    def test_cap_at_25_drops_oldest(self):
        for i in range(30):
            self.ch.learn_criteria(f"should render view {i} in under {i + 1} ms")
        crits = self.ch.known_criteria()
        self.assertEqual(len(crits), 25)
        # the oldest (seeded) entries were dropped...
        self.assertNotIn("no horizontal scroll at 390px", crits)
        # ...and the newest learned one survives
        self.assertIn("should render view 29 in under 30 ms", crits)

    # --- context_block -------------------------------------------------------

    def test_context_block_known_cwd_has_hint_and_criteria(self):
        self.ch.add_project_hint("shop", SHOP_HINT)
        block = self.ch.context_block("/Users/x/Desktop/Shop Redesign")
        self.assertIn("Project context: " + SHOP_HINT, block)
        self.assertIn("no horizontal scroll at 390px", block)

    def test_context_block_none_cwd_still_lists_criteria(self):
        # decided behavior: criteria are user-level, so they appear even with
        # no cwd — only the project line is omitted.
        block = self.ch.context_block(None)
        self.assertNotIn("Project context:", block)
        self.assertIn("acceptance criteria", block)
        self.assertIn("tests pass", block)

    def test_context_block_empty_when_nothing_to_add(self):
        (Path(self.tmp) / "criteria.json").write_text(json.dumps({"criteria": []}))
        self.assertEqual(self.ch.context_block(None), "")

    def test_context_block_caps_criteria_count(self):
        for i in range(30):
            self.ch.learn_criteria(f"should render view {i} in under {i + 1} ms")
        block = self.ch.context_block(None)
        listed = block.split(": ", 1)[1].split("; ")
        self.assertLessEqual(len(listed), 6)

    # --- scaffold_extra -------------------------------------------------------

    def test_scaffold_extra(self):
        self.ch.add_project_hint("shop", SHOP_HINT)
        self.assertEqual(
            self.ch.scaffold_extra("/Users/x/Desktop/Shop Redesign"),
            "+ Project: " + SHOP_HINT,
        )
        self.assertIsNone(self.ch.scaffold_extra("/Users/x/Desktop/unknown-place"))
        self.assertIsNone(self.ch.scaffold_extra(None))


if __name__ == "__main__":
    unittest.main()
