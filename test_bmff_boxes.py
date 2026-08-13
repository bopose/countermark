"""Run: python3 -m unittest test_bmff_boxes -v

The C2PA uuid-box payload layout (version/flags, null-terminated purpose,
merkle offset, then JUMBF) isn't in ISO 14496-12 — it comes from the C2PA
spec and was confirmed against the reference implementation's source
(c2pa-rs bmff_io.rs). These tests use programmatically-built fixtures
matching that layout.
"""

import unittest

from countermark.bmff_boxes import (
    BmffError, find_c2pa_manifest, is_avif, iter_boxes, iter_xmp_payloads,
)

_C2PA_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")
_XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")


def _box(box_type, payload):
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _large_box(box_type, payload):
    return (1).to_bytes(4, "big") + box_type + (16 + len(payload)).to_bytes(8, "big") + payload


def _ftyp(major=b"avif", *compatible):
    return _box(b"ftyp", major + b"\x00\x00\x00\x00" + b"".join(compatible))


def _c2pa_uuid_box(jumbf, purpose=b"manifest", merkle_offset=0):
    payload = _C2PA_UUID + b"\x00\x00\x00\x00" + purpose + b"\x00"
    if purpose == b"manifest":
        payload += merkle_offset.to_bytes(8, "big")
    return _box(b"uuid", payload + jumbf)


def _avif(*boxes):
    return _ftyp() + _box(b"mdat", b"fake image data") + b"".join(boxes)


class TestIterBoxes(unittest.TestCase):

    def test_walks_boxes_in_order(self):
        data = _ftyp() + _box(b"mdat", b"payload")
        types = [t for t, _, _ in iter_boxes(data)]
        self.assertEqual(types, [b"ftyp", b"mdat"])

    def test_largesize_box_read_correctly(self):
        data = _ftyp() + _large_box(b"mdat", b"large-format payload")
        boxes = list(iter_boxes(data))
        self.assertEqual(boxes[1], (b"mdat", None, b"large-format payload"))

    def test_size_zero_box_extends_to_end_of_file(self):
        data = _ftyp() + (0).to_bytes(4, "big") + b"mdat" + b"rest of the file"
        boxes = list(iter_boxes(data))
        self.assertEqual(boxes[1], (b"mdat", None, b"rest of the file"))

    def test_truncated_box_raises(self):
        data = _ftyp() + (100).to_bytes(4, "big") + b"mdat" + b"short"
        with self.assertRaises(BmffError):
            list(iter_boxes(data))

    def test_size_smaller_than_header_raises(self):
        data = (4).to_bytes(4, "big") + b"mdat"
        with self.assertRaises(BmffError):
            list(iter_boxes(data))

    def test_truncated_largesize_header_raises(self):
        data = (1).to_bytes(4, "big") + b"mdat" + b"\x00\x00"
        with self.assertRaises(BmffError):
            list(iter_boxes(data))

    def test_uuid_box_exposes_extended_type_separately(self):
        data = _box(b"uuid", _C2PA_UUID + b"rest")
        boxes = list(iter_boxes(data))
        self.assertEqual(boxes, [(b"uuid", _C2PA_UUID, b"rest")])

    def test_uuid_box_too_small_for_its_uuid_raises(self):
        data = _box(b"uuid", b"only-8b!")
        with self.assertRaises(BmffError):
            list(iter_boxes(data))


class TestIsAvif(unittest.TestCase):

    def test_major_brand_avif(self):
        self.assertTrue(is_avif(_ftyp(b"avif")))

    def test_avif_only_in_compatible_brands(self):
        # Real encoders often use major brand "mif1" with avif compatible.
        self.assertTrue(is_avif(_ftyp(b"mif1", b"miaf", b"avif")))

    def test_avis_image_sequence_accepted(self):
        self.assertTrue(is_avif(_ftyp(b"avis")))

    def test_heic_is_not_avif(self):
        self.assertFalse(is_avif(_ftyp(b"heic", b"mif1")))

    def test_garbage_is_not_avif_and_does_not_raise(self):
        self.assertFalse(is_avif(b"random bytes, nothing like bmff"))
        self.assertFalse(is_avif(b""))


class TestFindC2paManifest(unittest.TestCase):

    def test_manifest_extracted_after_merkle_offset(self):
        jumbf = b"pretend jumbf manifest store"
        avif = _avif(_c2pa_uuid_box(jumbf))
        self.assertEqual(find_c2pa_manifest(avif), jumbf)

    def test_nonzero_merkle_offset_still_yields_manifest_only(self):
        # The 8-byte offset field is metadata, never part of the JUMBF.
        jumbf = b"manifest bytes"
        avif = _avif(_c2pa_uuid_box(jumbf, merkle_offset=0x1234))
        self.assertEqual(find_c2pa_manifest(avif), jumbf)

    def test_no_c2pa_box_returns_none(self):
        self.assertIsNone(find_c2pa_manifest(_avif()))

    def test_unrelated_uuid_box_ignored(self):
        other = _box(b"uuid", b"\x99" * 16 + b"unrelated payload")
        self.assertIsNone(find_c2pa_manifest(_avif(other)))

    def test_merkle_purpose_box_is_skipped(self):
        avif = _avif(_c2pa_uuid_box(b"merkle cbor", purpose=b"merkle"))
        self.assertIsNone(find_c2pa_manifest(avif))

    def test_manifest_found_alongside_merkle_boxes(self):
        avif = _avif(
            _c2pa_uuid_box(b"merkle cbor", purpose=b"merkle"),
            _c2pa_uuid_box(b"the manifest"),
        )
        self.assertEqual(find_c2pa_manifest(avif), b"the manifest")

    def test_missing_purpose_null_terminator_raises(self):
        payload = _C2PA_UUID + b"\x00\x00\x00\x00" + b"manifest-without-nul"
        avif = _avif(_box(b"uuid", payload))
        with self.assertRaises(BmffError):
            find_c2pa_manifest(avif)

    def test_manifest_too_small_for_merkle_offset_raises(self):
        payload = _C2PA_UUID + b"\x00\x00\x00\x00" + b"manifest\x00" + b"\x00\x00"
        avif = _avif(_box(b"uuid", payload))
        with self.assertRaises(BmffError):
            find_c2pa_manifest(avif)

    def test_two_manifest_boxes_raise_rather_than_silently_picking_one(self):
        avif = _avif(_c2pa_uuid_box(b"first"), _c2pa_uuid_box(b"second"))
        with self.assertRaises(BmffError):
            find_c2pa_manifest(avif)


class TestIterXmpPayloads(unittest.TestCase):

    def test_xmp_uuid_box_payload_yielded(self):
        packet = b"<x:xmpmeta>fake</x:xmpmeta>"
        avif = _avif(_box(b"uuid", _XMP_UUID + packet))
        self.assertEqual(list(iter_xmp_payloads(avif)), [packet])

    def test_no_xmp_box_yields_nothing(self):
        self.assertEqual(list(iter_xmp_payloads(_avif())), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
