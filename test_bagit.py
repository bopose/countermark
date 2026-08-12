"""Run: python3 -m unittest test_bagit -v

Checks the bag against RFC 8493's actual requirements: the bagit.txt
declaration, a payload under data/, sha256 manifests whose checksums are
correct, and a Payload-Oxum that matches the payload.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
from datetime import datetime

from unwatermark.bagit import make_bag


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse_manifest(path):
    """Manifest lines are '<checksum>  <path>' — the same format as sha256sum."""
    entries = {}
    for line in _read(path).splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        entries[rel] = digest
    return entries


class TestMakeBag(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="unwatermark-bag-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.bag = os.path.join(self.dir, "bag")

        self.source = os.path.join(self.dir, "source.bin")
        with open(self.source, "wb") as f:
            f.write(b"pretend this is an image")

    def _make(self, **kw):
        return make_bag(self.bag, {
            "image.bin": self.source,
            "image.bin.premis.xml": "<premis/>",
            "notes.txt": b"raw bytes payload",
        }, **kw)

    def test_creates_the_required_bagit_structure(self):
        self._make()
        for name in ("bagit.txt", "bag-info.txt",
                     "manifest-sha256.txt", "tagmanifest-sha256.txt"):
            self.assertTrue(os.path.exists(os.path.join(self.bag, name)), name)
        self.assertTrue(os.path.isdir(os.path.join(self.bag, "data")))

    def test_bagit_txt_declares_version_and_encoding(self):
        self._make()
        text = _read(os.path.join(self.bag, "bagit.txt"))
        self.assertIn("BagIt-Version: 1.0", text)
        self.assertIn("Tag-File-Character-Encoding: UTF-8", text)

    def test_payload_lands_under_data(self):
        self._make()
        data_dir = os.path.join(self.bag, "data")
        self.assertEqual(sorted(os.listdir(data_dir)),
                         ["image.bin", "image.bin.premis.xml", "notes.txt"])

    def test_copied_file_content_is_preserved_byte_for_byte(self):
        self._make()
        with open(os.path.join(self.bag, "data", "image.bin"), "rb") as f:
            self.assertEqual(f.read(), b"pretend this is an image")

    def test_bytes_and_str_payload_entries_both_written(self):
        self._make()
        with open(os.path.join(self.bag, "data", "notes.txt"), "rb") as f:
            self.assertEqual(f.read(), b"raw bytes payload")
        self.assertEqual(_read(os.path.join(self.bag, "data", "image.bin.premis.xml")),
                         "<premis/>")

    def test_manifest_checksums_are_correct(self):
        self._make()
        manifest = _parse_manifest(os.path.join(self.bag, "manifest-sha256.txt"))
        self.assertEqual(len(manifest), 3)
        for rel, recorded in manifest.items():
            full = os.path.join(self.bag, rel)
            with open(full, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(recorded, actual, rel)

    def test_manifest_paths_are_relative_to_the_bag_and_use_forward_slashes(self):
        self._make()
        manifest = _parse_manifest(os.path.join(self.bag, "manifest-sha256.txt"))
        for rel in manifest:
            self.assertTrue(rel.startswith("data/"), rel)
            self.assertNotIn("\\", rel)

    def test_tagmanifest_covers_the_tag_files_and_is_correct(self):
        self._make()
        tags = _parse_manifest(os.path.join(self.bag, "tagmanifest-sha256.txt"))
        self.assertEqual(set(tags), {"bagit.txt", "bag-info.txt", "manifest-sha256.txt"})
        for rel, recorded in tags.items():
            with open(os.path.join(self.bag, rel), "rb") as f:
                self.assertEqual(recorded, hashlib.sha256(f.read()).hexdigest(), rel)

    def test_payload_oxum_matches_the_payload(self):
        self._make()
        info = _read(os.path.join(self.bag, "bag-info.txt"))
        oxum = [l.split(": ", 1)[1] for l in info.splitlines()
                if l.startswith("Payload-Oxum")][0]
        total = count = 0
        for root, _dirs, names in os.walk(os.path.join(self.bag, "data")):
            for n in names:
                total += os.path.getsize(os.path.join(root, n))
                count += 1
        self.assertEqual(oxum, f"{total}.{count}")

    def test_bagging_date_recorded_when_a_timestamp_is_given(self):
        self._make(timestamp=datetime(2026, 8, 12))
        self.assertIn("Bagging-Date: 2026-08-12",
                      _read(os.path.join(self.bag, "bag-info.txt")))

    def test_custom_bag_info_is_included(self):
        self._make(bag_info={"External-Description": "a test bag"})
        self.assertIn("External-Description: a test bag",
                      _read(os.path.join(self.bag, "bag-info.txt")))

    def test_refuses_to_bag_into_a_non_empty_directory(self):
        os.makedirs(self.bag)
        with open(os.path.join(self.bag, "existing.txt"), "w", encoding="utf-8") as f:
            f.write("someone else's data")
        with self.assertRaises(ValueError):
            self._make()

    def test_empty_existing_directory_is_acceptable(self):
        os.makedirs(self.bag)
        self._make()  # must not raise
        self.assertTrue(os.path.exists(os.path.join(self.bag, "bagit.txt")))

    def test_nested_payload_paths_are_supported(self):
        make_bag(self.bag, {"sub/dir/file.txt": "nested"})
        self.assertTrue(os.path.exists(
            os.path.join(self.bag, "data", "sub", "dir", "file.txt")))
        manifest = _parse_manifest(os.path.join(self.bag, "manifest-sha256.txt"))
        self.assertIn("data/sub/dir/file.txt", manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
