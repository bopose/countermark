"""Command-line interface — the batch counterpart to the web UI.

    python3 -m countermark <command> [files...]

The web UI handles one pasted document at a time, which is the wrong shape for
an archive: nobody feeds ten thousand files through a browser form. Every
command here accepts multiple files and directories, and `c2pa --sidecar-dir`
writes a durable provenance record next to every image in a collection — the
workflow this project was originally motivated by.

Standard library only, like everything else here.
"""

import argparse
import json
import os
import sys
from datetime import datetime

from .bagit import make_bag
from .c2pa_reader import read_c2pa, to_sidecar, to_summary_text
from .clean import clean
from .documents import DOCUMENT_SUFFIXES, extract_document_text, sniff_document_format
from .fixity import MISMATCH, NO_RECORD, OK, summarise, verify_against_records
from .premis import to_premis_xml
from .provenance import diff_drafts
from .scan import analyze

TEXT_SUFFIXES = (".txt", ".md", ".markdown") + DOCUMENT_SUFFIXES
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


class UsageError(Exception):
    """A problem with what the user asked for, not with a file."""


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------

def collect_files(paths, suffixes, recursive):
    """Expand paths into a sorted file list, filtering directories by suffix.

    Explicitly-named files are always included, whatever their extension —
    if a user names a file, second-guessing its suffix is unhelpful. The
    filter applies only when walking a directory.
    """
    out = []
    for path in paths:
        if os.path.isdir(path):
            if not recursive:
                raise UsageError(
                    f"{path} is a directory; pass --recursive to descend into it"
                )
            for root, _dirs, names in os.walk(path):
                for name in sorted(names):
                    if name.lower().endswith(suffixes):
                        out.append(os.path.join(root, name))
        elif os.path.exists(path):
            out.append(path)
        else:
            raise UsageError(f"no such file or directory: {path}")
    return sorted(dict.fromkeys(out))


def read_text(path):
    """Read a text document, transparently extracting .docx and .odt.

    The format is sniffed from the bytes rather than the extension, so a
    document saved under the wrong suffix still reads correctly.
    """
    with open(path, "rb") as f:
        raw = f.read()
    if sniff_document_format(raw):
        return extract_document_text(raw)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"not valid UTF-8 text ({exc}); supported documents are .docx and .odt"
        )


# --------------------------------------------------------------------------
# Text renderers
# --------------------------------------------------------------------------

def render_scan(path, report):
    s = report["summary"]
    lines = [f"{path}"]
    if not s["flag_count"] and not s["homoglyph_count"]:
        lines.append(f"  No hidden or disguised characters in {s['total_chars']} characters.")
        lines.append("  (This does not rule out a statistical watermark — see README.)")
        return "\n".join(lines)

    if s["flag_count"]:
        kinds = ", ".join(f"{v} {k}" for k, v in sorted(s["counts"].items()))
        lines.append(f"  {s['flag_count']} hidden character(s): {kinds}")
        for f in report["findings"]:
            lines.append(f"    offset {f['offset']:>7}  {f['codepoint']:<8} {f['name']}")
    if s["homoglyph_count"]:
        lines.append(f"  {s['homoglyph_count']} look-alike word(s):")
        for h in report["homoglyphs"]:
            looks = f" looks like {h['looks_like']!r}" if h.get("looks_like") else ""
            lines.append(
                f"    offset {h['offset']:>7}  [{h['confidence']}] {h['token']!r}{looks}"
                f"  ({'+'.join(h['scripts'])})"
            )
    return "\n".join(lines)


def render_clean_summary(path, result):
    s = result["summary"]
    bits = []
    if s["removed"]:
        bits.append(f"{s['removed']} removed")
    if s["replaced"]:
        bits.append(f"{s['replaced']} normalised")
    if s["homoglyphs_normalized"]:
        bits.append(f"{s['homoglyphs_normalized']} homoglyphs fixed")
    return f"{path}: {', '.join(bits) if bits else 'nothing to clean'}"


