"""Run: python3 -m unittest test_cli -v

Tests drive cli.main() in-process with argv lists, capturing stdout/stderr,
so they exercise argument parsing and exit codes the same way a shell would —
without spawning subprocesses.
"""

import contextlib
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import zipfile
import zlib

from unwatermark.cli import main

ZWSP = "​"
NBSP = " "
CYRILLIC_S = "ѕ"

_PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _png_chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def _plain_png():
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return _PNG_SIG + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IEND", b"")


def run(argv):
    """Run the CLI, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="unwatermark-cli-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        p = self.path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def write_bytes(self, name, data):
        p = self.path(name)
        with open(p, "wb") as f:
            f.write(data)
        return p


class TestScan(CliTestCase):

    def test_reports_hidden_characters(self):
        p = self.write("dirty.txt", f"hel{ZWSP}lo world")
        code, out, _ = run(["scan", p])
        self.assertEqual(code, 0)
        self.assertIn("U+200B", out)
        self.assertIn("ZERO WIDTH SPACE", out)

    def test_clean_text_says_so_and_keeps_the_caveat(self):
        p = self.write("clean.txt", "nothing hidden here")
        code, out, _ = run(["scan", p])
        self.assertEqual(code, 0)
        self.assertIn("No hidden or disguised characters", out)
        # The honesty boundary must survive into CLI output too.
        self.assertIn("does not rule out a statistical watermark", out)

    def test_only_findings_suppresses_clean_files(self):
        dirty = self.write("dirty.txt", f"a{ZWSP}b")
        clean_file = self.write("clean.txt", "ordinary")
        code, out, _ = run(["scan", "--only-findings", dirty, clean_file])
        self.assertEqual(code, 0)
        self.assertIn("dirty.txt", out)
        self.assertNotIn("clean.txt", out)

    def test_json_output_is_parseable_and_keyed_by_path(self):
        p = self.write("a.txt", f"x{ZWSP}y")
        code, out, _ = run(["scan", "--json", p])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn(p, data)
        self.assertEqual(data[p]["summary"]["flag_count"], 1)

    def test_reads_docx_transparently(self):
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        doc = (f'<w:document xmlns:w="{ns}"><w:body>'
               f'<w:p><w:r><w:t>hi{ZWSP}there</w:t></w:r></w:p>'
               "</w:body></w:document>")
        p = self.path("essay.docx")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", doc)
        code, out, _ = run(["scan", p])
        self.assertEqual(code, 0)
        self.assertIn("U+200B", out)

    def test_reads_odt_transparently(self):
        office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        content = (
            f'<office:document-content xmlns:office="{office}" xmlns:text="{text_ns}">'
            f"<office:body><office:text><text:p>hi{ZWSP}there</text:p></office:text>"
            "</office:body></office:document-content>"
        )
        p = self.path("essay.odt")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
            z.writestr("content.xml", content)
        code, out, _ = run(["scan", p])
        self.assertEqual(code, 0)
        self.assertIn("U+200B", out)

    def test_document_format_detected_from_content_not_extension(self):
        """A .docx saved under a .txt name should still read as a document."""
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        p = self.path("mislabelled.txt")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{ns}"><w:body>'
                       f'<w:p><w:r><w:t>a{ZWSP}b</w:t></w:r></w:p>'
                       "</w:body></w:document>")
        code, out, _ = run(["scan", p])
        self.assertEqual(code, 0)
        self.assertIn("U+200B", out)

    def test_missing_file_is_a_usage_error(self):
        code, _, err = run(["scan", self.path("nope.txt")])
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_directory_without_recursive_is_refused(self):
        self.write("sub/a.txt", "hello")
        code, _, err = run(["scan", self.path("sub")])
        self.assertEqual(code, 2)
        self.assertIn("--recursive", err)

    def test_recursive_walks_directories(self):
        self.write("sub/deep/a.txt", f"a{ZWSP}b")
        code, out, _ = run(["scan", "--recursive", self.dir])
        self.assertEqual(code, 0)
        self.assertIn("a.txt", out)

    def test_non_utf8_file_reports_error_and_nonzero_exit(self):
        p = self.write_bytes("bad.txt", b"\xff\xfe\x00binary")
        code, _, err = run(["scan", p])
        self.assertEqual(code, 1)
        self.assertIn("not valid UTF-8", err)


class TestClean(CliTestCase):

    def test_cleaned_text_goes_to_stdout_summary_to_stderr(self):
        p = self.write("dirty.txt", f"hel{ZWSP}lo")
        code, out, err = run(["clean", p])
        self.assertEqual(code, 0)
        # stdout must be *only* the cleaned text, so it can be piped.
        self.assertEqual(out, "hello")
        self.assertIn("1 removed", err)

    def test_refuses_to_write_multiple_files_to_stdout(self):
        a = self.write("a.txt", "one")
        b = self.write("b.txt", "two")
        code, out, err = run(["clean", a, b])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("refusing", err)

    def test_output_dir_writes_cleaned_copies(self):
        self.write("a.txt", f"a{ZWSP}b")
        self.write("b.txt", f"c{NBSP}d")
        outdir = self.path("out")
        code, _, _ = run(["clean", "--recursive", self.dir, "-o", outdir])
        self.assertEqual(code, 0)
        with open(os.path.join(outdir, "a.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "ab")
        with open(os.path.join(outdir, "b.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "c d")  # NBSP normalised, not deleted

    def test_in_place_overwrites_the_original(self):
        p = self.write("a.txt", f"a{ZWSP}b")
        code, _, _ = run(["clean", p, "--in-place"])
        self.assertEqual(code, 0)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ab")

    def test_in_place_and_output_dir_conflict(self):
        p = self.write("a.txt", "x")
        code, _, err = run(["clean", p, "--in-place", "-o", self.path("out")])
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", err)

    def test_homoglyphs_untouched_unless_requested(self):
        p = self.write("a.txt", f"pa{CYRILLIC_S}sword")
        _, out, _ = run(["clean", p])
        self.assertIn(CYRILLIC_S, out)
        _, out2, _ = run(["clean", p, "--fix-homoglyphs"])
        self.assertEqual(out2, "password")

    def test_in_place_refuses_docx(self):
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        p = self.path("a.docx")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{ns}"><w:body></w:body></w:document>')
        code, _, err = run(["clean", p, "--in-place"])
        self.assertEqual(code, 1)
        self.assertIn("cannot rewrite .docx", err)

    def test_in_place_refuses_odt(self):
        office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        p = self.path("a.odt")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
            z.writestr("content.xml",
                       f'<office:document-content xmlns:office="{office}">'
                       "<office:body><office:text/></office:body>"
                       "</office:document-content>")
        code, _, err = run(["clean", p, "--in-place"])
        self.assertEqual(code, 1)
        self.assertIn("cannot rewrite .odt", err)


class TestC2pa(CliTestCase):

    def test_png_without_manifest_reports_none(self):
        p = self.write_bytes("plain.png", _plain_png())
        code, out, _ = run(["c2pa", p])
        self.assertEqual(code, 0)
        self.assertIn("No C2PA manifest is embedded", out)

    def test_caveat_present_in_single_file_output(self):
        p = self.write_bytes("plain.png", _plain_png())
        _, out, _ = run(["c2pa", p])
        self.assertIn("NOT cryptographically verified", out)

    def test_batch_mode_prints_one_line_per_file(self):
        a = self.write_bytes("a.png", _plain_png())
        b = self.write_bytes("b.png", _plain_png())
        code, out, _ = run(["c2pa", a, b])
        self.assertEqual(code, 0)
        self.assertEqual(len([l for l in out.splitlines() if l.strip()]), 2)
        self.assertIn("no manifest", out)

    def test_sidecar_dir_writes_one_record_per_image(self):
        p = self.write_bytes("a.png", _plain_png())
        sidecars = self.path("sidecars")
        code, _, _ = run(["c2pa", p, "--sidecar-dir", sidecars])
        self.assertEqual(code, 0)
        dest = os.path.join(sidecars, "a.png.c2pa-sidecar.json")
        self.assertTrue(os.path.exists(dest))
        with open(dest, encoding="utf-8") as f:
            record = json.load(f)
        self.assertEqual(record["record_type"], "unwatermark-c2pa-read")
        self.assertEqual(record["source_filename"], "a.png")
        # Even a "nothing found" record must carry the caveat.
        self.assertIn("NOT cryptographically verified", record["caveat"])

    def test_json_output_keyed_by_path(self):
        p = self.write_bytes("a.png", _plain_png())
        code, out, _ = run(["c2pa", "--json", p])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertFalse(data[p]["found"])

    def test_premis_dir_writes_valid_preservation_metadata(self):
        import xml.etree.ElementTree as ET
        p = self.write_bytes("a.png", _plain_png())
        premis_dir = self.path("premis")
        code, _, _ = run(["c2pa", p, "--premis-dir", premis_dir])
        self.assertEqual(code, 0)
        dest = os.path.join(premis_dir, "a.png.premis.xml")
        self.assertTrue(os.path.exists(dest))
        root = ET.parse(dest).getroot()  # must be well-formed
        self.assertEqual(root.tag, "{http://www.loc.gov/premis/v3}premis")
        self.assertEqual(root.get("version"), "3.0")

    def test_sidecar_and_premis_can_be_written_together(self):
        p = self.write_bytes("a.png", _plain_png())
        code, _, _ = run(["c2pa", p,
                          "--sidecar-dir", self.path("side"),
                          "--premis-dir", self.path("prem")])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(
            os.path.join(self.path("side"), "a.png.c2pa-sidecar.json")))
        self.assertTrue(os.path.exists(
            os.path.join(self.path("prem"), "a.png.premis.xml")))


class TestVerify(CliTestCase):

    def _setup_collection(self):
        files = self.path("files")
        premis = self.path("premis")
        os.makedirs(files)
        a = os.path.join(files, "a.png")
        with open(a, "wb") as f:
            f.write(_plain_png())
        run(["c2pa", a, "--premis-dir", premis])
        return files, premis, a

    def test_unmodified_file_verifies(self):
        files, premis, _ = self._setup_collection()
        code, out, _ = run(["verify", "--premis-dir", premis, "-r", files])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_modified_file_fails_with_nonzero_exit(self):
        files, premis, a = self._setup_collection()
        with open(a, "ab") as f:
            f.write(b"corruption")
        code, out, _ = run(["verify", "--premis-dir", premis, "-r", files])
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)

    def test_only_problems_hides_passing_files(self):
        files, premis, _ = self._setup_collection()
        code, out, _ = run(["verify", "--premis-dir", premis, "-r", files,
                            "--only-problems"])
        self.assertEqual(code, 0)
        self.assertNotIn("OK        a.png", out)

    def test_missing_premis_dir_is_a_usage_error(self):
        files, _, _ = self._setup_collection()
        code, _, err = run(["verify", "--premis-dir", self.path("nope"), "-r", files])
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_json_output(self):
        files, premis, _ = self._setup_collection()
        code, out, _ = run(["verify", "--premis-dir", premis, "-r", files, "--json"])
        self.assertEqual(code, 0)
        results = json.loads(out)
        self.assertEqual(results[0]["status"], "ok")


class TestBag(CliTestCase):

    def test_bag_contains_image_sidecar_and_premis(self):
        p = self.write_bytes("a.png", _plain_png())
        bag = self.path("bag")
        code, _, err = run(["bag", p, "-o", bag])
        self.assertEqual(code, 0)
        data_dir = os.path.join(bag, "data")
        self.assertEqual(sorted(os.listdir(data_dir)),
                         ["a.png", "a.png.c2pa-sidecar.json", "a.png.premis.xml"])
        self.assertIn("Bagged 1 image", err)

    def test_bag_is_structurally_complete(self):
        p = self.write_bytes("a.png", _plain_png())
        bag = self.path("bag")
        run(["bag", p, "-o", bag])
        for name in ("bagit.txt", "bag-info.txt", "manifest-sha256.txt",
                     "tagmanifest-sha256.txt"):
            self.assertTrue(os.path.exists(os.path.join(bag, name)), name)

    def test_bagged_files_verify_against_their_own_premis(self):
        """A bag should be self-consistent: its PREMIS describes its payload."""
        p = self.write_bytes("a.png", _plain_png())
        bag = self.path("bag")
        run(["bag", p, "-o", bag])
        data_dir = os.path.join(bag, "data")
        code, out, _ = run(["verify", "--premis-dir", data_dir, "-r", data_dir])
        self.assertEqual(code, 0, out)

    def test_duplicate_basenames_are_refused(self):
        os.makedirs(self.path("x"))
        os.makedirs(self.path("y"))
        for sub in ("x", "y"):
            with open(self.path(f"{sub}/same.png"), "wb") as f:
                f.write(_plain_png())
        code, _, err = run(["bag", self.path("x/same.png"), self.path("y/same.png"),
                            "-o", self.path("bag")])
        self.assertEqual(code, 2)
        self.assertIn("basename", err)


class TestDiff(CliTestCase):

    def test_reports_both_percentages(self):
        a = self.write("draft.txt", "i was very intrested in the topic")
        b = self.write("final.txt", "i was very interested in the topic")
        code, out, _ = run(["diff", a, b])
        self.assertEqual(code, 0)
        self.assertIn("word-for-word", out)
        self.assertIn("minor spelling/grammar fixes", out)

    def test_json_mode(self):
        a = self.write("draft.txt", "one two three")
        b = self.write("final.txt", "one two three four")
        code, out, _ = run(["diff", "--json", a, b])
        self.assertEqual(code, 0)
        stats = json.loads(out)["stats"]
        self.assertEqual(stats["inserted"], 1)


class TestRealSamplesThroughCli(unittest.TestCase):
    """A couple of end-to-end runs against the real sample corpus."""

    SAMPLES = os.path.join(os.path.dirname(__file__), "samples")

    def _sample(self, name):
        p = os.path.join(self.SAMPLES, name)
        if not os.path.exists(p):
            self.skipTest(f"{name} not present")
        return p

    def test_signed_png_reports_manifest(self):
        p = self._sample("ChatGPT_Image.png")
        code, out, _ = run(["c2pa", p])
        self.assertEqual(code, 0)
        self.assertIn("GPT-4o", out)
        self.assertIn("NOT cryptographically verified", out)

    def test_external_manifest_file_is_reported_not_silently_empty(self):
        p = self._sample("libpng-test_with_url.png")
        code, out, _ = run(["c2pa", p])
        self.assertEqual(code, 0)
        self.assertIn("EXTERNAL manifest", out)
        self.assertIn(".c2pa", out)
        self.assertIn("does not fetch it", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
