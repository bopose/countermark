"""Run: python3 -m unittest test_odt_extract -v"""

import io
import os
import unittest
import zipfile

from countermark import analyze, extract_odt_text
from countermark.documents import extract_document_text, sniff_document_format

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ODT_MIME = b"application/vnd.oasis.opendocument.text"


def _odt(body_xml, mimetype=ODT_MIME, content_name="content.xml"):
    """Build a minimal .odt in memory."""
    xml = (
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}">'
        f"<office:body><office:text>{body_xml}</office:text></office:body>"
        "</office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if mimetype is not None:
            zf.writestr("mimetype", mimetype)
        zf.writestr(content_name, xml)
    return buf.getvalue()


def _paragraphs(*texts):
    return "".join(f"<text:p>{t}</text:p>" for t in texts)


class TestOdtExtract(unittest.TestCase):

    def test_paragraphs_joined_by_blank_line(self):
        data = _odt(_paragraphs("First paragraph.", "Second paragraph."))
        self.assertEqual(extract_odt_text(data),
                         "First paragraph.\n\nSecond paragraph.")

    def test_mixed_content_keeps_text_after_inline_formatting(self):
        """The failure mode this module exists to avoid.

        OpenDocument puts text directly in the paragraph and in the *tail* of
        inline elements. A reader that only collects element .text would
        silently drop everything after the first formatting change.
        """
        data = _odt("<text:p>Hello <text:span>brave</text:span> new world</text:p>")
        self.assertEqual(extract_odt_text(data), "Hello brave new world")

    def test_nested_spans_are_flattened(self):
        data = _odt(
            "<text:p>a<text:span>b<text:span>c</text:span>d</text:span>e</text:p>"
        )
        self.assertEqual(extract_odt_text(data), "abcde")

    def test_headings_count_as_paragraphs(self):
        data = _odt("<text:h>A Heading</text:h>" + _paragraphs("Body text."))
        self.assertEqual(extract_odt_text(data), "A Heading\n\nBody text.")

    def test_explicit_space_element(self):
        # <text:s/> is one space; text:c gives a repeat count.
        data = _odt("<text:p>a<text:s/>b<text:s text:c=\"3\"/>c</text:p>")
        self.assertEqual(extract_odt_text(data), "a b   c")

    def test_tab_and_line_break(self):
        data = _odt("<text:p>a<text:tab/>b<text:line-break/>c</text:p>")
        self.assertEqual(extract_odt_text(data), "a\tb\nc")

    def test_list_paragraphs_are_collected_once_each(self):
        data = _odt(
            "<text:list><text:list-item><text:p>one</text:p></text:list-item>"
            "<text:list-item><text:p>two</text:p></text:list-item></text:list>"
        )
        self.assertEqual(extract_odt_text(data), "one\n\ntwo")

    def test_empty_body_returns_empty_string(self):
        self.assertEqual(extract_odt_text(_odt("")), "")

    def test_hidden_characters_survive_extraction(self):
        # The whole point: a zero-width space in the document must reach the
        # scanner intact rather than being normalised away.
        data = _odt("<text:p>hel​lo</text:p>")
        self.assertEqual(extract_odt_text(data), "hel​lo")

    # --- error paths ---

    def test_not_a_zip_raises(self):
        with self.assertRaises(ValueError):
            extract_odt_text(b"this is not a zip file")

    def test_missing_content_xml_raises(self):
        data = _odt(_paragraphs("x"), content_name="something-else.xml")
        with self.assertRaises(ValueError):
            extract_odt_text(data)

    def test_malformed_xml_raises(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", ODT_MIME)
            zf.writestr("content.xml", "<office:document-content><unclosed>")
        with self.assertRaises(ValueError):
            extract_odt_text(buf.getvalue())

    def test_spreadsheet_mimetype_is_rejected_clearly(self):
        # An .ods also has content.xml; without this check it would extract to
        # an empty string — a silent wrong answer rather than an error.
        data = _odt(_paragraphs("x"),
                    mimetype=b"application/vnd.oasis.opendocument.spreadsheet")
        with self.assertRaises(ValueError) as ctx:
            extract_odt_text(data)
        self.assertIn("spreadsheet", str(ctx.exception))

    def test_missing_mimetype_entry_is_tolerated(self):
        # The mimetype entry is optional in the format, so absence alone must
        # not make a readable document fail.
        data = _odt(_paragraphs("still readable"), mimetype=None)
        self.assertEqual(extract_odt_text(data), "still readable")


class TestRealLibreOfficeFile(unittest.TestCase):
    """A genuine LibreOffice-produced .odt, not a hand-built fixture.

    samples/libreoffice-sample.odt was written by LibreOffice itself, so it
    carries all the real scaffolding (styles, settings, RDF manifest, nested
    style spans) that a synthetic fixture leaves out.
    """

    PATH = os.path.join(os.path.dirname(__file__), "samples", "libreoffice-sample.odt")

    def setUp(self):
        if not os.path.exists(self.PATH):
            self.skipTest("sample .odt not present")
        with open(self.PATH, "rb") as f:
            self.text = extract_odt_text(f.read())

    def test_heading_and_paragraphs_extracted(self):
        self.assertTrue(self.text.startswith("Essay on Provenance"))
        self.assertIn("mixed-content case", self.text)

    def test_text_after_inline_formatting_is_not_lost(self):
        # LibreOffice wraps bold/italic in <text:span>; everything after them
        # lives in tails. Losing tails would truncate this sentence.
        self.assertIn("This paragraph has bold and italic text mid-sentence", self.text)

    def test_list_items_extracted(self):
        self.assertIn("First list item", self.text)
        self.assertIn("Second list item", self.text)

    def test_hidden_characters_reach_the_scanner_intact(self):
        report = analyze(self.text)
        self.assertEqual(report["summary"]["flag_count"], 1)
        self.assertEqual(report["findings"][0]["codepoint"], "U+200B")
        self.assertEqual(report["summary"]["homoglyph_count"], 1)
        self.assertEqual(report["homoglyphs"][0]["looks_like"], "password")


class TestDocumentSniffing(unittest.TestCase):

    def _docx(self):
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml",
                        f'<w:document xmlns:w="{ns}"><w:body>'
                        "<w:p><w:r><w:t>from docx</w:t></w:r></w:p>"
                        "</w:body></w:document>")
        return buf.getvalue()

    def test_detects_odt(self):
        self.assertEqual(sniff_document_format(_odt(_paragraphs("x"))), "odt")

    def test_detects_docx(self):
        self.assertEqual(sniff_document_format(self._docx()), "docx")

    def test_detects_neither(self):
        self.assertIsNone(sniff_document_format(b"plain text, not a zip"))

    def test_dispatcher_reads_both_formats(self):
        self.assertEqual(extract_document_text(_odt(_paragraphs("from odt"))),
                         "from odt")
        self.assertEqual(extract_document_text(self._docx()), "from docx")

    def test_dispatcher_rejects_unsupported_data(self):
        with self.assertRaises(ValueError):
            extract_document_text(b"not a document at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
