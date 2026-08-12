"""Run: python3 -m unittest test_premis -v

Structural checks against the rules read out of the official PREMIS 3.0 schema
(premis.xsd, Library of Congress): required elements, their order within each
complex type, cardinality, the mandatory version attribute, and the namespace.

A full XSD validation would need a third-party validator, which this project
deliberately doesn't have. Instead these assert the schema's actual constraints
element by element, and the README explains how to validate externally.
"""

import struct
import unittest
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone

from unwatermark.c2pa_reader import UNVERIFIED_CAVEAT, read_c2pa
from unwatermark.premis import PREMIS_NS, UW_NS, detect_media_type, to_premis_xml

P = f"{{{PREMIS_NS}}}"
U = f"{{{UW_NS}}}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}"

FIXED_TIME = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)

_PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _png_chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def _plain_png():
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return _PNG_SIG + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IEND", b"")


def _build(data=None, filename="test.png", **kw):
    data = _plain_png() if data is None else data
    xml = to_premis_xml(data, read_c2pa(data), filename=filename,
                        timestamp=FIXED_TIME, **kw)
    return xml, ET.fromstring(xml)


def _children(el):
    """Local names of an element's children, in document order."""
    return [c.tag.split("}", 1)[-1] for c in el]


class TestDocumentShape(unittest.TestCase):

    def setUp(self):
        self.xml, self.root = _build()

    def test_is_well_formed_and_declares_encoding(self):
        self.assertTrue(self.xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_root_is_premis_in_the_right_namespace(self):
        self.assertEqual(self.root.tag, f"{P}premis")

    def test_version_attribute_is_required_and_is_3_0(self):
        # premis.xsd: <xs:attribute name="version" type="version3" use="required"/>
        # and version3 enumerates exactly "3.0".
        self.assertEqual(self.root.get("version"), "3.0")

    def test_top_level_order_is_object_then_event_then_agent(self):
        # premisComplexType sequence: object+, event*, agent*, rights*
        order = _children(self.root)
        self.assertEqual(order[0], "object")
        self.assertEqual(order.count("agent"), 1)
        self.assertEqual(order[-1], "agent")
        first_event = order.index("event")
        self.assertLess(order.index("object"), first_event)
        self.assertLess(first_event, order.index("agent"))

    def test_schema_location_points_at_the_official_xsd(self):
        self.assertIn("loc.gov/standards/premis/v3/premis.xsd",
                      self.root.get(f"{XSI}schemaLocation"))


class TestObjectEntity(unittest.TestCase):

    def setUp(self):
        _, self.root = _build()
        self.obj = self.root.find(f"{P}object")

    def test_object_declares_the_file_subtype(self):
        # objectComplexType is abstract; xsi:type selects file/bitstream/etc.
        self.assertEqual(self.obj.get(f"{XSI}type"), "premis:file")

    def test_element_order_matches_the_file_type_sequence(self):
        # file: objectIdentifier+, ..., objectCharacteristics+, originalName?,
        # ..., linkingEventIdentifier*
        order = _children(self.obj)
        self.assertEqual(order[0], "objectIdentifier")
        self.assertLess(order.index("objectCharacteristics"), order.index("originalName"))
        self.assertLess(order.index("originalName"), order.index("linkingEventIdentifier"))

    def test_object_identifier_has_both_required_children_in_order(self):
        ident = self.obj.find(f"{P}objectIdentifier")
        self.assertEqual(_children(ident),
                         ["objectIdentifierType", "objectIdentifierValue"])

    def test_object_characteristics_order_and_required_format(self):
        # objectCharacteristics: compositionLevel?, fixity*, size?, format+, ...
        chars = self.obj.find(f"{P}objectCharacteristics")
        order = _children(chars)
        self.assertEqual(order, ["fixity", "size", "format",
                                 "objectCharacteristicsExtension"])

    def test_fixity_records_a_real_sha256(self):
        import hashlib
        data = _plain_png()
        fixity = self.obj.find(f"{P}objectCharacteristics/{P}fixity")
        self.assertEqual(_children(fixity), ["messageDigestAlgorithm", "messageDigest",
                                             "messageDigestOriginator"])
        self.assertEqual(fixity.findtext(f"{P}messageDigestAlgorithm"), "SHA-256")
        self.assertEqual(fixity.findtext(f"{P}messageDigest"),
                         hashlib.sha256(data).hexdigest())

    def test_size_is_the_real_byte_count(self):
        size = self.obj.findtext(f"{P}objectCharacteristics/{P}size")
        self.assertEqual(int(size), len(_plain_png()))

    def test_format_uses_formatDesignation_with_a_media_type(self):
        fmt = self.obj.find(f"{P}objectCharacteristics/{P}format")
        designation = fmt.find(f"{P}formatDesignation")
        self.assertIsNotNone(designation)
        self.assertEqual(designation.findtext(f"{P}formatName"), "image/png")

    def test_links_to_both_events(self):
        values = [e.findtext(f"{P}linkingEventIdentifierValue")
                  for e in self.obj.findall(f"{P}linkingEventIdentifier")]
        self.assertEqual(values, ["test.png-digest", "test.png-extraction"])


class TestEventEntities(unittest.TestCase):

    def setUp(self):
        _, self.root = _build()
        self.events = self.root.findall(f"{P}event")

    def test_exactly_two_events_are_emitted(self):
        self.assertEqual(len(self.events), 2)

    def test_event_element_order_matches_the_schema_sequence(self):
        # event: eventIdentifier, eventType, eventDateTime, then optional parts.
        for event in self.events:
            order = _children(event)
            self.assertEqual(order[:3], ["eventIdentifier", "eventType", "eventDateTime"])
            self.assertEqual(order[3:], ["eventDetailInformation", "eventOutcomeInformation",
                                         "linkingAgentIdentifier", "linkingObjectIdentifier"])

    def test_event_types_use_the_loc_controlled_vocabulary(self):
        types = {e.findtext(f"{P}eventType") for e in self.events}
        self.assertEqual(types, {"message digest calculation", "metadata extraction"})
        for event in self.events:
            et = event.find(f"{P}eventType")
            self.assertEqual(et.get("authorityURI"),
                             "http://id.loc.gov/vocabulary/preservation/eventType")
            self.assertTrue(et.get("valueURI", "").startswith(
                "http://id.loc.gov/vocabulary/preservation/eventType/"))

    def test_event_datetime_is_iso8601(self):
        for event in self.events:
            self.assertEqual(event.findtext(f"{P}eventDateTime"), "2026-08-12T12:00:00+00:00")

    def test_outcome_information_order(self):
        for event in self.events:
            info = event.find(f"{P}eventOutcomeInformation")
            self.assertEqual(_children(info), ["eventOutcome", "eventOutcomeDetail"])

    def test_every_event_links_to_the_agent_and_the_object(self):
        for event in self.events:
            self.assertEqual(
                event.findtext(f"{P}linkingAgentIdentifier/{P}linkingAgentIdentifierValue"),
                "unwatermark-1")
            self.assertEqual(
                event.findtext(f"{P}linkingObjectIdentifier/{P}linkingObjectIdentifierValue"),
                "test.png")

    def test_extraction_outcome_carries_the_unverified_caveat(self):
        extraction = [e for e in self.events
                      if e.findtext(f"{P}eventType") == "metadata extraction"][0]
        note = extraction.findtext(
            f"{P}eventOutcomeInformation/{P}eventOutcomeDetail/{P}eventOutcomeDetailNote")
        self.assertIn("NOT cryptographically verified", note)


class TestAgentEntity(unittest.TestCase):

    def setUp(self):
        _, self.root = _build()
        self.agent = self.root.find(f"{P}agent")

    def test_agent_order_matches_schema_sequence(self):
        # agent: agentIdentifier+, agentName*, agentType?, agentVersion?, agentNote*
        self.assertEqual(_children(self.agent),
                         ["agentIdentifier", "agentName", "agentType",
                          "agentVersion", "agentNote"])

    def test_agent_is_identified_as_software(self):
        self.assertEqual(self.agent.findtext(f"{P}agentType"), "software")

    def test_agent_note_states_the_verification_boundary(self):
        self.assertIn("without verifying signatures",
                      self.agent.findtext(f"{P}agentNote"))


class TestC2paExtension(unittest.TestCase):
    """The C2PA payload lives in objectCharacteristicsExtension, which PREMIS
    defines as xs:any — so it must be well-formed and namespaced."""

    def test_extension_content_is_in_our_own_namespace(self):
        _, root = _build()
        ext = root.find(f"{P}object/{P}objectCharacteristics/"
                        f"{P}objectCharacteristicsExtension")
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0].tag, f"{U}c2pa")

    def test_extension_is_marked_unverified_and_carries_the_caveat(self):
        _, root = _build()
        c2pa = root.find(f".//{U}c2pa")
        self.assertEqual(c2pa.get("verified"), "false")
        self.assertEqual(c2pa.findtext(f"{U}caveat"), UNVERIFIED_CAVEAT)

    def test_file_without_manifest_is_marked_as_such(self):
        _, root = _build()
        self.assertEqual(root.find(f".//{U}c2pa").get("embeddedManifest"), "false")


