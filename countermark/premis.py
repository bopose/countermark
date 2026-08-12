"""Export C2PA findings as PREMIS 3.0 preservation metadata.

This closes the loop on the problem that started this project: C2PA metadata
is embedded *in* the file, so it dies on the first screenshot, re-encode, or
format migration. Provenance only survives the long term if it is recorded in
preservation metadata that travels alongside the object — which is what PREMIS
is for.

Structure produced (element order and cardinality follow premis.xsd v3.0):

    premis (version="3.0")
      object xsi:type="file"   — the image being preserved
        objectIdentifier       — caller-supplied or the filename
        objectCharacteristics
          fixity               — SHA-256, so the record supports later fixity checks
          size
          format               — media type
          objectCharacteristicsExtension — the C2PA manifest, verbatim
        originalName
        linkingEventIdentifier — back-references to the events below
      event                    — "message digest calculation" (we computed the hash)
      event                    — "metadata extraction" (we read the manifest)
      agent                    — this tool

THE IMPORTANT DESIGN DECISION: the claims *inside* a C2PA manifest (that an
image was created by GPT-4o, edited in Photoshop, and so on) are deliberately
NOT emitted as PREMIS Events.

A PREMIS Event asserts something the repository did or witnessed. A C2PA
assertion is an unverified claim made by a third party, which this tool cannot
check (it verifies no signatures — see c2pa_reader). Promoting those claims to
first-class PREMIS Events would launder "the file says this happened" into
"the repository records that this happened", which is precisely the
overclaiming this project exists to avoid. So the only Events emitted are the
two things this tool genuinely did: it hashed the file and it read the
metadata. The claims are preserved verbatim inside
objectCharacteristicsExtension, carrying their own "not verified" caveat.
"""

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .c2pa_reader import UNVERIFIED_CAVEAT

PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
PREMIS_SCHEMA_LOCATION = (
    "http://www.loc.gov/premis/v3 http://www.loc.gov/standards/premis/v3/premis.xsd"
)

# Our own namespace for the extension payload. PREMIS allows any well-formed
# XML inside an extension (xs:any namespace="##any"), but it must be namespaced
# so a repository can tell whose extension it is.
CM_NS = "urn:countermark:c2pa:1"

_EVENT_TYPE_AUTHORITY = "http://id.loc.gov/vocabulary/preservation/eventType"
_EVENT_OUTCOME_AUTHORITY = "http://id.loc.gov/vocabulary/preservation/eventOutcome"

# Verified against the Library of Congress vocabulary.
_EVENT_TYPES = {
    "message digest calculation": _EVENT_TYPE_AUTHORITY + "/mes",
    "metadata extraction": _EVENT_TYPE_AUTHORITY + "/mee",
}

_AGENT_NAME = "countermark"
_AGENT_NOTE = (
    "Reads C2PA provenance without verifying signatures; see eventOutcomeDetail."
)

_MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg"}

_PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _q(tag):
    return f"{{{PREMIS_NS}}}{tag}"


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, _q(tag), {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = str(text)
    return el


def _controlled(parent, tag, value, authority_uri=None, value_uri=None, authority=None):
    """A stringPlusAuthority element, optionally citing its controlled vocabulary."""
    return _sub(parent, tag, value,
                authority=authority, authorityURI=authority_uri, valueURI=value_uri)


def detect_media_type(data):
    """Best-effort media type from the file's own bytes."""
    if data[:8] == _PNG_SIGNATURE:
        return _MEDIA_TYPES["png"]
    if data[:2] == b"\xff\xd8":
        return _MEDIA_TYPES["jpeg"]
    return "application/octet-stream"


# --------------------------------------------------------------------------
# The C2PA extension payload
# --------------------------------------------------------------------------

def _extension_node(parent, result):
    """Represent the C2PA finding as namespaced XML inside a PREMIS extension.

    The manifest tree is mirrored as nested elements so it stays inspectable
    with XPath, and each decoded assertion's value is carried verbatim as JSON
    text rather than being reshaped into a lossy XML rendering.
    """
    root = ET.SubElement(parent, f"{{{CM_NS}}}c2pa", {
        "verified": "false",
        "embeddedManifest": "true" if result.get("found") else "false",
    })
    ET.SubElement(root, f"{{{CM_NS}}}caveat").text = result.get("caveat", UNVERIFIED_CAVEAT)

    if result.get("error"):
        ET.SubElement(root, f"{{{CM_NS}}}error").text = result["error"]

    if result.get("external_manifest_url"):
        ext = ET.SubElement(root, f"{{{CM_NS}}}externalManifest")
        ET.SubElement(ext, f"{{{CM_NS}}}url").text = result["external_manifest_url"]
        ET.SubElement(ext, f"{{{CM_NS}}}note").text = (
            "This file declares a manifest stored outside itself. It was not "
            "fetched; its contents are therefore not recorded here."
        )

    manifest = result.get("manifest")
    if manifest:
        _manifest_node(root, manifest)
    return root


def _manifest_node(parent, node):
    el = ET.SubElement(parent, f"{{{CM_NS}}}box", {
        k: v for k, v in (
            ("label", node.get("label")),
            ("meaning", node.get("uuid_meaning")),
        ) if v
    })
    for child in node.get("children", []):
        if child.get("kind") == "superbox":
            _manifest_node(el, child)
        else:
            content = child.get("content", {})
            leaf = ET.SubElement(el, f"{{{CM_NS}}}content", {
                k: v for k, v in (
                    ("type", child.get("type")),
                    ("decodedAs", content.get("decoded_as") or "none"),
                ) if v
            })
            if "value" in content:
                leaf.text = json.dumps(content["value"], ensure_ascii=False, sort_keys=True)
            elif content.get("decode_error"):
                leaf.set("decodeError", content["decode_error"])
            elif content.get("byte_length") is not None:
                leaf.set("byteLength", str(content["byte_length"]))
    return el


# --------------------------------------------------------------------------
# Document assembly
# --------------------------------------------------------------------------

def _add_event(parent, *, identifier, event_type, when, detail, outcome, outcome_note,
               agent_id, object_id):
    """Append one premis:event in schema order."""
    event = _sub(parent, "event")
    ident = _sub(event, "eventIdentifier")
    _controlled(ident, "eventIdentifierType", "local")
    _sub(ident, "eventIdentifierValue", identifier)

    _controlled(event, "eventType", event_type,
                authority="eventType", authority_uri=_EVENT_TYPE_AUTHORITY,
                value_uri=_EVENT_TYPES.get(event_type))
    _sub(event, "eventDateTime", when)

    detail_info = _sub(event, "eventDetailInformation")
    _sub(detail_info, "eventDetail", detail)

    outcome_info = _sub(event, "eventOutcomeInformation")
    _controlled(outcome_info, "eventOutcome", outcome,
                authority="eventOutcome", authority_uri=_EVENT_OUTCOME_AUTHORITY)
    outcome_detail = _sub(outcome_info, "eventOutcomeDetail")
    _sub(outcome_detail, "eventOutcomeDetailNote", outcome_note)

    linking_agent = _sub(event, "linkingAgentIdentifier")
    _controlled(linking_agent, "linkingAgentIdentifierType", "local")
    _sub(linking_agent, "linkingAgentIdentifierValue", agent_id)

    linking_object = _sub(event, "linkingObjectIdentifier")
    _controlled(linking_object, "linkingObjectIdentifierType", "filename")
    _sub(linking_object, "linkingObjectIdentifierValue", object_id)
    return event


def build_premis_tree(data, result, *, filename, identifier=None, timestamp=None,
                      tool_version="1"):
    """Build the PREMIS document as an ElementTree Element.

    `data` is the image's bytes (hashed for fixity), `result` is a
    c2pa_reader.read_c2pa() result.
    """
    object_id = identifier or filename
    when = (timestamp or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    agent_id = f"{_AGENT_NAME}-{tool_version}"
    digest = hashlib.sha256(data).hexdigest()

    root = ET.Element(_q("premis"), {
        "version": "3.0",
        f"{{{XSI_NS}}}schemaLocation": PREMIS_SCHEMA_LOCATION,
    })

    # --- object ---------------------------------------------------------
    obj = _sub(root, "object")
    obj.set(f"{{{XSI_NS}}}type", "premis:file")

    ident = _sub(obj, "objectIdentifier")
    _controlled(ident, "objectIdentifierType", "filename")
    _sub(ident, "objectIdentifierValue", object_id)

    characteristics = _sub(obj, "objectCharacteristics")
    fixity = _sub(characteristics, "fixity")
    _controlled(fixity, "messageDigestAlgorithm", "SHA-256")
    _sub(fixity, "messageDigest", digest)
    _controlled(fixity, "messageDigestOriginator", _AGENT_NAME)
    _sub(characteristics, "size", len(data))
    fmt = _sub(characteristics, "format")
    designation = _sub(fmt, "formatDesignation")
    _controlled(designation, "formatName", detect_media_type(data))
    extension = _sub(characteristics, "objectCharacteristicsExtension")
    _extension_node(extension, result)

    _sub(obj, "originalName", filename)

    for suffix in ("digest", "extraction"):
        link = _sub(obj, "linkingEventIdentifier")
        _controlled(link, "linkingEventIdentifierType", "local")
        _sub(link, "linkingEventIdentifierValue", f"{object_id}-{suffix}")

    # --- events (only what this tool actually did) ----------------------
    _add_event(
        root,
        identifier=f"{object_id}-digest",
        event_type="message digest calculation",
        when=when,
        detail=f"SHA-256 message digest calculated by {_AGENT_NAME}.",
        outcome="success",
        outcome_note=f"SHA-256: {digest}",
        agent_id=agent_id,
        object_id=object_id,
    )

    if result.get("error"):
        outcome, note = "failure", f"C2PA data present but could not be parsed: {result['error']}"
    elif result.get("found"):
        note = ("A C2PA manifest was found and decoded; its content is recorded in "
                "objectCharacteristicsExtension. " + UNVERIFIED_CAVEAT)
        outcome = "success"
    elif result.get("external_manifest_url"):
        outcome = "success"
        note = ("No embedded C2PA manifest. The file declares an external manifest at "
                f"{result['external_manifest_url']}, which was not retrieved. "
                + UNVERIFIED_CAVEAT)
    else:
        outcome = "success"
        note = "No C2PA manifest is embedded in this file. " + UNVERIFIED_CAVEAT

    _add_event(
        root,
        identifier=f"{object_id}-extraction",
        event_type="metadata extraction",
        when=when,
        detail=f"C2PA provenance metadata read by {_AGENT_NAME} (signatures not verified).",
        outcome=outcome,
        outcome_note=note,
        agent_id=agent_id,
        object_id=object_id,
    )

    # --- agent ----------------------------------------------------------
    agent = _sub(root, "agent")
    agent_ident = _sub(agent, "agentIdentifier")
    _controlled(agent_ident, "agentIdentifierType", "local")
    _sub(agent_ident, "agentIdentifierValue", agent_id)
    _controlled(agent, "agentName", _AGENT_NAME)
    _controlled(agent, "agentType", "software")
    _sub(agent, "agentVersion", tool_version)
    _sub(agent, "agentNote", _AGENT_NOTE)

    return root


def to_premis_xml(data, result, *, filename, identifier=None, timestamp=None,
                  tool_version="1"):
    """Build the PREMIS record and return it as an indented XML string."""
    ET.register_namespace("premis", PREMIS_NS)
    ET.register_namespace("xsi", XSI_NS)
    ET.register_namespace("cm", CM_NS)
    root = build_premis_tree(data, result, filename=filename, identifier=identifier,
                             timestamp=timestamp, tool_version=tool_version)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    ) + "\n"
