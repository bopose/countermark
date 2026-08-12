"""Run: python3 -m unittest test_jumbf -v

Box layout and description-box toggle semantics were verified against real
hex test vectors from jumbf-rs (Adobe, Apache-2.0/MIT,
https://github.com/scouten-adobe/jumbf-rs). Rather than hand-transcribe those
hex dumps (error-prone for 16-byte UUIDs), fixtures here are built
programmatically from the same verified rules — self-consistent by
construction — with a couple of short, independently hand-checkable hex
snippets for the box-header edge cases.
"""

import unittest

from countermark.jumbf import (
    JumbfError,
    content_type_uuid,
    parse_description_box,
    parse_jumbf,
    parse_super_box,
    read_box,
)

_TOGGLE_REQUESTABLE = 0x01
_TOGGLE_LABEL = 0x02
_TOGGLE_ID = 0x04
_TOGGLE_HASH = 0x08
_TOGGLE_PRIVATE = 0x10


def _box(box_type, payload):
    assert len(box_type) == 4
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _jumd_payload(uuid, label=None, id_=None, hash_=None, private_box=None, requestable=False):
    assert len(uuid) == 16
    toggles = (_TOGGLE_REQUESTABLE if requestable else 0)
    parts = [uuid]
    if label is not None:
        toggles |= _TOGGLE_LABEL
    if id_ is not None:
        toggles |= _TOGGLE_ID
    if hash_ is not None:
        toggles |= _TOGGLE_HASH
    if private_box is not None:
        toggles |= _TOGGLE_PRIVATE
    parts.append(bytes([toggles]))
    if label is not None:
        parts.append(label.encode("utf-8") + b"\x00")
    if id_ is not None:
        parts.append(id_.to_bytes(4, "big"))
    if hash_ is not None:
        assert len(hash_) == 32
        parts.append(hash_)
    if private_box is not None:
        parts.append(private_box)
    return b"".join(parts)


def _jumd_box(uuid, **kw):
    return _box(b"jumd", _jumd_payload(uuid, **kw))


def _jumb_box(jumd_box_bytes, *content_boxes):
    return _box(b"jumb", jumd_box_bytes + b"".join(content_boxes))


class TestContentTypeUuid(unittest.TestCase):

    def test_matches_real_c2pa_uuids(self):
        # Derived by hand from real hex found in jumbf-rs's C2PA test vectors:
        # 'c2pa' -> 6332706100110010800000aa00389b71, 'c2cs' -> 6332637300110010800000aa00389b71
        self.assertEqual(content_type_uuid(b"c2pa").hex(), "6332706100110010800000aa00389b71")
        self.assertEqual(content_type_uuid(b"c2ma").hex(), "63326d6100110010800000aa00389b71")
        self.assertEqual(content_type_uuid(b"c2as").hex(), "6332617300110010800000aa00389b71")
        self.assertEqual(content_type_uuid(b"c2cl").hex(), "6332636c00110010800000aa00389b71")
        self.assertEqual(content_type_uuid(b"c2cs").hex(), "6332637300110010800000aa00389b71")
        self.assertEqual(content_type_uuid(b"json").hex(), "6a736f6e00110010800000aa00389b71")


class TestReadBox(unittest.TestCase):

    def test_normal_box(self):
        box_type, payload, end = read_box(b"\x00\x00\x00\x10" b"abcd" b"hello123")
        self.assertEqual(box_type, b"abcd")
        self.assertEqual(payload, b"hello123")
        self.assertEqual(end, 16)

    def test_size_zero_reads_to_end_of_buffer(self):
        data = b"\x00\x00\x00\x00" b"abcd" b"rest of the buffer"
        box_type, payload, end = read_box(data)
        self.assertEqual(payload, b"rest of the buffer")
        self.assertEqual(end, len(data))

    def test_size_one_extended_length(self):
        payload = b"x" * 20
        xlsize = 16 + len(payload)  # extended size includes the 16-byte header
        data = b"\x00\x00\x00\x01" b"abcd" + xlsize.to_bytes(8, "big") + payload
        box_type, out_payload, end = read_box(data)
        self.assertEqual(out_payload, payload)
        self.assertEqual(end, len(data))

    def test_reserved_size_2_to_7_raises(self):
        for size in range(2, 8):
            with self.assertRaises(JumbfError):
                read_box(size.to_bytes(4, "big") + b"abcd")

    def test_truncated_header_raises(self):
        with self.assertRaises(JumbfError):
            read_box(b"\x00\x00")

    def test_payload_longer_than_available_raises(self):
        with self.assertRaises(JumbfError):
            read_box(b"\x00\x00\x00\x64" b"abcd" b"short")


