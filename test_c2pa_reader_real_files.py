"""Run: python3 -m unittest test_c2pa_reader_real_files -v

Integration tests against real files from the official C2PA public-testfiles
repository (https://github.com/c2pa-org/public-testfiles, CC BY-SA), kept in
samples/. Unlike every other test in this project, these are NOT built by us —
they're real, third-party-generated JPEGs from three different vendors
(Adobe/c2pa-rs, Nikon, Truepic), so this is the actual validation against
ground truth that hand-built fixtures cannot provide.

The corpus deliberately includes the repository's *negative* cases —
files with invalid signatures, tampered assertions, hash mismatches, and
missing claims. Those matter enormously here, because this tool does not
verify signatures: TestTamperBlindness below proves, with real bytes, that a
tampered file reads identically to a valid one. That is the whole reason for
the UNVERIFIED_CAVEAT, demonstrated rather than merely asserted.

Every test skips gracefully if its sample file is absent, so the suite still
passes on a checkout without the 21 MB samples/ directory.
"""

import json
import os
import unittest

from unwatermark.c2pa_reader import UNVERIFIED_CAVEAT, read_c2pa, to_sidecar, to_summary_text

SAMPLES = os.path.join(os.path.dirname(__file__), "samples")


def _sample(name):
    return os.path.join(SAMPLES, name)


def _read(name):
    with open(_sample(name), "rb") as f:
        return f.read()


def _has(name):
    return os.path.exists(_sample(name))


def _all_samples():
    if not os.path.isdir(SAMPLES):
        return []
    return sorted(
        f for f in os.listdir(SAMPLES)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    )


# Files documented as carrying no Content Credentials at all. sample1.png is
# especially useful: it's a real PNG stuffed with ordinary eXIf/tEXt metadata,
# so it proves the PNG "not found" path doesn't false-positive on normal chunks.
NO_MANIFEST = [
    "adobe-20220124-A.jpg",
    "adobe-20220124-I.jpg",
    "sample1.png",
    "libpng-test.png",
]

# One representative file per generator vendor, to prove the reader isn't
# accidentally specific to Adobe's c2pa-rs output.
VENDORS = [
    ("adobe-20220124-CA.jpg", "c2pa-rs"),
    ("nikon-20221019-building.jpeg", "Nikon"),
    ("truepic-20230212-camera.jpg", "Truepic"),
]


def _find_claim_generator(node):
    """Depth-first search for the first c2pa.claim's claim_generator string."""
    for child in node.get("children", []):
        if child.get("label") == "c2pa.claim":
            for sub in child.get("children", []):
                value = sub.get("content", {}).get("value")
                if isinstance(value, dict) and "claim_generator" in value:
                    return value["claim_generator"]
        if child.get("kind") == "superbox":
            found = _find_claim_generator(child)
            if found:
                return found
    return None


@unittest.skipUnless(_all_samples(), "samples/ directory not present")
class TestAllRealFilesRobustness(unittest.TestCase):
    """Sweep the whole corpus: nothing may crash, everything stays well-formed.

    This includes the repository's deliberately-corrupt negative cases, which
    are exactly the inputs most likely to break a hand-written binary parser.
    """

    def test_no_file_raises(self):
        for name in _all_samples():
            with self.subTest(file=name):
                try:
                    read_c2pa(_read(name))
                except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
                    self.fail(f"{name} raised {type(exc).__name__}: {exc}")

    def test_every_result_is_well_formed_and_carries_the_caveat(self):
        for name in _all_samples():
            with self.subTest(file=name):
                result = read_c2pa(_read(name))
                self.assertIn("found", result)
                self.assertIn("manifest", result)
                self.assertEqual(result["caveat"], UNVERIFIED_CAVEAT)
                # A manifest is present if and only if it parsed cleanly.
                if result["manifest"] is not None:
                    self.assertTrue(result["found"])
                    self.assertIsNone(result["error"])

    def test_every_result_is_json_serialisable(self):
        for name in _all_samples():
            with self.subTest(file=name):
                json.dumps(to_sidecar(read_c2pa(_read(name)), source_filename=name))

    def test_summary_text_never_omits_the_caveat(self):
        for name in _all_samples():
            with self.subTest(file=name):
                text = to_summary_text(read_c2pa(_read(name)))
                self.assertIn("NOT cryptographically verified", text)


class TestFilesWithoutManifest(unittest.TestCase):

    def test_reports_not_found_without_calling_it_an_error(self):
        for name in NO_MANIFEST:
            if not _has(name):
                continue
            with self.subTest(file=name):
                result = read_c2pa(_read(name))
                self.assertFalse(result["found"])
                self.assertIsNone(result["error"])
                self.assertIsNone(result["manifest"])
                self.assertIsNone(result["external_manifest_url"])
                self.assertIn("No C2PA manifest is embedded", to_summary_text(result))


