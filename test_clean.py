"""Run: python3 -m unittest test_clean -v"""

import unittest

from unwatermark import clean

ZWSP = "​"
NBSP = " "
LSEP = " "
BIDI_RLO = "‮"
TAG_A = "\U000e0041"


class TestClean(unittest.TestCase):

    def test_removes_zero_width_space(self):
        r = clean("hel" + ZWSP + "lo")
        self.assertEqual(r["cleaned"], "hello")
        self.assertEqual(r["summary"]["removed"], 1)

    def test_normalises_nbsp_to_plain_space(self):
        # The visible gap is preserved: words must not be merged.
        r = clean("a" + NBSP + "b")
        self.assertEqual(r["cleaned"], "a b")
        self.assertEqual(r["cleaned"][1], " ")  # a plain U+0020
        self.assertEqual(r["summary"]["replaced"], 1)

    def test_line_separator_becomes_newline(self):
        r = clean("a" + LSEP + "b")
        self.assertEqual(r["cleaned"], "a\nb")

    def test_clean_text_is_unchanged(self):
        text = "The quick brown fox.\nSecond line.\tTabbed."
        r = clean(text)
        self.assertEqual(r["cleaned"], text)
        self.assertEqual(r["summary"]["removed"], 0)
        self.assertEqual(r["summary"]["replaced"], 0)

    def test_removes_bidi_and_tag_characters(self):
        r = clean("file" + BIDI_RLO + "gnp.exe hi" + TAG_A)
        self.assertEqual(r["cleaned"], "filegnp.exe hi")

    def test_homoglyphs_untouched_by_default(self):
        r = clean("my paѕsword")
        self.assertIn("ѕ", r["cleaned"])  # the Cyrillic dze is still there
        self.assertEqual(r["summary"]["homoglyphs_normalized"], 0)

    def test_homoglyphs_fixed_when_opted_in(self):
        r = clean("my paѕsword", normalize_homoglyphs=True)
        self.assertEqual(r["cleaned"], "my password")
        self.assertEqual(r["summary"]["homoglyphs_normalized"], 1)
        self.assertEqual(r["homoglyph_changes"][0]["looks_like"], "password")

    def test_low_confidence_foreign_word_not_latinised_even_when_opted_in(self):
        # раура is a low-confidence 'lookalike-word', NOT a disguised word, so
        # opting in must still leave it alone — protecting real foreign text.
        r = clean("visit раура", normalize_homoglyphs=True)
        self.assertEqual(r["cleaned"], "visit раура")
        self.assertEqual(r["summary"]["homoglyphs_normalized"], 0)

    def test_legit_russian_survives_cleaning_and_normalisation(self):
        r = clean("привет мир", normalize_homoglyphs=True)
        self.assertEqual(r["cleaned"], "привет мир")


if __name__ == "__main__":
    unittest.main(verbosity=2)
