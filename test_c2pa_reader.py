"""Run: python3 -m unittest test_c2pa_reader -v

End-to-end tests: build a synthetic PNG whose 'caBX' chunk contains a
C2PA-shaped JUMBF tree (mirroring the real structure verified in test_jumbf.py
against jumbf-rs), and confirm read_c2pa_png / to_summary_text / to_sidecar
handle it — plus the failure and "nothing found" paths, which matter as much
as the happy path for a tool whose job is honesty about what it can't show.
"""

import struct
import unittest
import zlib

from countermark.c2pa_reader import (
    UNVERIFIED_CAVEAT, read_c2pa, read_c2pa_png, to_sidecar, to_summary_text,
)
from countermark.jumbf import content_type_uuid

_PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _png_chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def _minimal_png(*extra_chunks):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (_PNG_SIG + _png_chunk(b"IHDR", ihdr) + b"".join(extra_chunks)
            + _png_chunk(b"IEND", b""))


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


def _build_c2pa_jumbf(claim_box_type=b"json", claim_payload=b'{"recorder": "TestTool"}'):
    assertion = _jumb_box(
        _jumd_box(content_type_uuid(b"json"), label="c2pa.location.broad"),
        _box(b"json", b'{"location": "Test City"}'),
    )
    assertion_store = _jumb_box(
        _jumd_box(content_type_uuid(b"c2as"), label="c2pa.assertions"), assertion,
    )
    claim = _jumb_box(
        _jumd_box(content_type_uuid(b"c2cl"), label="c2pa.claim"),
        _box(claim_box_type, claim_payload),
    )
    signature = _jumb_box(
        _jumd_box(content_type_uuid(b"c2cs"), label="c2pa.signature"),
        _box(b"uuid", content_type_uuid(b"c2cs") + b"pretend-COSE-signature-bytes"),
    )
    manifest = _jumb_box(
        _jumd_box(content_type_uuid(b"c2ma"), label="cb.testsuite_1"),
        assertion_store, claim, signature,
    )
    return _jumb_box(_jumd_box(content_type_uuid(b"c2pa"), label="c2pa"), manifest)


