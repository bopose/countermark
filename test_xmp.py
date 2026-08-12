"""Run: python3 -m unittest test_xmp -v

Detection of *external* C2PA manifests — files whose provenance lives in a
separate .c2pa file, referenced by an XMP dcterms:provenance URL. Before this
existed, such files were reported as having no provenance at all, which is
misleading: there is provenance, it just isn't embedded.
"""

import struct
import unittest
import zlib

from unwatermark.xmp import find_external_manifest_url

_PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])

XMP_TEMPLATE = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'dcterms:provenance="{url}"/>'
    "</rdf:RDF></x:xmpmeta><?xpacket end=\"r\"?>"
)


def _png_chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def _png_with_chunk(ctype, payload):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (_PNG_SIG + _png_chunk(b"IHDR", ihdr) + _png_chunk(ctype, payload)
            + _png_chunk(b"IEND", b""))


def _itxt_payload(xmp):
    # keyword \0 compression-flag compression-method language \0 translated \0 text
    return b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp.encode("utf-8")


def _jpeg_with_app1(payload):
    seg = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return b"\xff\xd8" + seg + b"\xff\xd9"


class TestPngExternalManifest(unittest.TestCase):

    def test_finds_url_in_itxt_xmp(self):
        xmp = XMP_TEMPLATE.format(url="https://example.org/manifests/photo.c2pa")
        png = _png_with_chunk(b"iTXt", _itxt_payload(xmp))
        self.assertEqual(
            find_external_manifest_url(png, "png"),
            "https://example.org/manifests/photo.c2pa",
        )

    def test_finds_url_in_compressed_ztxt_xmp(self):
        xmp = XMP_TEMPLATE.format(url="https://example.org/z.c2pa")
        payload = b"XML:com.adobe.xmp\x00\x00" + zlib.compress(xmp.encode("utf-8"))
        png = _png_with_chunk(b"zTXt", payload)
        self.assertEqual(find_external_manifest_url(png, "png"),
                         "https://example.org/z.c2pa")

    def test_png_without_xmp_returns_none(self):
        png = _png_with_chunk(b"tEXt", b"Comment\x00nothing to see here")
        self.assertIsNone(find_external_manifest_url(png, "png"))

    def test_xmp_without_provenance_returns_none(self):
        xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/">'
               '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               '<rdf:Description dc:title="a photo"/></rdf:RDF></x:xmpmeta>')
        png = _png_with_chunk(b"iTXt", _itxt_payload(xmp))
        self.assertIsNone(find_external_manifest_url(png, "png"))

    def test_malformed_container_returns_none_rather_than_raising(self):
        self.assertIsNone(find_external_manifest_url(b"not a png at all", "png"))


class TestJpegExternalManifest(unittest.TestCase):

    def test_finds_url_in_app1_xmp(self):
        xmp = XMP_TEMPLATE.format(url="https://example.org/j.c2pa")
        payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode("utf-8")
        self.assertEqual(find_external_manifest_url(_jpeg_with_app1(payload), "jpeg"),
                         "https://example.org/j.c2pa")

    def test_app1_exif_segment_is_ignored(self):
        payload = b"Exif\x00\x00" + b"\x00" * 40
        self.assertIsNone(find_external_manifest_url(_jpeg_with_app1(payload), "jpeg"))

    def test_unknown_format_returns_none(self):
        self.assertIsNone(find_external_manifest_url(b"whatever", "gif"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
