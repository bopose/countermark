"""Minimal RIFF chunk reader for WebP — stdlib only.

Just enough of the RIFF container format to walk a WebP file's chunk list and
pull out the 'C2PA' chunk, which is where C2PA embeds its JUMBF-encoded
manifest data. RIFF is little-endian (unlike PNG/JPEG/BMFF): a 12-byte header
("RIFF", 4-byte overall size, form type "WEBP"), then chunks of (4-byte
FourCC, 4-byte size, payload, one pad byte if the payload length is odd).

The 'C2PA' FourCC and the chunk's placement at the top level of the container
were confirmed against the reference implementation (c2pa-rs riff_io.rs,
which defines C2PA_CHUNK_ID as exactly these four bytes and writes the chunk
as a direct child of the RIFF form) — the C2PA spec names WebP as a supported
format but the RIFF binding details live in the implementation. Verified
byte-for-byte against a real c2patool-signed WebP — see
test_c2pa_reader_real_files.py.
"""

_C2PA_FOURCC = b"C2PA"


class RiffError(ValueError):
    pass


def iter_webp_chunks(data):
    """Yield (fourcc: bytes, chunk_data: bytes) for each top-level chunk in a
    WebP file, in file order.

    Chunks are only read up to the RIFF header's declared size: trailing bytes
    beyond it are ignored (appended data is common and harmless), but a chunk
    that claims more data than the file holds is corruption and raises.
    """
    if data[:4] != b"RIFF":
        raise RiffError("Not a valid RIFF file (bad magic).")
    if len(data) < 12:
        raise RiffError("Truncated RIFF header.")
    if data[8:12] != b"WEBP":
        raise RiffError(f"RIFF file is not WebP (form type {data[8:12]!r}).")
    end = 8 + int.from_bytes(data[4:8], "little")
    if end > len(data):
        raise RiffError("RIFF header declares more data than the file contains.")
    offset = 12
    while offset < end:
        if offset + 8 > end:
            raise RiffError(f"Truncated RIFF chunk header at offset {offset}.")
        fourcc = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], "little")
        payload_end = offset + 8 + size
        if payload_end > end:
            raise RiffError(f"Truncated RIFF chunk {fourcc!r} at offset {offset}.")
        yield fourcc, data[offset + 8:payload_end]
        offset = payload_end + (size & 1)  # odd-sized payloads get a pad byte


def find_c2pa_chunk(data):
    """Return the 'C2PA' chunk's payload (raw JUMBF bytes), or None if absent."""
    for fourcc, chunk_data in iter_webp_chunks(data):
        if fourcc == _C2PA_FOURCC:
            return chunk_data
    return None