class TestReadC2paPng(unittest.TestCase):

    def test_no_cabx_chunk_reports_not_found_without_error(self):
        result = read_c2pa_png(_minimal_png())
        self.assertFalse(result["found"])
        self.assertIsNone(result["error"])
        self.assertIsNone(result["manifest"])

    def test_caveat_always_present_even_when_nothing_found(self):
        result = read_c2pa_png(_minimal_png())
        self.assertEqual(result["caveat"], UNVERIFIED_CAVEAT)

    def test_not_a_png_reports_not_found_with_error(self):
        result = read_c2pa_png(b"this is not a png file")
        self.assertFalse(result["found"])
        self.assertIsNotNone(result["error"])

    def test_malformed_cabx_chunk_reports_found_with_error(self):
        png = _minimal_png(_png_chunk(b"caBX", b"not valid jumbf data"))
        result = read_c2pa_png(png)
        self.assertTrue(result["found"])
        self.assertIsNotNone(result["error"])
        self.assertIsNone(result["manifest"])

    def test_full_manifest_json_claim_extracted(self):
        jumbf = _build_c2pa_jumbf()
        png = _minimal_png(_png_chunk(b"caBX", jumbf))
        result = read_c2pa_png(png)

        self.assertTrue(result["found"])
        self.assertIsNone(result["error"])
        manifest = result["manifest"]
        self.assertEqual(manifest["label"], "c2pa")
        self.assertEqual(manifest["uuid_meaning"], "C2PA Manifest Store")

        manifest_node = manifest["children"][0]
        self.assertEqual(manifest_node["label"], "cb.testsuite_1")

        store, claim, sig = manifest_node["children"]
        self.assertEqual(store["label"], "c2pa.assertions")
        assertion = store["children"][0]
        self.assertEqual(assertion["label"], "c2pa.location.broad")
        self.assertEqual(
            assertion["children"][0]["content"]["value"], {"location": "Test City"})

        self.assertEqual(claim["label"], "c2pa.claim")
        self.assertEqual(
            claim["children"][0]["content"]["value"], {"recorder": "TestTool"})

        self.assertEqual(sig["label"], "c2pa.signature")
        sig_content = sig["children"][0]["content"]
        self.assertEqual(sig_content["decoded_as"], "uuid+binary")
        self.assertEqual(sig_content["uuid"], "Claim Signature")

    def test_cbor_claim_also_decoded(self):
        import countermark.cbor_decode as cbor
        # {"recorder": "TestTool"} hand-encoded as CBOR: map(1) { text(8)"recorder": text(8)"TestTool" }
        cbor_payload = (
            b"\xa1"                      # map, 1 pair
            b"\x68recorder"              # text(8) "recorder"
            b"\x68TestTool"              # text(8) "TestTool"
        )
        self.assertEqual(cbor.loads(cbor_payload), {"recorder": "TestTool"})  # sanity-check the fixture

        jumbf = _build_c2pa_jumbf(claim_box_type=b"cbor", claim_payload=cbor_payload)
        png = _minimal_png(_png_chunk(b"caBX", jumbf))
        result = read_c2pa_png(png)

        claim = result["manifest"]["children"][0]["children"][1]
        self.assertEqual(claim["label"], "c2pa.claim")
        content = claim["children"][0]["content"]
        self.assertEqual(content["decoded_as"], "cbor")
        self.assertEqual(content["value"], {"recorder": "TestTool"})

    def test_cbor_with_embedded_bytes_is_jsonified(self):
        # map(1) { text(4)"hash": bytes(4) 0x01 0x02 0x03 0x04 }
        cbor_payload = b"\xa1\x64hash\x44\x01\x02\x03\x04"
        jumbf = _build_c2pa_jumbf(claim_box_type=b"cbor", claim_payload=cbor_payload)
        png = _minimal_png(_png_chunk(b"caBX", jumbf))
        result = read_c2pa_png(png)

        claim = result["manifest"]["children"][0]["children"][1]
        value = claim["children"][0]["content"]["value"]
        # Raw bytes must never appear directly — result has to be JSON-serialisable.
        self.assertEqual(value["hash"], {"bytes_hex": "01020304", "byte_length": 4})

    def test_malformed_claim_content_reports_decode_error_not_crash(self):
        jumbf = _build_c2pa_jumbf(claim_box_type=b"json", claim_payload=b"{not valid json")
        png = _minimal_png(_png_chunk(b"caBX", jumbf))
        result = read_c2pa_png(png)  # must not raise

        claim = result["manifest"]["children"][0]["children"][1]
        content = claim["children"][0]["content"]
        self.assertIsNone(content["decoded_as"])
        self.assertIn("decode_error", content)


def _riff_chunk(fourcc, payload):
    pad = b"\x00" if len(payload) % 2 else b""
    return fourcc + len(payload).to_bytes(4, "little") + payload + pad


def _minimal_webp(*extra_chunks):
    body = b"WEBP" + _riff_chunk(b"VP8 ", b"fake image data") + b"".join(extra_chunks)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


_C2PA_BOX_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")


def _bmff_box(box_type, payload):
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _minimal_avif(*extra_boxes):
    ftyp = _bmff_box(b"ftyp", b"avif" + b"\x00\x00\x00\x00")
    return ftyp + _bmff_box(b"mdat", b"fake image data") + b"".join(extra_boxes)


def _c2pa_uuid_box(jumbf):
    payload = (_C2PA_BOX_UUID + b"\x00\x00\x00\x00" + b"manifest\x00"
               + (0).to_bytes(8, "big") + jumbf)
    return _bmff_box(b"uuid", payload)