class TestDescriptionBox(unittest.TestCase):

    def test_label_only(self):
        payload = _jumd_payload(b"\x00" * 16, label="test.descbox", requestable=True)
        result = parse_description_box(payload)
        self.assertEqual(result["label"], "test.descbox")
        self.assertTrue(result["requestable"])
        self.assertIsNone(result["id"])
        self.assertIsNone(result["hash"])
        self.assertIsNone(result["private"])

    def test_no_label(self):
        payload = _jumd_payload(b"\x00" * 16)
        result = parse_description_box(payload)
        self.assertIsNone(result["label"])
        self.assertFalse(result["requestable"])

    def test_with_id(self):
        payload = _jumd_payload(b"\x00" * 16, id_=4096)
        result = parse_description_box(payload)
        self.assertEqual(result["id"], 4096)
        self.assertIsNone(result["label"])

    def test_with_hash(self):
        h = b"This is a bogus hash............"[:32]
        payload = _jumd_payload(b"\x00" * 16, label="test.descbox", hash_=h)
        result = parse_description_box(payload)
        self.assertEqual(result["hash"], h)
        self.assertEqual(result["label"], "test.descbox")

    def test_label_id_and_hash_together_in_correct_order(self):
        h = b"h" * 32
        payload = _jumd_payload(b"\x11" * 16, label="multi", id_=99, hash_=h)
        result = parse_description_box(payload)
        self.assertEqual(result["uuid"], b"\x11" * 16)
        self.assertEqual(result["label"], "multi")
        self.assertEqual(result["id"], 99)
        self.assertEqual(result["hash"], h)

    def test_truncated_uuid_raises(self):
        with self.assertRaises(JumbfError):
            parse_description_box(b"\x00" * 10)

    def test_missing_toggle_byte_raises(self):
        with self.assertRaises(JumbfError):
            parse_description_box(b"\x00" * 16)

    def test_label_without_null_terminator_raises(self):
        payload = b"\x00" * 16 + bytes([_TOGGLE_LABEL]) + b"no null terminator here"
        with self.assertRaises(JumbfError):
            parse_description_box(payload)


class TestSuperBoxAndJumbf(unittest.TestCase):

    def test_superbox_first_child_must_be_jumd(self):
        not_jumd = _box(b"json", b"{}")
        with self.assertRaises(JumbfError):
            parse_super_box(not_jumd)

    def test_content_box_decoded_as_leaf(self):
        jumd = _jumd_box(b"\x00" * 16, label="root")
        content = _box(b"json", b'{"a":1}')
        tree = parse_super_box(jumd + content)
        self.assertEqual(tree["description"]["label"], "root")
        self.assertEqual(len(tree["children"]), 1)
        self.assertEqual(tree["children"][0]["kind"], "content")
        self.assertEqual(tree["children"][0]["type"], b"json")
        self.assertEqual(tree["children"][0]["data"], b'{"a":1}')

    def test_nested_superbox(self):
        inner_jumd = _jumd_box(b"\x22" * 16, label="child")
        inner_content = _box(b"json", b"1")
        inner = _jumb_box(inner_jumd, inner_content)

        outer_jumd = _jumd_box(b"\x11" * 16, label="root")
        tree = parse_super_box(outer_jumd + inner)

        self.assertEqual(tree["description"]["label"], "root")
        self.assertEqual(tree["children"][0]["kind"], "superbox")
        self.assertEqual(tree["children"][0]["description"]["label"], "child")
        self.assertEqual(tree["children"][0]["children"][0]["data"], b"1")

    def test_parse_jumbf_full_c2pa_shaped_structure(self):
        # Mirrors the real structure from jumbf-rs's C2PA test vector:
        # c2pa store -> manifest -> {assertion store -> one json assertion,
        # claim (json), signature (uuid+binary)}.
        assertion = _jumb_box(
            _jumd_box(content_type_uuid(b"json"), label="c2pa.location.broad"),
            _box(b"json", b'{"location": "Marga te City, NJ"}'),
        )
        assertion_store = _jumb_box(
            _jumd_box(content_type_uuid(b"c2as"), label="c2pa.assertions"),
            assertion,
        )
        claim = _jumb_box(
            _jumd_box(content_type_uuid(b"c2cl"), label="c2pa.claim"),
            _box(b"json", b'{"recorder": "Photoshop"}'),
        )
        signature = _jumb_box(
            _jumd_box(content_type_uuid(b"c2cs"), label="c2pa.signature"),
            _box(b"uuid", content_type_uuid(b"c2cs") + b"this would normally be COSE bytes"),
        )
        manifest = _jumb_box(
            _jumd_box(content_type_uuid(b"c2ma"), label="cb.adobe_1"),
            assertion_store, claim, signature,
        )
        top = _jumb_box(_jumd_box(content_type_uuid(b"c2pa"), label="c2pa"), manifest)

        tree = parse_jumbf(top)
        self.assertEqual(tree["description"]["label"], "c2pa")
        manifest_node = tree["children"][0]
        self.assertEqual(manifest_node["description"]["label"], "cb.adobe_1")
        self.assertEqual(len(manifest_node["children"]), 3)

        store_node, claim_node, sig_node = manifest_node["children"]
        self.assertEqual(store_node["description"]["label"], "c2pa.assertions")
        self.assertEqual(
            store_node["children"][0]["description"]["label"], "c2pa.location.broad")
        self.assertEqual(claim_node["description"]["label"], "c2pa.claim")
        self.assertEqual(sig_node["description"]["label"], "c2pa.signature")

    def test_top_level_must_be_single_jumb_box(self):
        with self.assertRaises(JumbfError):
            parse_jumbf(_box(b"json", b"{}"))

    def test_top_level_must_have_exactly_one_box(self):
        two_boxes = _box(b"json", b"1") + _box(b"json", b"2")
        with self.assertRaises(JumbfError):
            parse_jumbf(two_boxes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
