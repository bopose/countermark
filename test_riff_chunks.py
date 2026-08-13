"""Run: python3 -m unittest test_riff_chunks -v

The 'C2PA' FourCC and top-level chunk placement were confirmed against the
reference implementation's source (c2pa-rs riff_io.rs) and the container
walk against a real WebP (see test_c2pa_reader_real_files.py). These tests
use programmatically-built fixtures matching that layout.
"""

import unittest

from countermark.riff_chunks import RiffError, find_c2pa_chunk, iter_webp_chunks


def _chunk(fourcc, payload):
    pad = b"\x00" if len(payload) % 2 else b""
    return fourcc + len(payload).to_bytes(4, "little") + payload + pad


def _webp(*chunks):
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


class TestIterWebpChunks(unittest.TestCase):

    def test_walks_chunks_in_order(self):
        webp = _webp(_chunk(b"VP8 ", b"fake image data"), _chunk(b"EXIF", b"fake exif"))
        chunks = list(iter_webp_chunks(webp))
        self.assertEqual(chunks, [(b"VP8 ", b"fake image data"), (b"EXIF", b"fake exif")])

    def test_odd_sized_chunk_pad_byte_is_skipped_not_misread(self):
        # "abc" is odd-length, so a pad byte follows it; the next chunk header
        # must be read after the pad, not one byte into it.
        webp = _webp(_chunk(b"VP8 ", b"abc"), _chunk(b"EXIF", b"even"))
        chunks = list(iter_webp_chunks(webp))
        self.assertEqual(chunks[0], (b"VP8 ", b"abc"))
        self.assertEqual(chunks[1], (b"EXIF", b"even"))

    def test_not_riff_raises(self):
        with self.assertRaises(RiffError):
            list(iter_webp_chunks(b"this is not a riff file"))

    def test_riff_but_not_webp_raises(self):
        wave = b"RIFF" + (8).to_bytes(4, "little") + b"WAVE" + b"fmt "
        with self.assertRaises(RiffError):
            list(iter_webp_chunks(wave))

    def test_header_declaring_more_than_file_holds_raises(self):
        webp = bytearray(_webp(_chunk(b"VP8 ", b"data")))
        webp[4:8] = (9999).to_bytes(4, "little")
        with self.assertRaises(RiffError):
            list(iter_webp_chunks(bytes(webp)))

    def test_trailing_bytes_beyond_declared_size_are_ignored(self):
        webp = _webp(_chunk(b"VP8 ", b"data")) + b"appended junk"
        self.assertEqual(list(iter_webp_chunks(webp)), [(b"VP8 ", b"data")])


class TestFindC2paChunk(unittest.TestCase):

    def test_returns_c2pa_payload(self):
        jumbf = b"pretend jumbf bytes!"
        webp = _webp(_chunk(b"VP8 ", b"image"), _chunk(b"C2PA", jumbf))
        self.assertEqual(find_c2pa_chunk(webp), jumbf)

    def test_found_among_many_metadata_chunks(self):
        webp = _webp(
            _chunk(b"VP8X", b"\x00" * 10),
            _chunk(b"ICCP", b"fake icc profile"),
            _chunk(b"EXIF", b"fake exif"),
            _chunk(b"XMP ", b"<x:xmpmeta></x:xmpmeta>"),
            _chunk(b"C2PA", b"the jumbf"),
        )
        self.assertEqual(find_c2pa_chunk(webp), b"the jumbf")

    def test_no_c2pa_chunk_returns_none(self):
        webp = _webp(_chunk(b"VP8 ", b"image"), _chunk(b"EXIF", b"meta"))
        self.assertIsNone(find_c2pa_chunk(webp))

    def test_forged_huge_chunk_size_raises_instead_of_overreading(self):
        # Mirrors c2pa-rs's test_read_cai_forged_c2pa_chunk_size_returns_error:
        # a C2PA chunk claiming ~4GB of data in a tiny file.
        c2pa = b"C2PA" + (0xFFFF_FFF0).to_bytes(4, "little") + b"tiny"
        body = b"WEBP" + c2pa
        webp = b"RIFF" + len(body).to_bytes(4, "little") + body
        with self.assertRaises(RiffError):
            find_c2pa_chunk(webp)

    def test_lowercase_fourcc_is_not_mistaken_for_c2pa(self):
        # FourCCs are case-sensitive; 'c2pa' would be a different chunk.
        webp = _webp(_chunk(b"c2pa", b"not the real thing"))
        self.assertIsNone(find_c2pa_chunk(webp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