class TestReadC2paWebpAndAvif(unittest.TestCase):
    """End-to-end through read_c2pa()'s content-based dispatch, with the same
    honesty contract as PNG: not-found is not an error, and a present-but-
    broken manifest is found-with-error."""

    def test_webp_manifest_found_and_parsed_via_dispatch(self):
        webp = _minimal_webp(_riff_chunk(b"C2PA", _build_c2pa_jumbf()))
        result = read_c2pa(webp)
        self.assertTrue(result["found"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["manifest"]["uuid_meaning"], "C2PA Manifest Store")
        claim = result["manifest"]["children"][0]["children"][1]
        self.assertEqual(claim["children"][0]["content"]["value"], {"recorder": "TestTool"})

    def test_avif_manifest_found_and_parsed_via_dispatch(self):
        avif = _minimal_avif(_c2pa_uuid_box(_build_c2pa_jumbf()))
        result = read_c2pa(avif)
        self.assertTrue(result["found"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["manifest"]["uuid_meaning"], "C2PA Manifest Store")

    def test_webp_without_c2pa_chunk_is_not_found_not_error(self):
        result = read_c2pa(_minimal_webp())
        self.assertFalse(result["found"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["caveat"], UNVERIFIED_CAVEAT)

    def test_avif_without_c2pa_box_is_not_found_not_error(self):
        result = read_c2pa(_minimal_avif())
        self.assertFalse(result["found"])
        self.assertIsNone(result["error"])

    def test_webp_with_malformed_jumbf_reports_found_with_error(self):
        webp = _minimal_webp(_riff_chunk(b"C2PA", b"not valid jumbf data"))
        result = read_c2pa(webp)
        self.assertTrue(result["found"])
        self.assertIsNotNone(result["error"])
        self.assertIsNone(result["manifest"])

    def test_avif_with_malformed_jumbf_reports_found_with_error(self):
        avif = _minimal_avif(_c2pa_uuid_box(b"not valid jumbf data"))
        result = read_c2pa(avif)
        self.assertTrue(result["found"])
        self.assertIsNotNone(result["error"])

    def test_corrupt_riff_container_reports_error_without_raising(self):
        truncated = bytearray(_minimal_webp())
        truncated[4:8] = (9999).to_bytes(4, "little")  # header overstates size
        result = read_c2pa(bytes(truncated))
        self.assertFalse(result["found"])
        self.assertIsNotNone(result["error"])

    def test_heic_is_refused_by_name_not_misread(self):
        heic = _bmff_box(b"ftyp", b"heic" + b"\x00\x00\x00\x00" + b"mif1")
        result = read_c2pa(heic)
        self.assertFalse(result["found"])
        self.assertIn(b"heic".decode(), result["error"])

    def test_unrecognised_header_names_all_supported_formats(self):
        result = read_c2pa(b"GIF89a...")
        self.assertFalse(result["found"])
        for fmt in ("PNG", "JPEG", "WebP", "AVIF"):
            self.assertIn(fmt, result["error"])


class TestRendering(unittest.TestCase):

    def test_summary_text_always_includes_caveat(self):
        result = read_c2pa_png(_minimal_png())
        text = to_summary_text(result)
        self.assertIn("NOT cryptographically verified", text)

    def test_summary_text_reports_not_found(self):
        result = read_c2pa_png(_minimal_png())
        self.assertIn("No C2PA manifest is embedded", to_summary_text(result))

    def test_summary_text_renders_found_manifest_labels(self):
        jumbf = _build_c2pa_jumbf()
        png = _minimal_png(_png_chunk(b"caBX", jumbf))
        text = to_summary_text(read_c2pa_png(png))
        self.assertIn("cb.testsuite_1", text)
        self.assertIn("c2pa.location.broad", text)
        self.assertIn("Test City", text)

    def test_sidecar_is_json_serialisable_and_carries_caveat(self):
        import json
        jumbf = _build_c2pa_jumbf()
        png = _minimal_png(_png_chunk(b"caBX", jumbf))
        sidecar = to_sidecar(read_c2pa_png(png), source_filename="photo.png")
        dumped = json.dumps(sidecar)  # must not raise
        self.assertIn("photo.png", dumped)
        self.assertEqual(sidecar["caveat"], UNVERIFIED_CAVEAT)
        self.assertEqual(sidecar["record_type"], "countermark-c2pa-read")


if __name__ == "__main__":
    unittest.main(verbosity=2)
