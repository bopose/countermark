"""Run: python3 -m unittest test_png_chunks -v"""

import struct
import unittest
import zlib

from unwatermark.png_chunks import PngError, find_c2pa_chunk, iter_png_chunks

_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _chunk(ctype, data):
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    return length + ctype + data + crc


def _minimal_png(*extra_chunks):
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1 RGB, no interlace
    return _SIGNATURE + _chunk(b"IHDR", ihdr_data) + b"".join(extra_chunks) + _chunk(b"IEND", b"")


class TestPngChunks(unittest.TestCase):

    def test_bad_signature_raises(self):
        with self.assertRaises(PngError):
            list(iter_png_chunks(b"not a png at all, just text"))

    def test_iter_chunks_yields_ihdr_and_iend_in_order(self):
        png = _minimal_png()
        chunks = list(iter_png_chunks(png))
        types = [t for t, _ in chunks]
        self.assertEqual(types, [b"IHDR", b"IEND"])

    def test_finds_cabx_chunk_payload_exactly(self):
        payload = b"pretend this is JUMBF-encoded C2PA manifest data"
        png = _minimal_png(_chunk(b"caBX", payload))
        self.assertEqual(find_c2pa_chunk(png), payload)

    def test_no_cabx_chunk_returns_none(self):
        png = _minimal_png()
        self.assertIsNone(find_c2pa_chunk(png))

    def test_cabx_among_other_chunks_still_found(self):
        payload = b"c2pa data here"
        png = _minimal_png(
            _chunk(b"tEXt", b"some,text,chunk"),
            _chunk(b"caBX", payload),
            _chunk(b"pHYs", b"\x00\x00\x00\x01\x00\x00\x00\x01\x01"),
        )
        self.assertEqual(find_c2pa_chunk(png), payload)

    def test_truncated_chunk_raises(self):
        png = _minimal_png()[:-6]  # cut off partway through IEND's CRC
        with self.assertRaises(PngError):
            list(iter_png_chunks(png))


if __name__ == "__main__":
    unittest.main(verbosity=2)