class TestMultipleVendors(unittest.TestCase):
    """The reader must handle real output from more than one implementation."""

    def test_each_vendor_parses_and_names_its_generator(self):
        for name, expected_generator in VENDORS:
            if not _has(name):
                continue
            with self.subTest(file=name):
                result = read_c2pa(_read(name))
                self.assertTrue(result["found"])
                self.assertIsNone(result["error"])
                generator = _find_claim_generator(result["manifest"])
                self.assertIsNotNone(generator, f"no claim_generator found in {name}")
                self.assertIn(expected_generator, generator)


class TestTamperBlindness(unittest.TestCase):
    """Proof, in real bytes, that this tool cannot detect tampering.

    adobe-20220124-CA.jpg and adobe-20220124-E-sig-CA.jpg are the same image;
    the C2PA repository documents the latter as having an invalid signature
    (their Verify tool reports "Invalid credentials: Invalid signature"). The
    two files are byte-identical apart from six bytes inside the manifest,
    where the ASCII text "images" was overwritten with "xxxxxx" to break the
    signature.

    This tool reads both without complaint, because it never checks the
    signature. These tests pin that behaviour deliberately: if a future change
    ever made the tool *appear* to detect tampering, the honesty caveat would
    become a lie, and these tests would fail first.
    """

    VALID = "adobe-20220124-CA.jpg"
    TAMPERED = "adobe-20220124-E-sig-CA.jpg"

    def setUp(self):
        if not (_has(self.VALID) and _has(self.TAMPERED)):
            self.skipTest("tamper-pair samples not present")

    def test_the_pair_really_is_a_minimal_byte_level_tamper(self):
        valid, tampered = _read(self.VALID), _read(self.TAMPERED)
        self.assertEqual(len(valid), len(tampered), "same length expected")
        differing = [i for i, (a, b) in enumerate(zip(valid, tampered)) if a != b]
        self.assertEqual(len(differing), 6, "expected exactly six tampered bytes")
        start = differing[0]
        self.assertEqual(valid[start:start + 6], b"images")
        self.assertEqual(tampered[start:start + 6], b"xxxxxx")

    def test_tampered_file_still_parses_with_no_error_reported(self):
        result = read_c2pa(_read(self.TAMPERED))
        self.assertTrue(result["found"])
        self.assertIsNone(result["error"], "this tool must not claim to detect tampering")
        self.assertIsNotNone(result["manifest"])

    def test_tampered_content_is_shown_verbatim_not_silently_repaired(self):
        # The reader surfaces exactly what the file says, corrupted or not.
        generator = _find_claim_generator(read_c2pa(_read(self.TAMPERED))["manifest"])
        self.assertIn("xxxxxx", generator)
        self.assertIn("images", _find_claim_generator(read_c2pa(_read(self.VALID))["manifest"]))

    def test_both_outputs_carry_the_same_unverified_caveat(self):
        # A reader has no way to tell these apart from our output alone —
        # which is precisely what the caveat warns about.
        for name in (self.VALID, self.TAMPERED):
            text = to_summary_text(read_c2pa(_read(name)))
            self.assertIn("tampered or entirely fabricated", text)


class TestOtherNegativeCases(unittest.TestCase):
    """The repo's other corrupt cases must parse without crashing either."""

    CASES = [
        "adobe-20220124-E-uri-CA.jpg",      # tampered assertion (URI hash mismatch)
        "adobe-20220124-E-dat-CA.jpg",      # hard binding hash mismatch
        "adobe-20220124-E-clm-CAICAI.jpg",  # referenced claim missing
        "adobe-20220124-XCA.jpg",           # off-the-golden-path hash mismatch
        "adobe-20220124-XCI.jpg",
    ]

    def test_corrupt_cases_parse_without_error_or_crash(self):
        for name in self.CASES:
            if not _has(name):
                continue
            with self.subTest(file=name):
                result = read_c2pa(_read(name))
                self.assertTrue(result["found"])
                self.assertIsNone(result["error"])


class TestManifestChains(unittest.TestCase):
    """A manifest store holds a chain: one manifest per edit generation."""

    def test_multi_manifest_file_exposes_every_manifest(self):
        name = "adobe-20220124-CACA.jpg"
        if not _has(name):
            self.skipTest("sample not present")
        result = read_c2pa(_read(name))
        self.assertEqual(len(result["manifest"]["children"]), 2)

    def test_deeply_chained_file_exposes_all_six_manifests(self):
        name = "adobe-20220124-CAIAIIICAICIICAIICICA.jpg"
        if not _has(name):
            self.skipTest("sample not present")
        result = read_c2pa(_read(name))
        manifests = result["manifest"]["children"]
        self.assertEqual(len(manifests), 6)
        for m in manifests:
            self.assertEqual(m["uuid_meaning"], "Manifest")
            labels = {c.get("label") for c in m["children"]}
            self.assertIn("c2pa.claim", labels)
            self.assertIn("c2pa.assertions", labels)


@unittest.skipUnless(_has("libpng-test_with_url.png"), "sample not present")
class TestExternalManifestPointer(unittest.TestCase):
    """A real file whose provenance lives outside it.

    libpng-test_with_url.png (from c2pa-rs's fixtures) embeds no manifest, but
    its XMP carries a dcterms:provenance URL pointing at a separate .c2pa
    file. Reporting that as a flat "no manifest" would be misleading — there
    *is* provenance, it just isn't in this file. We surface the pointer and
    say plainly that we don't follow it.
    """

    def setUp(self):
        self.result = read_c2pa(_read("libpng-test_with_url.png"))

    def test_no_embedded_manifest_but_a_pointer_is_reported(self):
        self.assertFalse(self.result["found"])
        self.assertIsNone(self.result["error"])
        self.assertTrue(self.result["external_manifest_url"].endswith(".c2pa"))

    def test_summary_explains_the_pointer_and_that_we_do_not_fetch_it(self):
        text = to_summary_text(self.result)
        self.assertIn("EXTERNAL manifest", text)
        self.assertIn(self.result["external_manifest_url"], text)
        self.assertIn("does not fetch it", text)

    def test_pointer_is_preserved_in_the_sidecar(self):
        sidecar = to_sidecar(self.result, source_filename="libpng-test_with_url.png")
        self.assertEqual(sidecar["external_manifest_url"],
                         self.result["external_manifest_url"])


@unittest.skipUnless(_has("ChatGPT_Image.png"), "PNG sample not present")
class TestRealSignedPng(unittest.TestCase):
    """The PNG path, validated against a real signed PNG.

    ChatGPT_Image.png is a genuine ChatGPT/GPT-4o output from the Content
    Authenticity Initiative's example-assets repository (MIT licensed). It
    embeds its manifest in a single PNG 'caBX' chunk — the PNG counterpart to
    the JPEG APP11 path — and it exercises two things no hand-built fixture
    caught: indefinite-length CBOR (which the C2PA spec forbids but this real
    file uses anyway), and the v2/v3 assertion labels.
    """

    def setUp(self):
        self.result = read_c2pa(_read("ChatGPT_Image.png"))

    def test_png_manifest_found_and_parsed(self):
        self.assertTrue(self.result["found"])
        self.assertIsNone(self.result["error"])
        self.assertEqual(self.result["manifest"]["uuid_meaning"], "C2PA Manifest Store")

    def test_every_content_box_decodes_without_error(self):
        """No 'could not decode' anywhere — this is what caught the CBOR bug."""
        failures = []

        def walk(node, path=""):
            for child in node.get("children", []):
                here = f"{path}/{child.get('label') or child.get('type')}"
                if child.get("kind") == "superbox":
                    walk(child, here)
                else:
                    content = child.get("content", {})
                    if "decode_error" in content:
                        failures.append(f"{here}: {content['decode_error']}")

        walk(self.result["manifest"])
        self.assertEqual(failures, [], f"content boxes failed to decode: {failures}")

    def test_declares_itself_ai_generated(self):
        """The real payoff: this image openly declares its AI provenance."""
        actions = []

        def collect(node):
            for child in node.get("children", []):
                if child.get("label") == "c2pa.actions.v2":
                    actions.append(child["children"][0]["content"]["value"])
                if child.get("kind") == "superbox":
                    collect(child)

        collect(self.result["manifest"])
        self.assertTrue(actions, "no c2pa.actions.v2 assertion found")
        created = [
            a for group in actions for a in group.get("actions", [])
            if a.get("action") == "c2pa.created"
        ]
        self.assertTrue(created, "no c2pa.created action found")
        self.assertEqual(created[0]["softwareAgent"]["name"], "GPT-4o")
        self.assertIn("trainedAlgorithmicMedia", created[0]["digitalSourceType"])


