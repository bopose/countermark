"""Run: python3 -m unittest test_docx_extract -v"""

import io
import unittest
import zipfile

from countermark import extract_docx_text

_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_with_document_xml(document_xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _make_docx(paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    return _docx_with_document_xml(
        f'<w:document xmlns:w="{_NS}"><w:body>{body}</w:body></w:document>'
    )


class TestDocxExtract(unittest.TestCase):

    def test_extracts_paragraphs_joined_by_blank_line(self):
        data = _make_docx(["Hello world.", "Second paragraph."])
        self.assertEqual(extract_docx_text(data), "Hello world.\n\nSecond paragraph.")

    def test_not_a_zip_raises(self):
        with self.assertRaises(ValueError):
            extract_docx_text(b"not a zip file at all")

    def test_missing_document_xml_raises(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("something-else.txt", "x")
        with self.assertRaises(ValueError):
            extract_docx_text(buf.getvalue())

    def test_malformed_xml_raises(self):
        data = _docx_with_document_xml("<w:document><unclosed>")
        with self.assertRaises(ValueError):
            extract_docx_text(data)

    def test_tab_and_line_break_handled(self):
        data = _docx_with_document_xml(
            f'<w:document xmlns:w="{_NS}"><w:body>'
            "<w:p><w:r><w:t>A</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>B</w:t></w:r>"
            "<w:r><w:br/></w:r><w:r><w:t>C</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        self.assertEqual(extract_docx_text(data), "A\tB\nC")

    def test_empty_body_returns_empty_string(self):
        data = _docx_with_document_xml(f'<w:document xmlns:w="{_NS}"><w:body></w:body></w:document>')
        self.assertEqual(extract_docx_text(data), "")

    def test_multiple_runs_in_one_paragraph_concatenated(self):
        data = _docx_with_document_xml(
            f'<w:document xmlns:w="{_NS}"><w:body>'
            "<w:p><w:r><w:t>Hello, </w:t></w:r><w:r><w:t>world.</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        self.assertEqual(extract_docx_text(data), "Hello, world.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
