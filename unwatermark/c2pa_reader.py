"""Read — never verify — a C2PA manifest embedded in a PNG or JPEG file.

This deliberately does NOT check the manifest's cryptographic signature: that
needs X.509 certificate parsing and asymmetric-key verification, which
Python's standard library cannot do, and adding a crypto dependency would
break this tool's zero-dependency, read-every-line trust model (see README).
So this module shows exactly what a manifest CLAIMS, structurally, and is
explicit everywhere that none of it has been verified: a tampered or entirely
fabricated manifest would read identically to a genuine one here. Treat the
output as "here is what the file says," never as "here is proof."

C2PA manifest content is CBOR since spec 2.x, but was JSON in earlier
versions (confirmed against a real manifest example) — both are decoded.
"""

import json

from .cbor_decode import CborError, loads as cbor_loads
from .jpeg_segments import JpegError, find_c2pa_jumbf as find_c2pa_jpeg_jumbf
from .jumbf import JumbfError, content_type_uuid, parse_jumbf
from .png_chunks import PngError, find_c2pa_chunk
from .xmp import find_external_manifest_url

UNVERIFIED_CAVEAT = (
    "This tool has NOT cryptographically verified this manifest's signature. "
    "It shows only what the manifest claims. A tampered or entirely fabricated "
    "manifest would look identical here. Signature verification needs X.509 "
    "and asymmetric-crypto support this tool intentionally does not include, "
    "to stay dependency-free."
)

_KNOWN_UUIDS = {
    content_type_uuid(b"c2pa"): "C2PA Manifest Store",
    content_type_uuid(b"c2ma"): "Manifest",
    content_type_uuid(b"c2um"): "Update Manifest",
    content_type_uuid(b"c2as"): "Assertion Store",
    content_type_uuid(b"c2cl"): "Claim",
    content_type_uuid(b"c2cs"): "Claim Signature",
    content_type_uuid(b"json"): "JSON content",
    content_type_uuid(b"cbor"): "CBOR content",
}


def _describe_uuid(uuid_bytes):
    return _KNOWN_UUIDS.get(uuid_bytes, "uuid:" + uuid_bytes.hex())


def _jsonify(value):
    """Make a decoded CBOR value JSON-safe (bytes -> hex, non-str keys -> str)."""
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex(), "byte_length": len(value)}
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else repr(k)): _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    return value


def _decode_content(box_type, data):
    """Best-effort decode of a content box's payload. Never raises — a decode
    failure is reported inline rather than aborting the whole read."""
    if box_type == b"json":
        try:
            return {"decoded_as": "json", "value": json.loads(data.decode("utf-8"))}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"decoded_as": None, "decode_error": str(exc), "byte_length": len(data)}
    if box_type == b"cbor":
        try:
            return {"decoded_as": "cbor", "value": _jsonify(cbor_loads(data))}
        except CborError as exc:
            return {"decoded_as": None, "decode_error": str(exc), "byte_length": len(data)}
    if box_type == b"uuid" and len(data) >= 16:
        # A generic escape-hatch box: a 16-byte UUID naming the content's
        # purpose, then raw bytes (e.g. the claim signature's COSE bytes).
        return {
            "decoded_as": "uuid+binary",
            "uuid": _describe_uuid(data[:16]),
            "byte_length": len(data) - 16,
        }
    if box_type == b"bfdb" and len(data) >= 2:
        # JUMBF's media-type box: a 1-byte toggle, then a null-terminated
        # media type string (e.g. "image/jpeg") — confirmed against a real
        # C2PA thumbnail assertion.
        nul = data[1:].find(b"\x00")
        if nul != -1:
            try:
                return {"decoded_as": "media-type", "value": data[1:1 + nul].decode("utf-8")}
            except UnicodeDecodeError:
                pass
        return {"decoded_as": None, "byte_length": len(data)}
    if box_type == b"bidb":
        # JUMBF's binary-data box — typically the actual bytes of an embedded
        # asset (e.g. a thumbnail image) named by a sibling 'bfdb' box.
        return {"decoded_as": None, "byte_length": len(data),
                "note": "binary asset data (e.g. an embedded thumbnail image)"}
    return {"decoded_as": None, "byte_length": len(data)}


def _walk(node):
    """Turn a jumbf.parse_super_box() tree into a display/sidecar-friendly tree."""
    desc = node["description"]
    children = []
    for child in node["children"]:
        if child["kind"] == "superbox":
            children.append({
                "type": child["type"].decode("latin-1"),
                "kind": "superbox",
                **_walk(child),
            })
        else:
            children.append({
                "type": child["type"].decode("latin-1"),
                "kind": "content",
                "content": _decode_content(child["type"], child["data"]),
            })
    return {
        "label": desc["label"],
        "uuid_meaning": _describe_uuid(desc["uuid"]),
        "children": children,
    }


def _result(found, error, manifest, external_manifest_url=None):
    return {
        "found": found,
        "error": error,
        "manifest": manifest,
        # A URL here means the file points at a manifest stored *outside*
        # itself. We never fetch it — that would be a network request.
        "external_manifest_url": external_manifest_url,
        "caveat": UNVERIFIED_CAVEAT,
    }