@unittest.skipUnless(
    _has("ChatGPT_Image.png") and _has("ChatGPT_Image.json"),
    "PNG sample or its reference manifest not present",
)
class TestPngMatchesReferenceImplementation(unittest.TestCase):
    """Cross-check our parse against the official c2patool output.

    example-assets publishes, alongside each image, the manifest as extracted
    by the reference implementation. Comparing against it is the strongest
    correctness check available short of running c2patool ourselves.
    """

    def setUp(self):
        self.mine = read_c2pa(_read("ChatGPT_Image.png"))
        with open(_sample("ChatGPT_Image.json"), encoding="utf-8") as f:
            self.reference = json.load(f)

    def _claim_of(self, manifest_node):
        for child in manifest_node["children"]:
            if child.get("label") in ("c2pa.claim.v2", "c2pa.claim"):
                return child["children"][0]["content"]["value"]
        self.fail(f"no claim in manifest {manifest_node.get('label')!r}")

    def test_same_set_of_manifest_urns(self):
        self.assertEqual(
            {c["label"] for c in self.mine["manifest"]["children"]},
            set(self.reference["manifests"].keys()),
        )

    def test_same_title_and_generator_per_manifest(self):
        for node in self.mine["manifest"]["children"]:
            with self.subTest(manifest=node["label"]):
                claim = self._claim_of(node)
                ref = self.reference["manifests"][node["label"]]
                self.assertEqual(claim.get("dc:title"), ref.get("title"))
                ref_generator = (ref.get("claim_generator_info") or [{}])[0].get("name")
                self.assertEqual(
                    claim.get("claim_generator_info", {}).get("name"), ref_generator
                )

    def test_reference_calls_this_file_invalid_while_we_report_no_error(self):
        """The caveat, demonstrated on a second real file — and a subtler case.

        The official validator marks this file Invalid ('signingCredential.
        untrusted': the certificate isn't on its trust list), even though every
        signature and hash inside it actually verifies. We report it with no
        error at all, because we check neither signatures nor trust lists. A
        reader of our output cannot tell trusted from untrusted, nor valid from
        tampered — which is exactly what UNVERIFIED_CAVEAT warns about.
        """
        self.assertEqual(self.reference["validation_state"], "Invalid")
        self.assertIn(
            "signingCredential.untrusted",
            {s["code"] for s in self.reference["validation_status"]},
        )
        self.assertTrue(self.mine["found"])
        self.assertIsNone(self.mine["error"])


@unittest.skipUnless(_has("adobe-20220124-CA.jpg"), "sample file not present")
class TestSignedFileDetail(unittest.TestCase):
    """Detailed structural assertions against one known-good signed file."""

    def setUp(self):
        self.result = read_c2pa(_read("adobe-20220124-CA.jpg"))

    def _find(self, node, label):
        for c in node["children"]:
            if c.get("label") == label:
                return c
        self.fail(f"label {label!r} not found among {[c.get('label') for c in node['children']]}")

    def test_top_level_is_c2pa_manifest_store(self):
        self.assertEqual(self.result["manifest"]["label"], "c2pa")
        self.assertEqual(self.result["manifest"]["uuid_meaning"], "C2PA Manifest Store")

    def test_known_assertions_present_and_decoded(self):
        manifest_node = self.result["manifest"]["children"][0]
        store = self._find(manifest_node, "c2pa.assertions")
        labels = {c["label"] for c in store["children"]}
        self.assertIn("c2pa.actions", labels)
        self.assertIn("stds.schema-org.CreativeWork", labels)
        self.assertIn("c2pa.ingredient", labels)
        self.assertIn("c2pa.hash.data", labels)

        creative_work = self._find(store, "stds.schema-org.CreativeWork")
        content = creative_work["children"][0]["content"]
        self.assertEqual(content["decoded_as"], "json")
        self.assertEqual(content["value"]["@type"], "CreativeWork")

    def test_thumbnail_media_type_decoded(self):
        manifest_node = self.result["manifest"]["children"][0]
        store = self._find(manifest_node, "c2pa.assertions")
        thumb = self._find(store, "c2pa.thumbnail.claim.jpeg")
        media_box = next(c for c in thumb["children"] if c["type"] == "bfdb")
        self.assertEqual(media_box["content"]["decoded_as"], "media-type")
        self.assertEqual(media_box["content"]["value"], "image/jpeg")

    def test_signature_box_decodes_as_cose_with_cert_chain(self):
        # The claim signature is itself CBOR (COSE_Sign1): [protected header,
        # unprotected header map, payload, signature]. The unprotected header
        # carries the real X.509 certificate chain in 'x5chain' — this tool
        # never verifies it, but does surface it for independent inspection.
        manifest_node = self.result["manifest"]["children"][0]
        sig = self._find(manifest_node, "c2pa.signature")
        content = sig["children"][0]["content"]
        self.assertEqual(content["decoded_as"], "cbor")
        self.assertIn("x5chain", content["value"][1])

    def test_summary_text_mentions_caveat_and_real_content(self):
        text = to_summary_text(self.result)
        self.assertIn("NOT cryptographically verified", text)
        self.assertIn("c2pa-rs", text)
        self.assertIn("c2pa.actions", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
