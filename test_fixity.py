"""Run: python3 -m unittest test_fixity -v"""

import hashlib
import os
import shutil
import struct
import tempfile
import unittest
import zlib

from countermark.c2pa_reader import read_c2pa
from countermark.fixity import (
    FILE_MISSING, MISMATCH, NO_RECORD, OK, UNREADABLE_RECORD,
    file_digest, read_premis_fixity, summarise, verify_against_records,
)
from countermark.premis import to_premis_xml

_PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _png(seed=b""):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)

    def chunk(ctype, data):
        return (struct.pack(">I", len(data)) + ctype + data
                + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))
    extra = chunk(b"tEXt", b"seed\x00" + seed) if seed else b""
    return _PNG_SIG + chunk(b"IHDR", ihdr) + extra + chunk(b"IEND", b"")


class FixityTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="countermark-fixity-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.files = os.path.join(self.dir, "files")
        self.records = os.path.join(self.dir, "premis")
        os.makedirs(self.files)
        os.makedirs(self.records)

    def add_file(self, name, data):
        path = os.path.join(self.files, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def add_record(self, path):
        with open(path, "rb") as f:
            data = f.read()
        name = os.path.basename(path)
        dest = os.path.join(self.records, name + ".premis.xml")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(to_premis_xml(data, read_c2pa(data), filename=name))
        return dest

    def all_files(self):
        return [os.path.join(self.files, n) for n in sorted(os.listdir(self.files))]

    def all_records(self):
        return [os.path.join(self.records, n) for n in sorted(os.listdir(self.records))]


class TestFileDigest(unittest.TestCase):

    def test_matches_hashlib(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"some bytes to hash")
            path = f.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(file_digest(path), hashlib.sha256(b"some bytes to hash").hexdigest())

    def test_chunking_does_not_change_the_result(self):
        payload = b"x" * (1 << 20) + b"tail"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(payload)
            path = f.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(file_digest(path, chunk_size=1024),
                         hashlib.sha256(payload).hexdigest())


class TestReadPremisFixity(FixityTestCase):

    def test_reads_filename_algorithm_and_digest(self):
        path = self.add_file("a.png", _png())
        record = self.add_record(path)
        info = read_premis_fixity(record)
        self.assertEqual(info["filename"], "a.png")
        self.assertEqual(info["algorithm"], "SHA-256")
        self.assertEqual(info["digest"], file_digest(path))

    def test_non_xml_raises(self):
        bad = os.path.join(self.records, "bad.xml")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("not xml at all <<<")
        with self.assertRaises(ValueError):
            read_premis_fixity(bad)

    def test_xml_that_is_not_premis_raises(self):
        bad = os.path.join(self.records, "other.xml")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<something><else/></something>")
        with self.assertRaises(ValueError):
            read_premis_fixity(bad)


class TestVerifyAgainstRecords(FixityTestCase):

    def test_unchanged_files_verify(self):
        for name in ("a.png", "b.png"):
            self.add_record(self.add_file(name, _png(name.encode())))
        results = verify_against_records(self.all_records(), self.all_files())
        self.assertEqual(summarise(results), {OK: 2})

    def test_single_flipped_bit_is_detected(self):
        """The entire point of recording fixity."""
        path = self.add_file("a.png", _png(b"payload-to-corrupt"))
        self.add_record(path)

        with open(path, "rb") as f:
            data = bytearray(f.read())
        data[len(data) // 2] ^= 0x01
        with open(path, "wb") as f:
            f.write(bytes(data))

        results = verify_against_records(self.all_records(), self.all_files())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], MISMATCH)
        self.assertNotEqual(results[0]["expected"], results[0]["actual"])

    def test_truncated_file_is_detected(self):
        path = self.add_file("a.png", _png(b"long-enough-to-truncate"))
        self.add_record(path)
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(data[:-10])
        results = verify_against_records(self.all_records(), self.all_files())
        self.assertEqual(results[0]["status"], MISMATCH)

    def test_missing_file_is_reported(self):
        path = self.add_file("a.png", _png())
        self.add_record(path)
        os.unlink(path)
        results = verify_against_records(self.all_records(), [])
        self.assertEqual(results[0]["status"], FILE_MISSING)

    def test_file_without_a_record_is_reported_not_ignored(self):
        # Silence about an unrecorded file would be its own dishonesty.
        recorded = self.add_file("a.png", _png(b"a"))
        self.add_record(recorded)
        self.add_file("b.png", _png(b"b"))  # no record written
        results = verify_against_records(self.all_records(), self.all_files())
        statuses = {r["status"] for r in results}
        self.assertEqual(statuses, {OK, NO_RECORD})

    def test_unreadable_record_is_reported(self):
        with open(os.path.join(self.records, "broken.xml"), "w", encoding="utf-8") as f:
            f.write("<not-premis/>")
        results = verify_against_records(self.all_records(), [])
        self.assertEqual(results[0]["status"], UNREADABLE_RECORD)

    def test_mixed_collection_reports_each_status(self):
        good = self.add_file("good.png", _png(b"good"))
        self.add_record(good)
        bad = self.add_file("bad.png", _png(b"bad"))
        self.add_record(bad)
        with open(bad, "ab") as f:
            f.write(b"appended")
        self.add_file("unrecorded.png", _png(b"unrecorded"))

        results = verify_against_records(self.all_records(), self.all_files())
        counts = summarise(results)
        self.assertEqual(counts.get(OK), 1)
        self.assertEqual(counts.get(MISMATCH), 1)
        self.assertEqual(counts.get(NO_RECORD), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
