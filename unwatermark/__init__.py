"""Hidden-character inspector for text transparency.

This package finds and explains invisible / suspicious Unicode characters in
text. It deliberately does NOT try to detect statistical token watermarks
(SynthID-Text style): those cannot be confirmed or located by a third party
without the vendor's key, and any tool claiming otherwise is guessing from
writing style — the same discriminatory guess that unfairly flags
non-native-speaker and neurodiverse writers. We report only what is really
there and inspectable.
"""

from .scan import analyze
from .clean import clean
from .provenance import diff_drafts, build_record
from .docx_extract import extract_docx_text
from .odt_extract import extract_odt_text
from .documents import DOCUMENT_SUFFIXES, extract_document_text, sniff_document_format
from .c2pa_reader import (
    read_c2pa, read_c2pa_png, read_c2pa_jpeg,
    to_sidecar as c2pa_to_sidecar, to_summary_text as c2pa_to_summary_text,
)
from .premis import to_premis_xml

__all__ = [
    "analyze", "clean", "diff_drafts", "build_record",
    "extract_docx_text", "extract_odt_text", "extract_document_text",
    "sniff_document_format", "DOCUMENT_SUFFIXES",
    "read_c2pa", "read_c2pa_png", "read_c2pa_jpeg", "c2pa_to_sidecar", "c2pa_to_summary_text",
    "to_premis_xml",
]
