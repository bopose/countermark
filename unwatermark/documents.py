"""Detect and extract text from word-processor documents.

Both .docx and .odt are zip archives, so the format is detected from what's
actually inside rather than from the filename — a file called .txt that is
really a .docx should still read correctly, and a mislabelled extension
shouldn't produce a confusing error.
"""

import io
import zipfile

from .docx_extract import extract_docx_text
from .odt_extract import extract_odt_text

DOCUMENT_SUFFIXES = (".docx", ".odt")


def sniff_document_format(data):
    """Return "docx", "odt", or None if `data` isn't a supported document."""
    try:
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if "word/document.xml" in names:
        return "docx"
    if "content.xml" in names:
        return "odt"
    return None


def extract_document_text(data):
    """Extract text from a .docx or .odt, detected by content.

    Raises ValueError if the data isn't a document we can read.
    """
    fmt = sniff_document_format(data)
    if fmt == "docx":
        return extract_docx_text(data)
    if fmt == "odt":
        return extract_odt_text(data)
    raise ValueError(
        "Not a supported document (expected a .docx or .odt zip archive)."
    )
