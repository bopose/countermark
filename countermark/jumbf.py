"""A minimal JUMBF (ISO/IEC 19566-5) box parser — the container format C2PA
uses to embed its manifest data in files.

The ISO standard itself is paywalled. This module's box header rules and the
description-box ('jumd') toggle-byte layout were verified against real hex
test vectors from jumbf-rs (Adobe, Apache-2.0/MIT licensed,
https://github.com/scouten-adobe/jumbf-rs) — several of this module's own
tests reuse those vectors directly, including a full real C2PA manifest
structure, so this parser is checked against production code, not guesswork.
"""


class JumbfError(ValueError):
    pass


def read_box(data, offset=0):
    """Read one JUMBF box header at `offset`. Returns (box_type, payload, next_offset)."""
    if offset + 8 > len(data):
        raise JumbfError(f"truncated box header at offset {offset}")
    size = int.from_bytes(data[offset:offset + 4], "big")
    box_type = data[offset + 4:offset + 8]

    if size == 0:
        # Box extends to the end of the buffer — only valid for the outermost box.
        payload = data[offset + 8:]
        return box_type, payload, len(data)
    elif size == 1:
        # Extended (64-bit) length follows the type field.
        if offset + 16 > len(data):
            raise JumbfError(f"truncated extended box length at offset {offset}")
        xlsize = int.from_bytes(data[offset + 8:offset + 16], "big")
        if xlsize < 16:
            raise JumbfError(f"invalid extended box length {xlsize} at offset {offset}")
        payload_len = xlsize - 16
        payload_start = offset + 16
    elif 2 <= size <= 7:
        raise JumbfError(f"reserved/invalid box size {size} at offset {offset}")
    else:
        payload_len = size - 8
        payload_start = offset + 8

    payload_end = payload_start + payload_len
    if payload_end > len(data):
        raise JumbfError(
            f"box {box_type!r} at offset {offset} claims {payload_len} payload bytes "
            f"but only {len(data) - payload_start} are available"
        )
    return box_type, data[payload_start:payload_end], payload_end


def iter_boxes(data):
    """Yield (box_type, payload) for each top-level box packed into `data`."""
    offset = 0
    while offset < len(data):
        box_type, payload, offset = read_box(data, offset)
        yield box_type, payload


# Description-box (jumd) toggle bits, verified against jumbf-rs's toggles.rs.
_REQUESTABLE = 0x01
_HAS_LABEL = 0x02
_HAS_ID = 0x04
_HAS_HASH = 0x08
_HAS_PRIVATE_BOX = 0x10

# JUMBF content-type UUIDs follow a fixed template: a 4-character code
# followed by this constant suffix (verified against real UUIDs found in
# jumbf-rs's C2PA test vectors, e.g. 'c2pa' + this suffix == the C2PA
# Manifest Store UUID quoted in the C2PA spec).
_CONTENT_TYPE_UUID_SUFFIX = bytes.fromhex("00110010800000aa00389b71")


def content_type_uuid(fourcc):
    """The generic JUMBF content-type UUID for a 4-byte code, e.g. b'c2pa'."""
    if len(fourcc) != 4:
        raise ValueError("fourcc must be exactly 4 bytes")
    return fourcc + _CONTENT_TYPE_UUID_SUFFIX


def parse_description_box(payload):
    """Parse a 'jumd' box payload into its fields."""
    if len(payload) < 16:
        raise JumbfError("description box payload shorter than the 16-byte UUID")
    uuid = payload[:16]
    rest = payload[16:]
    if not rest:
        raise JumbfError("description box missing toggle byte")
    toggles, rest = rest[0], rest[1:]

    requestable = bool(toggles & _REQUESTABLE)

    label = None
    if toggles & _HAS_LABEL:
        nul = rest.find(b"\x00")
        if nul == -1:
            raise JumbfError("description box label is not null-terminated")
        label = rest[:nul].decode("utf-8")
        rest = rest[nul + 1:]

    box_id = None
    if toggles & _HAS_ID:
        if len(rest) < 4:
            raise JumbfError("description box truncated before 4-byte id")
        box_id, rest = int.from_bytes(rest[:4], "big"), rest[4:]

    box_hash = None
    if toggles & _HAS_HASH:
        if len(rest) < 32:
            raise JumbfError("description box truncated before 32-byte hash")
        box_hash, rest = rest[:32], rest[32:]

    # A private box's own internal structure is application-specific and rare
    # in practice; we keep its raw bytes rather than parsing further.
    private = rest if (toggles & _HAS_PRIVATE_BOX) else None

    return {
        "uuid": uuid,
        "label": label,
        "requestable": requestable,
        "id": box_id,
        "hash": box_hash,
        "private": private,
    }


def parse_super_box(payload):
    """Parse a 'jumb' superbox's payload into {description, children}.

    Each child is either {"type", "kind": "superbox", "description", "children"}
    (recursively parsed) or a leaf {"type", "kind": "content", "data": bytes}.
    """
    boxes = list(iter_boxes(payload))
    if not boxes:
        raise JumbfError("empty superbox payload")
    first_type, first_payload = boxes[0]
    if first_type != b"jumd":
        raise JumbfError(f"superbox's first child must be 'jumd', found {first_type!r}")
    description = parse_description_box(first_payload)

    children = []
    for box_type, box_payload in boxes[1:]:
        if box_type == b"jumb":
            sub = parse_super_box(box_payload)
            children.append({"type": box_type, "kind": "superbox", **sub})
        else:
            children.append({"type": box_type, "kind": "content", "data": box_payload})
    return {"description": description, "children": children}


def parse_jumbf(data):
    """Parse a top-level JUMBF byte stream (e.g. a PNG 'caBX' chunk's payload).

    Expects exactly one top-level 'jumb' box, per how C2PA embeds manifests.
    """
    boxes = list(iter_boxes(data))
    if len(boxes) != 1:
        raise JumbfError(f"expected exactly one top-level box, found {len(boxes)}")
    box_type, payload = boxes[0]
    if box_type != b"jumb":
        raise JumbfError(f"top-level box must be 'jumb', found {box_type!r}")
    return parse_super_box(payload)
