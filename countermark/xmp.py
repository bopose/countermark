"""Find XMP metadata in PNG/JPEG/WebP/AVIF, and the external-manifest pointer
inside it.

A C2PA manifest is usually embedded in the file, but it can also live
*outside* it: the asset carries only an XMP `dcterms:provenance` URL pointing
at a separate `.c2pa` manifest. A reader that only looks for embedded
manifests reports such a file as having no provenance at all, which is
misleading — there is provenance, it just isn't here.

This module spots that case so the reader can say so plainly. It does not
fetch the external manifest: that would mean a network request, which this
tool never makes.
"""

import re
import zlib

_XMP_START = b"<x:xmpmeta"
_XMP_END = b"</x:xmpmeta>"

# XMP is XML, but real-world packets are often malformed enough to break a
# strict parser, and we only need one attribute. A targeted regex is the more
# forgiving tool here, and a false negative just means we say nothing.
_PROVENANCE_RE = re.compile(
    rb"""dcterms:provenance\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)

_JPEG_XMP_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"


def _extract_packet(blob):
    """Return the XMP packet inside `blob`, or None."""
    start = blob.find(_XMP_START)
    if start == -1:
        return None
    end = blob.find(_XMP_END, start)
    if end == -1:
        return None
    return blob[start:end + len(_XMP_END)]


def _png_xmp_packets(data):
    from .png_chunks import iter_png_chunks  # local import avoids a cycle

    for ctype, payload in iter_png_chunks(data):
        if ctype not in (b"iTXt", b"tEXt", b"zTXt"):
            continue
        blob = payload
        if ctype == b"zTXt":
            # keyword \0 compression-method compressed-text
            nul = payload.find(b"\x00")
            if nul == -1 or len(payload) < nul + 2:
                continue
            try:
                blob = zlib.decompress(payload[nul + 2:])
            except zlib.error:
                continue
        packet = _extract_packet(blob)
        if packet:
            yield packet


def _jpeg_xmp_packets(data):
    from .jpeg_segments import iter_jpeg_segments  # local import avoids a cycle

    for marker, payload in iter_jpeg_segments(data):
        if marker != 0xE1:  # APP1
            continue
        if not payload.startswith(_JPEG_XMP_PREFIX):
            continue
        packet = _extract_packet(payload)
        if packet:
            yield packet


def _webp_xmp_packets(data):
    from .riff_chunks import iter_webp_chunks  # local import avoids a cycle

    for fourcc, payload in iter_webp_chunks(data):
        if fourcc != b"XMP ":  # the FourCC really is "XMP" plus a space
            continue
        packet = _extract_packet(payload)
        if packet:
            yield packet


def _bmff_xmp_packets(data):
    # Covers AVIF and HEIC alike: only XMP in a top-level uuid box (where
    # c2pa-rs writes it). HEIF-family files can instead store XMP as a
    # metadata item, which would need a meta/iinf/iloc parser this tool
    # doesn't have — those report nothing, which best-effort detection
    # permits (a miss just means we stay silent).
    from .bmff_boxes import iter_xmp_payloads  # local import avoids a cycle

    for payload in iter_xmp_payloads(data):
        packet = _extract_packet(payload)
        if packet:
            yield packet


def find_external_manifest_url(data, fmt):
    """Return an external C2PA manifest URL declared in the file's XMP, or None.

    `fmt` is "png", "jpeg", "webp", "avif", or "heic". Never raises: a
    malformed container just means no pointer is reported.
    """
    finder = {"png": _png_xmp_packets, "jpeg": _jpeg_xmp_packets,
              "webp": _webp_xmp_packets, "avif": _bmff_xmp_packets,
              "heic": _bmff_xmp_packets}.get(fmt)
    if finder is None:
        return None
    try:
        for packet in finder(data):
            match = _PROVENANCE_RE.search(packet)
            if match:
                return match.group(1).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - detection is best-effort by design
        return None
    return None
