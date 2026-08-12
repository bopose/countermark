"""Write a BagIt package (RFC 8493) — stdlib only.

BagIt is the usual way to hand a set of files to another institution: a
directory holding the payload under data/, plus manifests of checksums and a
little metadata. It is a layout convention rather than a binary format, which
makes it a good fit for a zero-dependency tool and a good endpoint for this
one — the bag carries the images together with their provenance sidecars and
PREMIS records, so the whole package can be verified by any BagIt reader.

Only bag *writing* is implemented. Validating someone else's bag is a
different job, and `verify` already covers checking our own records.
"""

import hashlib
import os
import shutil

BAGIT_VERSION = "1.0"
_CHUNK = 1 << 20


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _payload_oxum(bag_dir):
    """RFC 8493 Payload-Oxum: total octets, then a dot, then the file count."""
    total = count = 0
    for root, _dirs, names in os.walk(os.path.join(bag_dir, "data")):
        for name in names:
            total += os.path.getsize(os.path.join(root, name))
            count += 1
    return f"{total}.{count}"


def make_bag(bag_dir, payload, *, bag_info=None, timestamp=None):
    """Create a BagIt bag at `bag_dir`.

    `payload` maps a path relative to data/ -> either an existing file path to
    copy, or bytes/str content to write. Returns the bag directory.

    Refuses to write into an existing non-empty directory: silently merging
    into someone else's bag would produce a package whose manifest doesn't
    describe its contents.
    """
    if os.path.exists(bag_dir) and os.listdir(bag_dir):
        raise ValueError(f"refusing to bag into non-empty directory: {bag_dir}")

    data_dir = os.path.join(bag_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    for rel, source in payload.items():
        dest = os.path.join(data_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if isinstance(source, bytes):
            with open(dest, "wb") as f:
                f.write(source)
        elif isinstance(source, str) and os.path.exists(source):
            shutil.copy2(source, dest)
        elif isinstance(source, str):
            _write(dest, source)
        else:
            raise ValueError(f"unsupported payload entry for {rel!r}")

    # bagit.txt — the declaration that makes this a bag.
    _write(os.path.join(bag_dir, "bagit.txt"),
           f"BagIt-Version: {BAGIT_VERSION}\nTag-File-Character-Encoding: UTF-8\n")

    # manifest-sha256.txt — checksums of everything under data/.
    lines = []
    for root, _dirs, names in os.walk(data_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, bag_dir).replace(os.sep, "/")
            lines.append(f"{_sha256(full)}  {rel}\n")
    lines.sort(key=lambda line: line.split("  ", 1)[1])
    _write(os.path.join(bag_dir, "manifest-sha256.txt"), "".join(lines))

    # bag-info.txt — human-readable metadata about the bag itself.
    info = {
        "Bag-Software-Agent": "unwatermark",
        "Payload-Oxum": _payload_oxum(bag_dir),
    }
    if timestamp is not None:
        info["Bagging-Date"] = timestamp.strftime("%Y-%m-%d")
    info.update(bag_info or {})
    _write(os.path.join(bag_dir, "bag-info.txt"),
           "".join(f"{k}: {v}\n" for k, v in info.items()))

    # tagmanifest-sha256.txt — checksums of the tag files themselves.
    tag_lines = []
    for name in ("bagit.txt", "bag-info.txt", "manifest-sha256.txt"):
        full = os.path.join(bag_dir, name)
        tag_lines.append(f"{_sha256(full)}  {name}\n")
    _write(os.path.join(bag_dir, "tagmanifest-sha256.txt"), "".join(tag_lines))

    return bag_dir
