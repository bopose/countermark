"""A minimal CBOR (RFC 8949) decoder — stdlib only.

Python's standard library has no CBOR support, and C2PA manifests (2.x+) are
CBOR-encoded, so this is a small hand-written decoder covering the subset
C2PA actually uses: unsigned/negative integers, byte strings, text strings,
arrays, maps, tags (unwrapped and discarded), and simple values (bool/null/
floats).

Indefinite-length items are supported. The C2PA spec requires claims to use
"Core Deterministic Encoding" (definite lengths only), and this decoder
originally rejected indefinite lengths on that basis — but a real signed PNG
in the wild (a Sora image signed via Truepic's Lens CLI) encodes its
`c2pa.claim.v2` and `c2pa.actions.v2` with indefinite-length CBOR anyway. For
a tool whose job is to *show what a file actually contains*, refusing to read
a real file because it deviates from spec would be the wrong trade, so this
reads leniently and reports what's there.
"""

import struct


class CborError(ValueError):
    pass


class _Cursor:
    __slots__ = ("data", "pos")

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        end = self.pos + n
        if end > len(self.data):
            raise CborError("unexpected end of CBOR data")
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def read_byte(self):
        return self.read(1)[0]

    def peek(self):
        if self.pos >= len(self.data):
            raise CborError("unexpected end of CBOR data")
        return self.data[self.pos]


# Additional-info value 31 means "indefinite length": the item's contents run
# until a 0xFF "break" byte instead of being length-prefixed.
_INDEFINITE = 31
_BREAK_BYTE = 0xFF


class _Break:
    """Sentinel for the 0xFF break code that ends an indefinite-length item."""
    __slots__ = ()


_BREAK = _Break()


def _read_length(cursor, info):
    if info < 24:
        return info
    if info == 24:
        return cursor.read_byte()
    if info == 25:
        return int.from_bytes(cursor.read(2), "big")
    if info == 26:
        return int.from_bytes(cursor.read(4), "big")
    if info == 27:
        return int.from_bytes(cursor.read(8), "big")
    raise CborError(f"reserved additional-info value {info}")


def _decode_indefinite_string(cursor, major):
    """Concatenate the definite-length chunks of an indefinite-length string.

    RFC 8949 requires every chunk to share the outer item's major type.
    """
    chunks = []
    while True:
        if cursor.peek() == _BREAK_BYTE:
            cursor.read_byte()
            break
        initial = cursor.read_byte()
        if initial >> 5 != major:
            raise CborError(
                "indefinite-length string contains a chunk of a different major type"
            )
        info = initial & 0x1F
        if info == _INDEFINITE:
            raise CborError("indefinite-length string chunks may not themselves be indefinite")
        chunks.append(cursor.read(_read_length(cursor, info)))
    return b"".join(chunks)


def _decode_half_float(raw):
    (bits,) = struct.unpack(">H", raw)
    sign = (bits >> 15) & 1
    exponent = (bits >> 10) & 0x1F
    fraction = bits & 0x3FF
    if exponent == 0:
        value = fraction * 2.0 ** -24
    elif exponent == 31:
        value = float("inf") if fraction == 0 else float("nan")
    else:
        value = (1 + fraction / 1024.0) * 2.0 ** (exponent - 15)
    return -value if sign else value


def _decode_item(cursor):
    initial = cursor.read_byte()
    major = initial >> 5
    info = initial & 0x1F

    if major == 0:  # unsigned integer
        return _read_length(cursor, info)
    if major == 1:  # negative integer
        return -1 - _read_length(cursor, info)
    if major == 2:  # byte string
        if info == _INDEFINITE:
            return _decode_indefinite_string(cursor, major)
        return bytes(cursor.read(_read_length(cursor, info)))
    if major == 3:  # text string
        if info == _INDEFINITE:
            raw = _decode_indefinite_string(cursor, major)
        else:
            raw = cursor.read(_read_length(cursor, info))
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CborError(f"text string is not valid UTF-8: {exc}")
    if major == 4:  # array
        if info == _INDEFINITE:
            items = []
            while True:
                item = _decode_item(cursor)
                if item is _BREAK:
                    return items
                items.append(item)
        return [_decode_item(cursor) for _ in range(_read_length(cursor, info))]
    if major == 5:  # map
        result = {}
        if info == _INDEFINITE:
            while True:
                key = _decode_item(cursor)
                if key is _BREAK:
                    return result
                result[key] = _decode_item(cursor)
            return result
        for _ in range(_read_length(cursor, info)):
            key = _decode_item(cursor)
            result[key] = _decode_item(cursor)
        return result
    if major == 6:  # tag — read and discard the tag number, decode the tagged value
        _read_length(cursor, info)
        return _decode_item(cursor)
    if major == 7:  # simple values and floats
        if info == 20:
            return False
        if info == 21:
            return True
        if info == 22:
            return None
        if info == 23:
            return None  # "undefined" has no clean Python equivalent
        if info == 25:
            return _decode_half_float(cursor.read(2))
        if info == 26:
            return struct.unpack(">f", cursor.read(4))[0]
        if info == 27:
            return struct.unpack(">d", cursor.read(8))[0]
        if info == _INDEFINITE:
            # The "break" code; only meaningful inside an indefinite-length
            # item, where the enclosing loop consumes this sentinel.
            return _BREAK
        raise CborError(f"unsupported simple value (additional info {info})")
    raise CborError(f"unknown CBOR major type {major}")


def loads(data):
    """Decode a single CBOR-encoded item from `data`.

    Raises CborError if the data is malformed or if any trailing bytes remain
    after the item — the latter usually means the caller sliced the wrong
    byte range, and staying strict here surfaces that bug instead of hiding it.
    """
    cursor = _Cursor(data)
    value = _decode_item(cursor)
    if value is _BREAK:
        raise CborError("stray break code outside any indefinite-length item")
    if cursor.pos != len(data):
        raise CborError(f"{len(data) - cursor.pos} trailing byte(s) after the decoded CBOR item")
    return value
