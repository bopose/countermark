"""Run: python3 -m unittest test_jpeg_segments -v

The APP11/JUMBF fragment-header layout was verified against a real,
c2pa-rs-generated test file (see test_c2pa_reader_real_files.py). These tests
use programmatically-built fixtures matching that verified layout.
"""

import unittest

from unwatermark.jpeg_segments import JpegError, find_c2pa_jumbf
from unwatermark.jumbf import content_type_uuid, parse_jumbf


def _app11(en, z, lbox, tbox, fragment):
    payload = b"JP" + en.to_bytes(2, "big") + z.to_bytes(4, "big") + lbox.to_bytes(4, "big") + tbox + fragment
    seg_len = len(payload) + 2
    assert seg_len <= 0xFFFF
    return b"\xff\xeb" + seg_len.to_bytes(2, "big") + payload


def _jpeg(*segments):
    return b"\xff\xd8" + b"".join(segments) + b"\xff\xd9"


def _box(box_type, payload):
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _jumd_box(uuid, label=None):
    toggles = 0x02 if label is not None else 0x00
    payload = uuid + bytes([toggles])
    if label is not None:
        payload += label.encode("utf-8") + b"\x00"
    return _box(b"jumd", payload)


def _jumb_box(jumd, *content_boxes):
    return _box(b"jumb", jumd + b"".join(content_boxes))


def _c2pa_top_box(label="c2pa"):
    inner = _jumb_box(_jumd_box(content_type_uuid(b"c2ma"), label="the-manifest"),
                       _box(b"json", b'{"claim_generator": "test-suite/1.0"}'))
    return _jumb_box(_jumd_box(content_type_uuid(b"c2pa"), label=label), inner)


def _fragment_box(full_box, chunk_size):
    """Split a full box (header + payload) into (lbox, tbox, [fragments])."""
    lbox = int.from_bytes(full_box[0:4], "big")
    tbox = full_box[4:8]
    content = full_box[8:]
    fragments = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)] or [b""]
    return lbox, tbox, fragments


class TestFindC2paJumbf(unittest.TestCase):

    def test_single_segment_no_fragmentation(self):
        full = _c2pa_top_box()
        lbox, tbox, fragments = _fragment_box(full, chunk_size=len(full))
        jpeg = _jpeg(_app11(1, 1, lbox, tbox, fragments[0]))
        result = find_c2pa_jumbf(jpeg)
        self.assertEqual(result, full)

    def test_two_fragment_reassembly_matches_original_exactly(self):
        full = _c2pa_top_box()
        lbox, tbox, fragments = _fragment_box(full, chunk_size=len(full) // 2 + 3)
        self.assertEqual(len(fragments), 2)
        jpeg = _jpeg(
            _app11(1, 1, lbox, tbox, fragments[0]),
            _app11(1, 2, lbox, tbox, fragments[1]),
        )
        result = find_c2pa_jumbf(jpeg)
        self.assertEqual(result, full)
        # And the reassembled bytes must be independently valid JUMBF.
        tree = parse_jumbf(result)
        self.assertEqual(tree["description"]["label"], "c2pa")

    def test_many_fragments_reassembled_in_order_regardless_of_file_order(self):
        full = _c2pa_top_box()
        lbox, tbox, fragments = _fragment_box(full, chunk_size=17)
        self.assertGreater(len(fragments), 3)
        # Write them out of order — a conformant reader must sort by sequence number.
        segments = [_app11(1, i + 1, lbox, tbox, frag) for i, frag in enumerate(fragments)]
        shuffled = [segments[-1]] + segments[:-1]
        jpeg = _jpeg(*shuffled)
        self.assertEqual(find_c2pa_jumbf(jpeg), full)

    def test_no_app11_segments_returns_none(self):
        payload = b"ABCD"  # an unrelated APP0 segment
        jpeg = _jpeg(b"\xff\xe0" + (len(payload) + 2).to_bytes(2, "big") + payload)
        self.assertIsNone(find_c2pa_jumbf(jpeg))

    def test_app11_without_jp_identifier_is_ignored(self):
        # Some other, non-JUMBF use of APP11 — must not be misread as JUMBF.
        payload = b"not jumbf data, some other APP11 use entirely"
        jpeg = _jpeg(b"\xff\xeb" + (len(payload) + 2).to_bytes(2, "big") + payload)
        self.assertIsNone(find_c2pa_jumbf(jpeg))

    def test_missing_fragment_raises(self):
        full = _c2pa_top_box()
        lbox, tbox, fragments = _fragment_box(full, chunk_size=len(full) // 3)
        self.assertGreaterEqual(len(fragments), 3)
        # Drop the middle fragment, leaving a gap in sequence numbers.
        jpeg = _jpeg(
            _app11(1, 1, lbox, tbox, fragments[0]),
            _app11(1, 3, lbox, tbox, fragments[2]),
        )
        with self.assertRaises(JpegError):
            find_c2pa_jumbf(jpeg)

    def test_inconsistent_lbox_across_fragments_raises(self):
        full = _c2pa_top_box()
        lbox, tbox, fragments = _fragment_box(full, chunk_size=len(full) // 2 + 3)
        jpeg = _jpeg(
            _app11(1, 1, lbox, tbox, fragments[0]),
            _app11(1, 2, lbox + 999, tbox, fragments[1]),  # corrupted length
        )
        with self.assertRaises(JpegError):
            find_c2pa_jumbf(jpeg)

    def test_bad_soi_raises(self):
        with self.assertRaises(JpegError):
            find_c2pa_jumbf(b"this is not a jpeg file")

    def test_unrelated_jumbf_box_instance_not_mistaken_for_c2pa(self):
        # A non-C2PA JUMBF box instance (different top-level UUID) present via
        # APP11 must not be returned as if it were the C2PA manifest.
        other = _jumb_box(_jumd_box(b"\x99" * 16, label="not-c2pa"))
        lbox, tbox, fragments = _fragment_box(other, chunk_size=len(other))
        jpeg = _jpeg(_app11(1, 1, lbox, tbox, fragments[0]))
        self.assertIsNone(find_c2pa_jumbf(jpeg))

    def test_finds_c2pa_instance_among_multiple_interleaved_instances(self):
        c2pa_full = _c2pa_top_box()
        c2pa_lbox, c2pa_tbox, c2pa_frags = _fragment_box(c2pa_full, chunk_size=len(c2pa_full))
        other = _jumb_box(_jumd_box(b"\x99" * 16, label="not-c2pa"))
        other_lbox, other_tbox, other_frags = _fragment_box(other, chunk_size=len(other))
        # Interleave: instance 2 (unrelated) segment, then instance 1 (c2pa) segment.
        jpeg = _jpeg(
            _app11(2, 1, other_lbox, other_tbox, other_frags[0]),
            _app11(1, 1, c2pa_lbox, c2pa_tbox, c2pa_frags[0]),
        )
        self.assertEqual(find_c2pa_jumbf(jpeg), c2pa_full)


if __name__ == "__main__":
    unittest.main(verbosity=2)
