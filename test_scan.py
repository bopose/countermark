"""Run: python3 -m unittest test_scan -v"""

import unittest

from unwatermark import analyze


class TestScan(unittest.TestCase):

    def test_clean_text_has_no_flags(self):
        r = analyze("The quick brown fox jumps over the lazy dog.\nSecond line.\tTabbed.")
        self.assertEqual(r["summary"]["flag_count"], 0)
        self.assertEqual(r["summary"]["homoglyph_count"], 0)

    def test_zero_width_space_is_flagged(self):
        r = analyze("hel​lo")
        self.assertEqual(r["summary"]["flag_count"], 1)
        f = r["findings"][0]
        self.assertEqual(f["codepoint"], "U+200B")
        self.assertEqual(f["category"], "zero-width")
        self.assertEqual(f["offset"], 3)

    def test_segments_reproduce_text_in_order(self):
        text = "a​b c"
        r = analyze(text)
        rebuilt = "".join(s["text"] for s in r["segments"])
        self.assertEqual(rebuilt, text)

    def test_nbsp_is_low_severity_not_high(self):
        r = analyze("a b")
        self.assertEqual(r["findings"][0]["severity"], "low")

    def test_bidi_override_flagged_high(self):
        r = analyze("file‮gnp.exe")
        self.assertEqual(r["findings"][0]["category"], "bidi")
        self.assertEqual(r["findings"][0]["severity"], "high")

    def test_tag_character_flagged(self):
        r = analyze("hi\U000e0041")  # tag latin capital A
        self.assertEqual(r["findings"][0]["category"], "invisible-tag")

    def test_plain_whitespace_never_flagged(self):
        r = analyze(" \t\n\r ")
        self.assertEqual(r["summary"]["flag_count"], 0)

    def _one(self, text):
        r = analyze(text)
        self.assertEqual(r["summary"]["homoglyph_count"], 1, r["homoglyphs"])
        return r["homoglyphs"][0]

    def test_disguised_word_is_high_confidence_with_skeleton(self):
        # "paѕsword" — Latin word with a Cyrillic dze standing in for 's'.
        h = self._one("my paѕsword is safe")
        self.assertEqual(h["kind"], "disguised")
        self.assertEqual(h["confidence"], "high")
        self.assertEqual(h["looks_like"], "password")
        self.assertEqual(h["swaps"][0]["maps_to"], "s")
        self.assertIn("CYRILLIC", h["scripts"])
        self.assertIn("LATIN", h["scripts"])

    def test_fully_disguised_all_cyrillic_word_flagged_low(self):
        # Every letter is a Cyrillic look-alike: раураӏ has no non-look-alike
        # letter, so it skeletonises cleanly. Length >= 4, all non-Latin.
        h = self._one("visit раура now")  # р а у р а -> "paypa"
        self.assertEqual(h["kind"], "lookalike-word")
        self.assertEqual(h["confidence"], "low")
        self.assertEqual(h["looks_like"], "paypa")

    def test_short_foreign_word_not_flagged(self):
        # Greek "και" (and) is 3 letters and all-non-Latin: below the length
        # floor, so it must not be flagged.
        r = analyze("και")
        self.assertEqual(r["summary"]["homoglyph_count"], 0, r["homoglyphs"])

    def test_legitimate_russian_word_not_flagged(self):
        # привет contains и/в/т, which have no Latin look-alike, so it cannot
        # skeletonise to Latin and must stay quiet.
        for word in ("привет", "Москва", "хлеб", "сова", "россия"):
            r = analyze(word)
            self.assertEqual(r["summary"]["homoglyph_count"], 0,
                             (word, r["homoglyphs"]))

    def test_german_umlauts_and_esszett_not_flagged(self):
        r = analyze("Straße über schön")
        self.assertEqual(r["summary"]["homoglyph_count"], 0, r["homoglyphs"])

    def test_mixed_script_without_clean_skeleton_is_low(self):
        # 'д' (Cyrillic de) is not a look-alike for any Latin letter, so this
        # blends scripts but does not read as Latin -> mixed-script tier.
        h = self._one("worдd")
        self.assertEqual(h["kind"], "mixed-script")
        self.assertNotIn("looks_like", h)

    def test_soft_hyphen_flagged(self):
        r = analyze("ex­am­ple")
        self.assertEqual(r["summary"]["flag_count"], 2)
        self.assertEqual(r["findings"][0]["category"], "soft-hyphen")


if __name__ == "__main__":
    unittest.main(verbosity=2)
