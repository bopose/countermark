"""Re-check files against the fixity values recorded in PREMIS records.

Recording a checksum is only half of a preservation workflow; the other half
is coming back later and confirming it still matches. This reads the SHA-256
values that `premis.py` wrote and re-hashes the files, which is what turns
those records from documentation into something that actually detects bit rot,
a truncated copy, or a botched migration.
"""

import hashlib
import os
import xml.etree.ElementTree as ET

from .premis import PREMIS_NS

P = f"{{{PREMIS_NS}}}"

# Status values, ordered from best to worst for reporting.
OK = "ok"
MISMATCH = "mismatch"
FILE_MISSING = "file-missing"
UNREADABLE_RECORD = "unreadable-record"
NO_RECORD = "no-record"


def file_digest(path, algorithm="sha256", chunk_size=1 << 20):
    """Stream a file through a hash so large files don't have to fit in memory."""
    digest = hashlib.new(algorithm.replace("-", "").lower())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_premis_fixity(path):
    """Return {filename, algorithm, digest} from a PREMIS record.

    Raises ValueError if the file isn't a PREMIS record carrying a fixity.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"not well-formed XML: {exc}")
    if root.tag != f"{P}premis":
        raise ValueError("not a PREMIS document")

    obj = root.find(f"{P}object")
    if obj is None:
        raise ValueError("PREMIS record has no object")

    # originalName is the filename as it was read; fall back to the identifier.
    filename = obj.findtext(f"{P}originalName") or obj.findtext(
        f"{P}objectIdentifier/{P}objectIdentifierValue")
    if not filename:
        raise ValueError("PREMIS object has no originalName or identifier")

    fixity = obj.find(f"{P}objectCharacteristics/{P}fixity")
    if fixity is None:
        raise ValueError("PREMIS object records no fixity")
    algorithm = fixity.findtext(f"{P}messageDigestAlgorithm")
    digest = fixity.findtext(f"{P}messageDigest")
    if not algorithm or not digest:
        raise ValueError("PREMIS fixity is incomplete")
    return {"filename": filename, "algorithm": algorithm, "digest": digest.strip().lower()}


def verify_against_records(record_paths, file_paths):
    """Check each PREMIS record against the file it describes.

    Files are matched by basename, which is how the records identify them.
    Returns a list of result dicts, plus any files that had no record at all —
    silence about an unrecorded file would be its own kind of dishonesty.
    """
    by_name = {}
    for path in file_paths:
        by_name.setdefault(os.path.basename(path), path)

    results = []
    accounted = set()
    for record in sorted(record_paths):
        try:
            recorded = read_premis_fixity(record)
        except ValueError as exc:
            results.append({"status": UNREADABLE_RECORD, "record": record,
                            "filename": None, "detail": str(exc)})
            continue

        filename = recorded["filename"]
        path = by_name.get(filename)
        if path is None:
            results.append({"status": FILE_MISSING, "record": record,
                            "filename": filename,
                            "detail": "no file with this name was given"})
            continue

        accounted.add(path)
        try:
            actual = file_digest(path, recorded["algorithm"])
        except (OSError, ValueError) as exc:
            results.append({"status": UNREADABLE_RECORD, "record": record,
                            "filename": filename, "detail": f"could not hash: {exc}"})
            continue

        if actual == recorded["digest"]:
            results.append({"status": OK, "record": record, "filename": filename,
                            "path": path, "algorithm": recorded["algorithm"],
                            "digest": actual})
        else:
            results.append({"status": MISMATCH, "record": record, "filename": filename,
                            "path": path, "algorithm": recorded["algorithm"],
                            "expected": recorded["digest"], "actual": actual,
                            "detail": "file does not match its recorded fixity"})

    for path in sorted(set(file_paths) - accounted):
        results.append({"status": NO_RECORD, "record": None,
                        "filename": os.path.basename(path), "path": path,
                        "detail": "no PREMIS record describes this file"})
    return results


def summarise(results):
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts
