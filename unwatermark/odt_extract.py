"""Extract plain text from a .odt file — stdlib only (zipfile + xml.etree).

An .odt (OpenDocument Text, what LibreOffice writes) is a zip archive
containing content.xml. Like the .docx reader next door, this pulls out the
paragraphs and joins them with blank lines.

It differs from the .docx reader in one way that matters: OOXML puts every run
of text in its own <w:t> element, whereas OpenDocument uses XML *mixed
content* — text sits directly in a paragraph's `.text` and in the `.tail` of
any inline element. So this walks the tree collecting both, rather than
picking out one tag. Getting that wrong silently drops text that follows any
formatting change, which is exactly the sort of quiet data loss a
hidden-character scanner must not have.

As with .docx, we read but never write: rebuilding a valid ODF package is a
much larger job than reading one, for a need pasting already solves.
"""

import io
import xml.etree.ElementTree as ET
import zipfile

_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

_ODT_MIMETYPE = b"application/vnd.oasis.opendocument.text"

# Paragraph-level blocks. Headings (text:h) are paragraphs for our purposes.
_BLOCK_TAGS = {"p", "h"}


def _local(tag):
    """Strip the {namespace} prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _inline_text(element):
    """Recursively collect the text of one paragraph, honouring ODF's
    explicit whitespace elements."""
    parts = [element.text or ""]
    for child in element:
        tag = _local(child.tag)
        if tag == "s":
            # <text:s/> is one or more literal spaces; text:c gives the count.
            count = child.get(f"{{{_TEXT_NS}}}c")
            try:
                parts.append(" " * max(1, int(count)))
            except (TypeError, ValueError):
                parts.append(" ")
        elif tag == "tab":
            parts.append("\t")
        elif tag == "line-break":
            parts.append("\n")
        else:
            parts.append(_inline_text(child))
        # Text following an inline element lives in its tail, not its parent.
        parts.append(child.tail or "")
    return "".join(parts)


def _collect_blocks(element, out):
    """Walk the body, appending each paragraph's text in document order.

    Paragraphs are treated as leaves, so nested structures (lists, tables,
    frames, footnotes) are descended into exactly once and their paragraphs
    are not double-counted.
    """
    for child in element:
        if _local(child.tag) in _BLOCK_TAGS:
            out.append(_inline_text(child))
        else:
            _collect_blocks(child, out)


def extract_odt_text(data):
    """Return the plain text of a .odt file's paragraphs, joined by blank lines.

    Raises ValueError if `data` isn't a readable .odt.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid .odt file (not a zip archive).")

    # An .ods spreadsheet or .odp presentation also contains content.xml, and
    # would otherwise parse to an empty string — a silent wrong answer. The
    # mimetype entry is optional in the format, so only reject a definite
    # mismatch rather than requiring it.
    try:
        mimetype = zf.read("mimetype").strip()
    except KeyError:
        mimetype = None
    if mimetype and mimetype != _ODT_MIMETYPE:
        raise ValueError(
            f"Not a text document: this file declares {mimetype.decode('ascii', 'replace')}. "
            "Only .odt (OpenDocument Text) is supported."
        )

    try:
        xml_bytes = zf.read("content.xml")
    except KeyError:
        raise ValueError("Not a valid .odt file (missing content.xml).")

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise ValueError("Not a valid .odt file (malformed content.xml).")

    body = root.find(f"{{{_OFFICE_NS}}}body")
    if body is None:
        return ""
    text_body = body.find(f"{{{_OFFICE_NS}}}text")
    if text_body is None:
        raise ValueError(
            "Not a text document (no office:text element); "
            "only .odt (OpenDocument Text) is supported."
        )

    paragraphs = []
    _collect_blocks(text_body, paragraphs)
    return "\n\n".join(paragraphs)
