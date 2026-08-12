"""Reassemble C2PA JUMBF data from JPEG APP11 marker segments.

A JUMBF box embedded in a JPEG is often too large for one APP11 segment
(limited to ~64KB), so it's split into fragments across multiple APP11
segments and reassembled using a small per-segment header: a 2-byte "JP"
common identifier, a 2-byte box instance number, a 4-byte fragment sequence
number, a 4-byte total box length (LBox), and a 4-byte box type (TBox) —
16 bytes total, repeated identically at the start of every fragment of the
same box instance except for the sequence number, which increments.

This layout isn't in any free specification (ISO/IEC 19566-5 is paywalled),
so it was verified byte-for-byte against a real, c2pa-rs-generated test file
from the official C2PA public-testfiles repository — see test_jpeg_segments.py.
"""

from .jumbf import JumbfError, content_type_uuid, parse_jumbf

_SOI = b"\xff\xd8"
_APP11 = 0xEB


class JpegError(ValueError):
    pass


def iter_jpeg_segments(data):
    """Yield (marker_byte, payload_bytes) for each JPEG marker segment, in
    file order, stopping at Start of Scan (where compressed data begins)."""
    if data[:2] != _SOI:
        raise JpegError("Not a valid JPEG file (bad SOI marker).")
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            raise JpegError(f"Malformed JPEG: expected a marker at offset {i}.")
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:  # Start of Scan — compressed image data follows.
            break
        if i + 4 > len(data):
            raise JpegError("Truncated JPEG marker segment header.")
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        seg_end = i + 2 + seg_len
        if seg_end > len(data):
            raise JpegError(f"Truncated JPEG marker segment at offset {i}.")
        yield marker, data[i + 4:seg_end]
        i = seg_end


def iter_app11_segments(data):
    """Yield (box_instance, sequence, lbox, tbox, fragment_bytes) for every
    JUMBF-carrying APP11 marker segment in a JPEG file, in file order.

    APP11 segments not starting with the "JP" common identifier are some
    other, non-JUMBF use of APP11 and are silently skipped.
    """
    for marker, payload in iter_jpeg_segments(data):
        if marker != _APP11:
            continue
        if len(payload) >= 16 and payload[:2] == b"JP":
            en = int.from_bytes(payload[2:4], "big")
            z = int.from_bytes(payload[4:8], "big")
            lbox = int.from_bytes(payload[8:12], "big")
            tbox = payload[12:16]
            yield en, z, lbox, tbox, payload[16:]


def find_c2pa_jumbf(data):
    """Reassemble and return the C2PA Manifest Store's JUMBF bytes (including
    its own 8-byte box header) from a JPEG's APP11 segments, or None if no
    C2PA manifest is present.

    A JPEG may carry other, unrelated JUMBF box instances via APP11; only the
    one whose top-level UUID matches the C2PA Manifest Store is returned.
    Raises JpegError if JUMBF data is present but corrupt (missing fragments,
    length mismatch, inconsistent per-fragment headers).
    """
    instances = {}
    for en, z, lbox, tbox, fragment in iter_app11_segments(data):
        inst = instances.setdefault(en, {"lbox": lbox, "tbox": tbox, "fragments": []})
        if inst["lbox"] != lbox or inst["tbox"] != tbox:
            raise JpegError(f"Inconsistent LBox/TBox across fragments of box instance {en}.")
        inst["fragments"].append((z, fragment))

    target_uuid = content_type_uuid(b"c2pa")
    for en, inst in instances.items():
        fragments = sorted(inst["fragments"], key=lambda x: x[0])
        seqs = [z for z, _ in fragments]
        if seqs != list(range(1, len(seqs) + 1)):
            raise JpegError(
                f"Box instance {en}: missing or out-of-order fragments (sequence numbers {seqs})."
            )
        content = b"".join(f for _, f in fragments)
        expected_len = inst["lbox"] - 8
        if len(content) != expected_len:
            raise JpegError(
                f"Box instance {en}: reassembled {len(content)} bytes, "
                f"expected {expected_len} bytes per its declared length."
            )
        if inst["tbox"] != b"jumb":
            continue
        reassembled = inst["lbox"].to_bytes(4, "big") + inst["tbox"] + content
        try:
            tree = parse_jumbf(reassembled)
        except JumbfError:
            continue
        if tree["description"]["uuid"] == target_uuid:
            return reassembled

    return None
