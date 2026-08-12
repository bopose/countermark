"""Extract plain text from a .docx file — stdlib only (zipfile + xml.etree).

A .docx is a zip archive containing OOXML. We read word/document.xml and pull
the text out of each paragraph, joining paragraphs with a blank line so the
result matches the "blank line separates paragraphs" convention the rest of
this tool already uses (see provenance.js's section splitter).

We deliberately don't try to write a .docx: reconstructing a valid OOXML
package from scratch is a much bigger, more fragile undertaking than reading
one, for a need a paste already solves.
"""

import io
import xml.etree.ElementTree as ET
import zipfile

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def extract_docx_text(data):
    """Return the plain text of a .docx file's paragraphs, joined by blank lines.

    Raises ValueError if `data` isn't a readable .docx.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid .docx file (not a zip archive).")

    try:
        xml_bytes = zf.read("word/document.xml")
    except KeyError:
        raise ValueError("Not a valid .docx file (missing word/document.xml).")

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise ValueError("Not a valid .docx file (malformed document.xml).")

    body = root.find(f"{{{_W_NS}}}body")
    if body is None:
        return ""

    paragraphs = []
    for p in body.findall(f"{{{_W_NS}}}p"):
        parts = []
        for node in p.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t":
                parts.append(node.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag in ("br", "cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n\n".join(paragraphs)