def render_diff(result):
    s = result["stats"]
    lines = [
        f"{s['percent_unchanged']}% of the final text is word-for-word from the original "
        f"({s['unchanged_words']} of {s['revised_words']} words).",
        f"Counting minor spelling/grammar fixes as your own wording, "
        f"{s['percent_your_wording']}% is yours.",
        f"Changes: {s['inserted']} added, {s['deleted']} removed, "
        f"{s['minor_fixes']} minor fixes, {s['rewritten']} rewritten.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_scan(args):
    files = collect_files(args.files, TEXT_SUFFIXES, args.recursive)
    results, failures = {}, 0
    for path in files:
        try:
            report = analyze(read_text(path))
        except (OSError, ValueError) as exc:
            print(f"{path}: ERROR: {exc}", file=sys.stderr)
            failures += 1
            continue
        results[path] = report
        if args.json:
            continue
        flagged = report["summary"]["flag_count"] or report["summary"]["homoglyph_count"]
        if flagged or not args.only_findings:
            print(render_scan(path, report))
    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 1 if failures else 0


def cmd_clean(args):
    files = collect_files(args.files, TEXT_SUFFIXES, args.recursive)
    if len(files) > 1 and not (args.output_dir or args.in_place):
        raise UsageError(
            "refusing to write several cleaned files to stdout; "
            "pass --output-dir DIR or --in-place"
        )
    if args.in_place and args.output_dir:
        raise UsageError("--in-place and --output-dir are mutually exclusive")

    failures = 0
    for path in files:
        try:
            text = read_text(path)
        except (OSError, ValueError) as exc:
            print(f"{path}: ERROR: {exc}", file=sys.stderr)
            failures += 1
            continue
        result = clean(text, normalize_homoglyphs=args.fix_homoglyphs)

        if args.in_place:
            if path.lower().endswith(DOCUMENT_SUFFIXES):
                # Writing cleaned text back into a .docx/.odt would mean
                # rebuilding the package; we only read those.
                suffix = os.path.splitext(path)[1]
                print(f"{path}: ERROR: cannot rewrite {suffix} in place (read-only format)",
                      file=sys.stderr)
                failures += 1
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(result["cleaned"])
            print(render_clean_summary(path, result), file=sys.stderr)
        elif args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(path))[0] + ".txt"
            dest = os.path.join(args.output_dir, base)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(result["cleaned"])
            print(f"{render_clean_summary(path, result)} -> {dest}", file=sys.stderr)
        else:
            # Single file, no destination: the cleaned text is the output, so
            # it goes to stdout and the summary to stderr — pipe-friendly.
            print(render_clean_summary(path, result), file=sys.stderr)
            sys.stdout.write(result["cleaned"])
    return 1 if failures else 0


def cmd_c2pa(args):
    files = collect_files(args.files, IMAGE_SUFFIXES, args.recursive)
    results, failures = {}, 0
    for path in files:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            print(f"{path}: ERROR: {exc}", file=sys.stderr)
            failures += 1
            continue
        result = read_c2pa(data)
        results[path] = result

        if args.sidecar_dir:
            os.makedirs(args.sidecar_dir, exist_ok=True)
            base = os.path.basename(path) + ".c2pa-sidecar.json"
            dest = os.path.join(args.sidecar_dir, base)
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(to_sidecar(result, source_filename=os.path.basename(path)),
                          f, indent=2, ensure_ascii=False)

        if args.premis_dir:
            os.makedirs(args.premis_dir, exist_ok=True)
            name = os.path.basename(path)
            dest = os.path.join(args.premis_dir, name + ".premis.xml")
            with open(dest, "w", encoding="utf-8") as f:
                f.write(to_premis_xml(data, result, filename=name))

        if args.json:
            continue
        if len(files) == 1 and not args.summary:
            print(to_summary_text(result))
        else:
            if result["error"]:
                status = f"ERROR: {result['error']}"
            elif result["found"]:
                status = "manifest found (NOT verified)"
            elif result["external_manifest_url"]:
                status = f"external manifest -> {result['external_manifest_url']}"
            else:
                status = "no manifest"
            if result["found"] or not args.only_findings:
                print(f"{path}: {status}")
    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 1 if failures else 0


def cmd_verify(args):
    """Re-check files against the fixity values in their PREMIS records."""
    if not os.path.isdir(args.premis_dir):
        raise UsageError(f"not a directory: {args.premis_dir}")
    records = [os.path.join(args.premis_dir, n)
               for n in sorted(os.listdir(args.premis_dir))
               if n.lower().endswith(".xml")]
    if not records:
        raise UsageError(f"no PREMIS records (*.xml) found in {args.premis_dir}")

    files = collect_files(args.files, IMAGE_SUFFIXES, args.recursive)
    results = verify_against_records(records, files)

    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        for r in results:
            if r["status"] == OK and args.only_problems:
                continue
            label = r["filename"] or r["record"]
            if r["status"] == OK:
                print(f"OK        {label}")
            else:
                print(f"{r['status'].upper():<9} {label}: {r.get('detail', '')}")
                if r["status"] == MISMATCH:
                    print(f"          expected {r['expected']}")
                    print(f"          actual   {r['actual']}")
        counts = summarise(results)
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        print(f"\n{len(results)} checked: " + ", ".join(parts))

    # Anything other than a clean match is worth a non-zero exit for scripting.
    bad = [r for r in results if r["status"] != OK]
    return 1 if bad else 0