def _from_jumbf_bytes(jumbf_bytes, external_url=None):
    """Shared tail: given raw JUMBF bytes (or None), produce a result dict."""
    if jumbf_bytes is None:
        return _result(False, None, None, external_url)
    try:
        tree = parse_jumbf(jumbf_bytes)
    except JumbfError as exc:
        return _result(True, str(exc), None, external_url)
    return _result(True, None, _walk(tree), external_url)


def read_c2pa_png(data):
    """Read a PNG's embedded C2PA manifest, if any.

    Returns {found, error, manifest, external_manifest_url, caveat}. `error`
    is set only when a C2PA chunk exists but couldn't be parsed — a normal PNG
    with no C2PA data at all is `found: False, error: None`, not an error.
    """
    external = find_external_manifest_url(data, "png")
    try:
        chunk = find_c2pa_chunk(data)
    except PngError as exc:
        return _result(False, str(exc), None, external)
    return _from_jumbf_bytes(chunk, external)


def read_c2pa_jpeg(data):
    """Read a JPEG's embedded C2PA manifest, if any (reassembling APP11
    fragments as needed). Same return shape as read_c2pa_png()."""
    external = find_external_manifest_url(data, "jpeg")
    try:
        jumbf_bytes = find_c2pa_jpeg_jumbf(data)
    except JpegError as exc:
        return _result(False, str(exc), None, external)
    return _from_jumbf_bytes(jumbf_bytes, external)


_PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
_JPEG_SOI = b"\xff\xd8"


def read_c2pa(data):
    """Read a C2PA manifest from a PNG or JPEG file, detected by content.

    Returns the same shape as read_c2pa_png()/read_c2pa_jpeg(). For a file
    that is neither, returns found=False with an explanatory error.
    """
    if data[:8] == _PNG_SIGNATURE:
        return read_c2pa_png(data)
    if data[:2] == _JPEG_SOI:
        return read_c2pa_jpeg(data)
    return _result(False, "Not a PNG or JPEG file (unrecognised file header).", None)


def _render_content(content, indent):
    prefix = "  " * indent
    if content["decoded_as"] in ("json", "cbor"):
        text = json.dumps(content["value"], indent=2, ensure_ascii=False)
        return [prefix + line for line in text.splitlines()]
    if content["decoded_as"] == "media-type":
        return [f"{prefix}media type: {content['value']}"]
    if content["decoded_as"] == "uuid+binary":
        return [f"{prefix}[{content['uuid']}: {content['byte_length']} raw bytes, not decoded]"]
    if "decode_error" in content:
        return [f"{prefix}[could not decode: {content['decode_error']}]"]
    note = f" ({content['note']})" if content.get("note") else ""
    return [f"{prefix}[{content['byte_length']} raw bytes, not decoded{note}]"]


def _render_node(node, indent=0):
    prefix = "  " * indent
    lines = [f"{prefix}- {node['label'] or '(unlabelled)'}  [{node['uuid_meaning']}]"]
    for child in node["children"]:
        if child["kind"] == "superbox":
            lines.extend(_render_node(child, indent + 1))
        else:
            lines.append(f"{prefix}  · {child['type']} content:")
            lines.extend(_render_content(child["content"], indent + 2))
    return lines


def to_summary_text(result):
    """Human-readable rendering of a read_c2pa_png() result."""
    lines = ["C2PA MANIFEST READ", "=" * 19, "", "⚠ " + result["caveat"], ""]
    external = result.get("external_manifest_url")

    def with_external(text):
        if not external:
            return text
        return (
            text + "\n\n"
            "However, this file declares an EXTERNAL manifest — its provenance "
            "is stored in a separate file, not inside this one:\n"
            f"    {external}\n"
            "This tool does not fetch it (that would mean a network request). "
            "To inspect it, retrieve that file yourself."
        )

    if not result["found"]:
        lines.append(with_external(
            f"This file could not be fully read: {result['error']}"
            if result["error"] else "No C2PA manifest is embedded in this file."
        ))
        return "\n".join(lines)
    if result["error"]:
        lines.append(with_external(
            f"A C2PA chunk was found but could not be fully parsed: {result['error']}"))
        return "\n".join(lines)
    lines.append("Manifest structure, as claimed (not verified):")
    lines.append("")
    lines.extend(_render_node(result["manifest"]))
    return "\n".join(lines)


def to_sidecar(result, source_filename=None):
    """A durable, independent JSON record of what was found — meant to be kept
    alongside a file whose embedded C2PA data won't survive format migration,
    screenshots, or re-encoding."""
    return {
        "record_type": "unwatermark-c2pa-read",
        "version": 1,
        "source_filename": source_filename,
        "found": result["found"],
        "error": result["error"],
        "manifest": result["manifest"],
        "external_manifest_url": result.get("external_manifest_url"),
        "caveat": result["caveat"],
    }