class TestClaimsAreNotPromotedToEvents(unittest.TestCase):
    """The central design decision, pinned by tests.

    A C2PA assertion is an unverified third-party claim. Emitting it as a
    PREMIS Event would turn "the file says this happened" into "the repository
    records that this happened". Only the two things this tool actually did
    may appear as Events.
    """

    SAMPLE = "samples/ChatGPT_Image.png"

    def setUp(self):
        import os
        path = os.path.join(os.path.dirname(__file__), self.SAMPLE)
        if not os.path.exists(path):
            self.skipTest("signed sample not present")
        with open(path, "rb") as f:
            self.data = f.read()
        self.xml, self.root = _build(self.data, filename="ChatGPT_Image.png")

    def test_still_only_two_events_despite_a_rich_manifest(self):
        events = self.root.findall(f"{P}event")
        self.assertEqual(len(events), 2)

    def test_no_event_claims_the_image_was_created(self):
        # The manifest asserts c2pa.created by GPT-4o; that must not become a
        # PREMIS "creation" event.
        types = {e.findtext(f"{P}eventType") for e in self.root.findall(f"{P}event")}
        self.assertNotIn("creation", types)
        self.assertNotIn("capture", types)

    def test_the_claim_is_still_preserved_verbatim_in_the_extension(self):
        # Not emitting it as an Event must not mean losing it.
        self.assertIn("trainedAlgorithmicMedia", self.xml)
        self.assertIn("GPT-4o", self.xml)

    def test_manifest_structure_is_navigable_by_xpath(self):
        boxes = self.root.findall(f".//{U}box")
        labels = {b.get("label") for b in boxes}
        self.assertIn("c2pa", labels)
        self.assertIn("c2pa.assertions", labels)


class TestOutcomeVariants(unittest.TestCase):

    def test_external_manifest_is_recorded_in_the_outcome_note(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "samples",
                            "libpng-test_with_url.png")
        if not os.path.exists(path):
            self.skipTest("sample not present")
        with open(path, "rb") as f:
            data = f.read()
        xml, root = _build(data, filename="libpng-test_with_url.png")
        extraction = [e for e in root.findall(f"{P}event")
                      if e.findtext(f"{P}eventType") == "metadata extraction"][0]
        note = extraction.findtext(
            f"{P}eventOutcomeInformation/{P}eventOutcomeDetail/{P}eventOutcomeDetailNote")
        self.assertIn("external manifest", note)
        self.assertIn("not retrieved", note)
        self.assertIn(".c2pa", xml)

    def test_jpeg_media_type_detected(self):
        self.assertEqual(detect_media_type(b"\xff\xd8\xff\xe0rest"), "image/jpeg")

    def test_unknown_media_type_falls_back(self):
        self.assertEqual(detect_media_type(b"unknown bytes"), "application/octet-stream")


if __name__ == "__main__":
    unittest.main(verbosity=2)