def cmd_bag(args):
    """Package images with their sidecars and PREMIS records into a BagIt bag."""
    files = collect_files(args.files, IMAGE_SUFFIXES, args.recursive)
    if not files:
        raise UsageError("no image files to bag")

    payload = {}
    for path in files:
        name = os.path.basename(path)
        if name in payload:
            raise UsageError(
                f"two files share the basename {name!r}; bag them separately")
        with open(path, "rb") as f:
            data = f.read()
        result = read_c2pa(data)
        payload[name] = path
        payload[name + ".c2pa-sidecar.json"] = json.dumps(
            to_sidecar(result, source_filename=name), indent=2, ensure_ascii=False)
        payload[name + ".premis.xml"] = to_premis_xml(data, result, filename=name)

    make_bag(args.output, payload,
             bag_info={"External-Description":
                       "Images with C2PA provenance sidecars and PREMIS records "
                       "(provenance read but NOT cryptographically verified)."},
             timestamp=datetime.now())
    print(f"Bagged {len(files)} image(s) with sidecars and PREMIS records "
          f"into {args.output}", file=sys.stderr)
    return 0


def cmd_diff(args):
    original = read_text(args.original)
    revised = read_text(args.revised)
    result = diff_drafts(original, revised)
    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(render_diff(result))
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="countermark",
        description="Inspect text for hidden characters and images for C2PA "
                    "provenance. Everything runs locally; no network requests.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    def add_common(p, suffix_hint):
        p.add_argument("files", nargs="+", metavar="FILE",
                       help=f"files or directories ({suffix_hint})")
        p.add_argument("-r", "--recursive", action="store_true",
                       help="descend into directories")
        p.add_argument("--json", action="store_true",
                       help="emit machine-readable JSON instead of text")

    p_scan = subs.add_parser("scan", help="find hidden/disguised characters in text")
    add_common(p_scan, ".txt, .md, .docx, .odt")
    p_scan.add_argument("--only-findings", action="store_true",
                        help="list only files that have something to report")
    p_scan.set_defaults(func=cmd_scan)

    p_clean = subs.add_parser("clean", help="remove hidden characters from text")
    add_common(p_clean, ".txt, .md, .docx, .odt")
    p_clean.add_argument("-o", "--output-dir", metavar="DIR",
                         help="write cleaned copies here as .txt")
    p_clean.add_argument("--in-place", action="store_true",
                         help="overwrite the input files (not available for .docx/.odt)")
    p_clean.add_argument("--fix-homoglyphs", action="store_true",
                         help="also latinise high-confidence disguised words")
    p_clean.set_defaults(func=cmd_clean)

    p_c2pa = subs.add_parser("c2pa", help="read C2PA provenance from images")
    add_common(p_c2pa, ".png, .jpg, .jpeg")
    p_c2pa.add_argument("--sidecar-dir", metavar="DIR",
                        help="write a durable JSON provenance record per image")
    p_c2pa.add_argument("--premis-dir", metavar="DIR",
                        help="write a PREMIS 3.0 preservation-metadata record per image")
    p_c2pa.add_argument("--summary", action="store_true",
                        help="one line per file even for a single file")
    p_c2pa.add_argument("--only-findings", action="store_true",
                        help="list only images that carry a manifest")
    p_c2pa.set_defaults(func=cmd_c2pa)

    p_verify = subs.add_parser(
        "verify", help="re-check files against the fixity in their PREMIS records")
    add_common(p_verify, ".png, .jpg, .jpeg")
    p_verify.add_argument("--premis-dir", metavar="DIR", required=True,
                          help="directory of PREMIS records written by `c2pa --premis-dir`")
    p_verify.add_argument("--only-problems", action="store_true",
                          help="list only files that failed to verify")
    p_verify.set_defaults(func=cmd_verify)

    p_bag = subs.add_parser(
        "bag", help="package images + sidecars + PREMIS into a BagIt bag")
    add_common(p_bag, ".png, .jpg, .jpeg")
    p_bag.add_argument("-o", "--output", metavar="DIR", required=True,
                       help="bag directory to create (must not already have contents)")
    p_bag.set_defaults(func=cmd_bag)

    p_diff = subs.add_parser("diff", help="compare an original draft with a final version")
    p_diff.add_argument("original", metavar="ORIGINAL")
    p_diff.add_argument("revised", metavar="REVISED")
    p_diff.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON instead of text")
    p_diff.set_defaults(func=cmd_diff)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0  # e.g. piped into `head`


if __name__ == "__main__":
    sys.exit(main())
