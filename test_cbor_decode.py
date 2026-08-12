"""Run: python3 -m unittest test_cbor_decode -v

Test vectors marked RFC are the canonical examples from RFC 8949 Appendix A.
"""

import struct
import unittest

from countermark.cbor_decode import CborError, loads


class TestCborDecode(unittest.TestCase):

    def test_small_unsigned_ints(self):  # RFC
        self.assertEqual(loads(b"\x00"), 0)
        self.assertEqual(loads(b"\x01"), 1)
        self.assertEqual(loads(b"\x0a"), 10)
        self.assertEqual(loads(b"\x17"), 23)

    def test_1byte_length_unsigned_int(self):  # RFC
        self.assertEqual(loads(b"\x18\x18"), 24)
        self.assertEqual(loads(b"\x18\x19"), 25)
        self.assertEqual(loads(b"\x18\x64"), 100)

    def test_2byte_length_unsigned_int(self):  # RFC
        self.assertEqual(loads(b"\x19\x03\xe8"), 1000)

    def test_4byte_length_unsigned_int(self):  # RFC
        self.assertEqual(loads(b"\x1a\x00\x0f\x42\x40"), 1000000)

    def test_negative_ints(self):  # RFC
        self.assertEqual(loads(b"\x20"), -1)
        self.assertEqual(loads(b"\x29"), -10)
        self.assertEqual(loads(b"\x38\x63"), -100)

    def test_byte_strings(self):  # RFC
        self.assertEqual(loads(b"\x40"), b"")
        self.assertEqual(loads(b"\x44\x01\x02\x03\x04"), b"\x01\x02\x03\x04")

    def test_text_strings(self):  # RFC
        self.assertEqual(loads(b"\x60"), "")
        self.assertEqual(loads(b"\x61a"), "a")
        self.assertEqual(loads(b"\x64IETF"), "IETF")

    def test_arrays(self):  # RFC
        self.assertEqual(loads(b"\x80"), [])
        self.assertEqual(loads(b"\x83\x01\x02\x03"), [1, 2, 3])

    def test_maps(self):  # RFC
        self.assertEqual(loads(b"\xa0"), {})
        self.assertEqual(loads(b"\xa2\x01\x02\x03\x04"), {1: 2, 3: 4})
        self.assertEqual(
            loads(b"\xa2\x61a\x01\x61b\x82\x02\x03"),
            {"a": 1, "b": [2, 3]},
        )

    def test_simple_values(self):  # RFC
        self.assertEqual(loads(b"\xf4"), False)
        self.assertEqual(loads(b"\xf5"), True)
        self.assertIsNone(loads(b"\xf6"))

    def test_half_float(self):  # RFC canonical example: 1.5 -> f9 3e00
        self.assertEqual(loads(b"\xf9\x3e\x00"), 1.5)

    def test_single_and_double_float_roundtrip(self):
        raw = bytes([0xFA]) + struct.pack(">f", 1.5)
        self.assertAlmostEqual(loads(raw), 1.5)
        raw = bytes([0xFB]) + struct.pack(">d", 3.14159265358979)
        self.assertAlmostEqual(loads(raw), 3.14159265358979)

    def test_tag_is_unwrapped_and_discarded(self):
        # Tag 0, wrapping unsigned int 1 — the tag number itself is dropped.
        self.assertEqual(loads(b"\xc0\x01"), 1)

    def test_nested_map_in_array(self):
        # [{"x": 1}]
        self.assertEqual(loads(b"\x81\xa1\x61x\x01"), [{"x": 1}])

    # --- Indefinite-length encoding (RFC 8949 §3.2) ---
    # The C2PA spec mandates definite-length ("Core Deterministic") encoding,
    # but a real signed PNG in the wild uses indefinite-length anyway, so the
    # decoder reads it rather than refusing a genuine file. See samples/.

    def test_indefinite_array(self):  # RFC: 9f 01 02 03 ff == [1,2,3]
        self.assertEqual(loads(b"\x9f\x01\x02\x03\xff"), [1, 2, 3])

    def test_indefinite_empty_array(self):
        self.assertEqual(loads(b"\x9f\xff"), [])

    def test_indefinite_map(self):  # {"a": 1, "b": [2, 3]}
        self.assertEqual(loads(b"\xbf\x61a\x01\x61b\x9f\x02\x03\xff\xff"),
                         {"a": 1, "b": [2, 3]})

    def test_indefinite_nested_in_definite(self):
        # [1, [_ 2, 3]] — an indefinite array inside a definite one.
        self.assertEqual(loads(b"\x82\x01\x9f\x02\x03\xff"), [1, [2, 3]])

    def test_indefinite_text_string_chunks_are_concatenated(self):
        # RFC: 7f 65 "strea" 64 "ming" ff == "streaming"
        self.assertEqual(loads(b"\x7f\x65strea\x64ming\xff"), "streaming")

    def test_indefinite_byte_string_chunks_are_concatenated(self):
        # RFC: 5f 42 0102 43 030405 ff == h'0102030405'
        self.assertEqual(loads(b"\x5f\x42\x01\x02\x43\x03\x04\x05\xff"),
                         b"\x01\x02\x03\x04\x05")

    def test_indefinite_string_chunk_of_wrong_major_type_raises(self):
        # A byte-string chunk inside an indefinite *text* string is invalid.
        with self.assertRaises(CborError):
            loads(b"\x7f\x42\x01\x02\xff")

    def test_stray_break_code_raises(self):
        with self.assertRaises(CborError):
            loads(b"\xff")

    def test_unterminated_indefinite_array_raises(self):
        with self.assertRaises(CborError):
            loads(b"\x9f\x01\x02")  # no break byte

    def test_truncated_data_raises(self):
        with self.assertRaises(CborError):
            loads(b"\x64IET")  # text string claims 4 bytes, only 3 given

    def test_trailing_bytes_raise(self):
        with self.assertRaises(CborError):
            loads(b"\x01\x02")  # a complete item (1) followed by junk


if __name__ == "__main__":
    unittest.main(verbosity=2)
