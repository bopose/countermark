"""Run: python3 -m unittest test_provenance -v"""

import unittest

from unwatermark import diff_drafts, build_record


class TestDiff(unittest.TestCase):

    def test_identical_drafts_are_fully_unchanged(self):
        r = diff_drafts("the cat sat", "the cat sat")
        self.assertEqual(r["stats"]["percent_unchanged"], 100.0)
        self.assertEqual(r["stats"]["inserted"], 0)
        self.assertEqual(r["stats"]["deleted"], 0)
        self.assertEqual(r["stats"]["minor_fixes"], 0)
        self.assertEqual(r["stats"]["rewritten"], 0)

    def test_spelling_fix_counts_as_minor_not_rewrite(self):
        # A one-letter transposition fix must not read as a substantive rewrite.
        r = diff_drafts("i was very intrested", "i was very interested")
        self.assertEqual(r["stats"]["minor_fixes"], 1)
        self.assertEqual(r["stats"]["rewritten"], 0)
        # The whole thing counts as the author's wording.
        self.assertEqual(r["stats"]["percent_your_wording"], 100.0)
        # The op is tagged so the UI can style it as a minor fix.
        replace_ops = [o for o in r["ops"] if o["op"] == "replace"]
        self.assertEqual(replace_ops[0]["change"], "minor")

    def test_substantive_rephrase_counts_as_rewrite(self):
        r = diff_drafts("the results were unclear", "we could not draw firm conclusions")
        self.assertGreater(r["stats"]["rewritten"], 0)
        self.assertEqual(r["stats"]["minor_fixes"], 0)

    def test_your_wording_is_at_least_percent_unchanged(self):
        original = "me and my freind was intrested becuase it effect everyone"
        revised = "my friend and I were interested because it affects everyone"
        r = diff_drafts(original, revised)
        self.assertGreaterEqual(
            r["stats"]["percent_your_wording"], r["stats"]["percent_unchanged"])

    def test_insertion_counted(self):
        r = diff_drafts("the cat sat", "the big cat sat down")
        self.assertEqual(r["stats"]["inserted"], 2)
        self.assertEqual(r["stats"]["original_words"], 3)
        self.assertEqual(r["stats"]["revised_words"], 5)

    def test_empty_original_is_zero_percent(self):
        r = diff_drafts("", "brand new text here")
        self.assertEqual(r["stats"]["percent_unchanged"], 0.0)
        self.assertEqual(r["stats"]["inserted"], 4)


class TestRecord(unittest.TestCase):

    def test_record_without_draft_has_no_comparison(self):
        r = build_record("Some final text.", annotations=[
            {"label": "self", "text": "Some final text."},
        ])
        self.assertIsNone(r["diff"])
        self.assertIsNone(r["sidecar"]["draft_comparison"])
        self.assertIn("PROVENANCE DECLARATION", r["statement"])
        self.assertIn("Written by me", r["statement"])

    def test_record_with_draft_includes_comparison(self):
        r = build_record(
            "The cat sat quietly.",
            annotations=[{"label": "ai-grammar", "text": "The cat sat quietly."}],
            original_draft="the cat sat quiet",
            metadata={"author": "A. Student", "date": "2026-08-11"},
        )
        self.assertIsNotNone(r["diff"])
        self.assertIsNotNone(r["sidecar"]["draft_comparison"])
        self.assertIn("A. Student", r["statement"])
        self.assertIn("Comparison with my original draft", r["statement"])

    def test_custom_label_passthrough(self):
        r = build_record("x", annotations=[{"label": "Translated by me", "text": "x"}])
        self.assertIn("Translated by me", r["statement"])
        self.assertEqual(r["sidecar"]["sections"][0]["label_text"], "Translated by me")

    def test_disclaimer_present_in_both_outputs(self):
        r = build_record("x", annotations=[{"label": "self", "text": "x"}])
        self.assertIn("good-faith declaration", r["statement"])
        self.assertIn("good-faith declaration", r["sidecar"]["disclaimer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
