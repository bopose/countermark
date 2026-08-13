"""Minimal ISO BMFF (ISO/IEC 14496-12) box reader for AVIF — stdlib only.

Just enough of the Base Media File Format to walk a file's top-level boxes
and pull C2PA data out. A box is (4-byte big-endian size, 4-byte type); a
size of 1 means an 8-byte "largesize" follows the type; a size of 0 means the
box runs to the end of the file; a 'uuid' box carries a further 16-byte
extended type after the header.

C2PA embeds its manifest in a top-level 'uuid' box whose extended type is
d8fec3d6-1b0e-483c-9297-5828877ec481. The box payload is: a 4-byte FullBox
version/flags field, a null-terminated ASCII "purpose" string, and — when the
purpose is "manifest" — an 8-byte big-endian offset to the first auxiliary
merkle box, then the JUMBF manifest-store bytes. ISO 14496-12 defines the box
framing but not this C2PA payload; the payload layout was confirmed against
the reference implementation (c2pa-rs bmff_io.rs, write_c2pa_box /
get_uuid_box_purpose / c2pa_boxes_from_tree_and_map) and verified
byte-for-byte against a real c2patool-signed AVIF — see
test_c2pa_reader_real_files.py.
"""

_C2PA_BOX_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")
_XMP_BOX_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")


class BmffError(ValueError):
    pass


def iter_boxes(data):
    """Yield (box_type: bytes, extended_type: bytes|None, payload: bytes) for
    each top-level box in an ISO BMFF file, in file order.

    extended_type is the 16-byte UUID of a 'uuid' box (excluded from its
    payload), None for every other box type.
    """
    offset = 0
    n = len(data)
    while offset < n:
        if offset + 8 > n:
            raise BmffError(f"Truncated BMFF box header at offset {offset}.")
        size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = data[offset + 4:offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > n:
                raise BmffError(f"Truncated largesize box header at offset {offset}.")
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        elif size == 0:  # box extends to the end of the file
            size = n - offset
        if size < header:
            raise BmffError(
                f"BMFF box {box_type!r} at offset {offset} declares a size "
                f"({size}) smaller than its own header.")
        end = offset + size
        if end > n:
            raise BmffError(f"Truncated BMFF box {box_type!r} at offset {offset}.")
        payload = data[offset + header:end]
        extended_type = None
        if box_type == b"uuid":
            if len(payload) < 16:
                raise BmffError(
                    f"BMFF uuid box at offset {offset} is too small to hold "
                    "its 16-byte extended type.")
            extended_type = payload[:16]
            payload = payload[16:]
        yield box_type, extended_type, payload
        offset = end


def is_avif(data):
    """True if the file starts with an 'ftyp' box declaring an AVIF brand
    ('avif' still image or 'avis' image sequence), as major or compatible
    brand. Never raises — anything malformed is simply not AVIF."""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    size = int.from_bytes(data[0:4], "big")
    if size < 16 or size > len(data) or size % 4 != 0:
        return False
    # Payload: 4-byte major brand, 4-byte minor version, then compatible brands.
    brands = [data[8:12]] + [data[i:i + 4] for i in range(16, size, 4)]
    return b"avif" in brands or b"avis" in brands


def find_c2pa_manifest(data):
    """Return the JUMBF manifest-store bytes from a file's C2PA 'uuid' box
    (purpose "manifest"), or None if no such box is present.

    Boxes with other C2PA purposes ("merkle" hash trees, and the "original"/
    "update" stores used by BMFF update manifests) are skipped: this reader
    shows the current manifest store only. Raises BmffError if the container
    or the C2PA box itself is corrupt, including more than one manifest box —
    the reference implementation rejects that too, and silently picking one
    would misrepresent an ambiguous file.
    """
    manifest = None
    for box_type, extended_type, payload in iter_boxes(data):
        if box_type != b"uuid" or extended_type != _C2PA_BOX_UUID:
            continue
        if len(payload) < 4:
            raise BmffError("C2PA uuid box is too small for its version/flags field.")
        nul = payload.find(b"\x00", 4)
        if nul == -1:
            raise BmffError("C2PA uuid box purpose string is missing its null terminator.")
        if payload[4:nul] != b"manifest":
            continue
        rest = payload[nul + 1:]
        if len(rest) < 8:
            raise BmffError("C2PA manifest box is too small for its merkle-offset field.")
        if manifest is not None:
            raise BmffError("File contains more than one C2PA manifest box.")
        manifest = rest[8:]  # skip the merkle offset; the rest is JUMBF
    return manifest


def iter_xmp_payloads(data):
    """Yield the payload of each top-level XMP 'uuid' box (the raw XMP packet
    bytes). This is where c2pa-rs writes XMP in BMFF files. AVIF files can
    instead store XMP as a HEIF metadata item (via meta/iinf/iloc), which this
    top-level walk does not reach — callers treat absence as best-effort."""
    for box_type, extended_type, payload in iter_boxes(data):
        if box_type == b"uuid" and extended_type == _XMP_BOX_UUID:
            yield payload
