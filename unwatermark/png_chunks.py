"""Minimal PNG chunk reader — stdlib only.

Just enough of the PNG spec to walk the chunk list and pull out the 'caBX'
chunk, which is where C2PA embeds its JUMBF-encoded manifest data.
"""

_PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


class PngError(ValueError):
    pass


def iter_png_chunks(data):
    """Yield (chunk_type: bytes, chunk_data: bytes) for each chunk in a PNG file."""
    if data[:8] != _PNG_SIGNATURE:
        raise PngError("Not a valid PNG file (bad signature).")
    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            raise PngError("Truncated PNG chunk header.")
        length = int.from_bytes(data[offset:offset + 4], "big")
        ctype = data[offset + 4:offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise PngError(f"Truncated PNG chunk {ctype!r}.")
        yield ctype, data[payload_start:payload_end]
        offset = payload_end + 4  # skip the 4-byte CRC


def find_c2pa_chunk(data):
    """Return the 'caBX' chunk's payload (raw JUMBF bytes), or None if absent."""
    for ctype, chunk_data in iter_png_chunks(data):
        if ctype == b"caBX":
            return chunk_data
    return None
