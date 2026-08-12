# unwatermark

A small, local, **zero-dependency** toolkit for AI-text and AI-image
provenance — built around one idea: **show what's verifiably there, never
guess.** No statistical "is this AI?" scoring, no watermark evasion, no
crypto-signature theater. Just honest inspection of what a piece of text or
an image file actually carries, plus a way to build your own positive record
of how you worked.

Built for students (especially non-native speakers, and neurodiverse or
visually-impaired writers) who are unfairly pushed to "prove" how they wrote
something, and for archivists dealing with provenance metadata that doesn't
survive format migration. It favours **honesty over reassurance** throughout.

Runs entirely on your own machine — the server binds to loopback only, makes
no network calls, and uses nothing but the Python 3 standard library. Nothing
to `pip install`, nothing hidden: every line is readable.

## Quick start

```bash
python3 server.py
```

Then open **<http://127.0.0.1:8765>**. Four pages, linked from each other:

| Page | URL | What it's for |
|---|---|---|
| Hidden-character inspector | `/` | Paste text, find and clean hidden/disguised Unicode characters |
| Provenance record builder | `/provenance` | Build your own "here's how I wrote this" record, with a real draft-comparison |
| C2PA manifest reader | `/c2pa` | Read (never verify) the Content Credentials manifest in a PNG or JPEG |
| What detectors can't show | `/detectors` | A printable, sourced summary of the published evidence on AI-detector false positives |

Optional: `python3 server.py --port 9000` to use a different port.

## Command line

The web UI handles one document at a time, which is the wrong shape for an
archive — nobody feeds ten thousand files through a browser form. Every
command below takes multiple files and directories, needs no installation, and
makes no network requests:

```bash
python3 -m unwatermark --help
```

| Command | Does |
|---|---|
| `scan FILE…` | find hidden/disguised characters (`.txt`, `.md`, `.docx`, `.odt`) |
| `clean FILE…` | write a cleaned copy |
| `c2pa FILE…` | read C2PA provenance from images; export JSON sidecars and PREMIS |
| `verify FILE…` | re-check files against the fixity recorded in their PREMIS records |
| `bag FILE…` | package images + sidecars + PREMIS into a BagIt bag |
| `diff ORIGINAL REVISED` | compare an original draft with a final version |

The archivist workflow — walk a collection and drop a durable provenance
record beside every image, as JSON, as PREMIS, or both:

```bash
python3 -m unwatermark c2pa --recursive collection/ --sidecar-dir sidecars/ --premis-dir premis/
```

Later — after a migration, a copy between systems, or just on a schedule —
re-check that nothing has rotted. A single flipped bit is enough to fail:

```bash
python3 -m unwatermark verify --premis-dir premis/ --recursive collection/
```

It exits non-zero if anything mismatches, is missing, or has no record at all,
so it drops straight into a cron job. To hand the whole thing to another
institution, wrap it as a [BagIt](https://datatracker.ietf.org/doc/html/rfc8493)
package — image, sidecar and PREMIS record together, with checksum manifests:

```bash
python3 -m unwatermark bag --recursive collection/ -o transfer-bag/
```

Other useful flags: `--json` on any command for machine-readable output,
`-r/--recursive` to descend into directories, `--only-findings` to list just
the files with something to report, and for `clean`, `-o DIR` or `--in-place`
(it refuses to dump several cleaned files to stdout; `.docx`/`.odt` are
read-only, so they can't be rewritten in place).
`clean` on a single file writes the cleaned text to stdout and its summary to
stderr, so it pipes cleanly.

## The three tools

### 1. Hidden-character inspector

- Finds invisible / non-printing characters: zero-width spaces and joiners,
  directional (bidi) controls, Unicode tag characters, variation selectors,
  soft hyphens, non-standard spaces, and other control/format codepoints.
  Shows each one **in place**, at its exact position, with its official
  Unicode name and why it's flagged.
- Finds **look-alike (homoglyph) words** — letters from another alphabet
  imitating Latin ones — and shows what each word *reads as* ("looks like
  `password`") plus which character stands in for which. Results are tiered
  by confidence: *high* for a word blending real Latin letters with
  look-alikes (`paѕsword` — no ordinary word does this), *low* for a word
  written entirely in another alphabet that reads as Latin (`раура`, flagged
  only at length ≥ 4 and marked as possibly legitimate foreign text), *low*
  for a word that mixes alphabets without cleanly reading as Latin. Ordinary
  multilingual text (`привет`, `Straße`, `café`) is never flagged — it always
  contains a letter with no Latin look-alike.
- **Clean text** produces a cleaned copy: removes invisible/formatting/control
  characters, normalises odd spaces to a plain space and line separators to a
  newline (so words never merge), and shows a full log of every change. This
  is **text hygiene** — for accessibility, for defusing "Trojan Source" bidi
  tricks, for tidy copy-paste — **not** watermark removal; this tool cannot
  see a statistical AI watermark and cleaning will not make AI text
  "undetectable." Homoglyph fixing is opt-in and only ever touches
  high-confidence disguised words, never a possibly-legitimate foreign one.

**What it deliberately does NOT do:** detect statistical "text watermarks"
(the kind AI vendors embed by subtly biasing word choice — only the vendor's
own secret key can confirm those) or guess whether text is AI-generated from
writing style. That guess is exactly what unfairly flags many non-native and
neurodiverse writers, and this tool refuses to make it. Finding nothing here
does not prove text is watermark-free; finding something here does not prove
text is AI-generated.

### 2. Provenance record builder

Helps a writer *show their own process* instead of being asked to prove they
didn't cheat:

- **Draft comparison — the evidence.** Paste your own earlier draft and the
  final version; it computes a word-level diff and reports two figures: how
  much is word-for-word yours, and — counting minor spelling/grammar fixes as
  your own wording — how much is yours overall. Minor fixes are told apart
  from substantive rewrites by character similarity, so a spelling correction
  never reads as a rewrite. Hard to fake, and it directly answers a false
  accusation when you used AI only for grammar, spelling, or dictation.
- **Section labels — the declaration.** Tag each paragraph by how it was made
  (written by me / AI grammar only / dictated / AI-drafted then edited /
  quoted). Self-reported and clearly framed as such.
- **Output.** A printable disclosure statement, plus a machine-readable JSON
  sidecar you can preserve alongside the work — provenance you *add* and
  keep, rather than a fragile signal that dies on the first format migration.

The record states plainly that labels are a declaration and the draft
comparison is the computed evidence. It does not claim to prove authorship.

### 3. C2PA manifest reader (PNG and JPEG)

Reads the "Content Credentials" (C2PA) manifest some image files carry — the
metadata this whole project was originally prompted by, since it's exactly
the kind of provenance signal that doesn't survive screenshots or format
migration unless it's extracted somewhere durable first.

**External manifests are reported too.** A file can carry no embedded
manifest yet still declare, in its XMP, a `dcterms:provenance` URL pointing at
a separate `.c2pa` file. Calling that "no provenance" would be misleading —
there is provenance, it just lives elsewhere — so the reader surfaces the URL
and states plainly that it does not fetch it (that would mean a network
request, which this tool never makes).

**It reads. It does not verify.** C2PA manifests are cryptographically
signed; confirming that signature — proving a manifest is genuine and the
file untampered — needs X.509 certificate handling and public-key
cryptography, which Python's standard library doesn't provide. Adding a
crypto dependency for that would break this tool's zero-dependency,
read-every-line trust model, so this reader stays honest about the boundary
instead: it shows exactly what a manifest *claims* (generator, actions,
assertions, structure, even the embedded certificate chain bytes), and says
plainly, everywhere, that **none of it is verified** — a tampered or entirely
fabricated manifest would look identical here.

Implementation is four small stdlib-only layers, each independently tested:
a CBOR decoder (`unwatermark/cbor_decode.py` — Python has none built in), a
JUMBF box parser (`unwatermark/jumbf.py` — the container format C2PA uses;
its byte layout was verified against real test vectors from
[jumbf-rs](https://github.com/scouten-adobe/jumbf-rs), since the ISO standard
itself is paywalled), a PNG chunk reader (`unwatermark/png_chunks.py`), and a
JPEG APP11-segment reassembler (`unwatermark/jpeg_segments.py` — JPEG splits
large manifests across multiple ~64 KB segments; that header layout isn't in
any free spec either, so it was verified against a real file). Manifests
using either CBOR or the older JSON-based claim encoding are both handled,
since real files may use either. `unwatermark/c2pa_reader.py` ties it all
together and exports a durable JSON sidecar of whatever it found — get the
provenance claim out of the fragile container before a migration or
screenshot loses it.

**Validated against 29 real files, not just hand-built fixtures.** The full
top-level JPEG corpus from the official
[C2PA public-testfiles](https://github.com/c2pa-org/public-testfiles)
repository (CC BY-SA), plus real PNGs from
[example-assets](https://github.com/contentauth/example-assets) (MIT), live in
`samples/` and drive the regression tests in
`test_c2pa_reader_real_files.py`. Coverage includes:

- **Both container formats** — JPEG (multi-segment APP11 reassembly) and PNG
  (single `caBX` chunk).
- **Four independent generators** — Adobe (c2pa-rs), Nikon (Z 9 camera),
  Truepic (Lens SDK), and OpenAI/ChatGPT (GPT-4o) — so the reader isn't
  accidentally specific to one vendor's output.
- **Manifest chains**, from a single manifest up to a six-manifest
  provenance chain, all correctly enumerated.
- **Files with no Content Credentials at all**, confirming the "not found"
  path doesn't false-positive on ordinary Lightroom/XMP metadata.
- **The repository's deliberately-corrupt negative cases** (invalid
  signature, tampered assertion, hash mismatch, missing claim) — the inputs
  most likely to break a hand-written binary parser. None crash; all are
  reported honestly.

Manifests are reassembled from multiple ~64 KB JPEG APP11 segments and
decoded, including the actual embedded X.509 certificate chain inside the
COSE signature structure, surfaced for anyone who wants to inspect or
independently verify it — even though this tool itself does not.

**The caveat is proven, not just asserted.** `adobe-20220124-CA.jpg` (valid)
and `adobe-20220124-E-sig-CA.jpg` (documented by C2PA as having an invalid
signature) are the same image, identical in length, differing by exactly six
bytes inside the manifest where the text `images` was overwritten with
`xxxxxx` to break the signature. **This tool reads both without complaint**,
because it never checks the signature — and `TestTamperBlindness` pins that
behaviour deliberately. If a future change ever made the tool *appear* to
detect tampering, the honesty caveat would become a lie, and those tests
would fail first.

**The PNG path is validated too**, via `ChatGPT_Image.png` — a genuine
GPT-4o output from the Content Authenticity Initiative's
[example-assets](https://github.com/contentauth/example-assets) repository
(MIT licensed). The official C2PA test corpus contains no PNGs at all (114
JPEGs, zero PNGs), so this file came from elsewhere; it embeds its manifest in
a single PNG `caBX` chunk and openly declares its own AI provenance
(`c2pa.created` by `GPT-4o`, `digitalSourceType: trainedAlgorithmicMedia`) —
a neat illustration of the provenance this tool exists to surface.

That one real file immediately earned its place by **exposing a genuine bug**.
The CBOR decoder had rejected indefinite-length encoding, on the reasonable
basis that the C2PA spec mandates deterministic (definite-length) CBOR — but
this real file encodes its claim and actions with indefinite lengths anyway,
so two of its most important assertions failed to decode. Reading leniently is
the right trade for a tool whose job is to show what a file *actually*
contains, so the decoder now supports it (RFC 8949 §3.2, with the canonical
test vectors in `test_cbor_decode.py`). No hand-built fixture would ever have
caught that, because the fixtures encoded my reading of the spec rather than
what implementations actually emit.

That file also **cross-checks against the reference implementation**:
example-assets publishes the manifest as extracted by the official c2patool
alongside each image, and `TestPngMatchesReferenceImplementation` asserts our
parse agrees with it on manifest URNs, titles, and generators.

And it sharpens the caveat with a subtler case than the byte-tamper above.
The official validator marks this file **Invalid** — but not because anything
was tampered with: every signature and hash inside it verifies. It's
`signingCredential.untrusted`, meaning the certificate isn't on the
validator's trust list. This tool reports it with no error whatsoever, because
it checks neither signatures nor trust lists. **A reader of our output cannot
tell trusted from untrusted, nor valid from tampered.** That distinction is
pinned by a test, so it can't quietly erode.

## PREMIS export — provenance that outlives the file

This is the point the whole project started from: C2PA metadata lives *inside*
the image, so it dies on the first screenshot, re-encode, or format migration.
Provenance survives the long term only if it is also recorded as preservation
metadata that travels alongside the object. That is what
[PREMIS](https://www.loc.gov/standards/premis/) is for.

```bash
python3 -m unwatermark c2pa --recursive collection/ --premis-dir premis/
```

There's also a **Download PREMIS record (.xml)** button on the `/c2pa` page.
Each record is a PREMIS 3.0 document containing:

- an **Object** (`xsi:type="premis:file"`) for the image, with a **SHA-256
  fixity value** — so the record keeps supporting integrity checks long after
  the embedded credentials are gone — plus size, media type, and the C2PA
  manifest preserved verbatim in `objectCharacteristicsExtension`;
- two **Events**, `message digest calculation` and `metadata extraction`,
  typed against the Library of Congress
  [eventType vocabulary](https://id.loc.gov/vocabulary/preservation/eventType.html);
- an **Agent** for this tool, whose `agentNote` states that it does not verify
  signatures.

**The important design decision.** The claims *inside* a C2PA manifest — that
an image was created by GPT-4o, edited in Photoshop, and so on — are
deliberately **not** emitted as PREMIS Events, even though it would be easy
and superficially impressive to do so.

A PREMIS Event asserts something the repository did or witnessed. A C2PA
assertion is an unverified claim by a third party, which this tool cannot
check. Promoting those claims to first-class Events would launder *"the file
says this happened"* into *"the repository records that this happened"* —
exactly the overclaiming this project exists to avoid. So the only Events are
the two things the tool genuinely did, and the claims are preserved verbatim
in the extension, still carrying their own "not verified" caveat. There are
tests pinning this, so it cannot quietly erode.

**On validation:** the output is built directly from the rules in the official
`premis.xsd` (element order, cardinality, the required `version="3.0"`, the
abstract-type substitution), and the test suite asserts those constraints
element by element. It is *not* run through a full XSD validator here, because
that needs a third-party library. To validate independently:

```bash
xmllint --noout --schema https://www.loc.gov/standards/premis/v3/premis.xsd record.premis.xml
```

## For students facing an accusation

The `/detectors` page is a printable, sourced one-pager on what AI writing
detectors can and cannot show — written to be handed to a marker or an
academic-conduct panel alongside the record built on `/provenance`. It quotes
figures from three independent sources, including the peer-reviewed
[Liang et al. (2023)](https://www.cell.com/patterns/fulltext/S2666-3899(23)00130-7)
study in *Patterns*, which found seven widely-used detectors were near-perfect
on US eighth-grade essays but misclassified **61.22%** of genuine TOEFL essays
by non-native English speakers as AI-generated.

It also cites [Chambers &amp; Kelley (2026)](https://arxiv.org/abs/2607.14729),
which tested ~60,000 Reddit posts and found writing from autistic-community
authors carried a 25–50% greater chance of being flagged — stated alongside the
low base rates (1.7% vs 1.2%), because the odds ratio without the base rate
would be the kind of overstatement that gets a document dismissed. For UK
students it adds the *reasonable adjustment* argument from
[Morgan (2026)](https://doi.org/10.1080/09687599.2026.2667528) in
*Disability &amp; Society*, which is stronger than an evidential point because
it engages a legal duty rather than a judgement call.

It argues one narrow, defensible claim — *a detector score is not by itself a
sound basis for a finding of misconduct* — and it includes a section stating
the limits of its own evidence, including the constraints its authors put on
their own studies and a vendor counter-claim a panel might raise. A document
that overstates gets taken apart in the room; naming the weaknesses first is
what makes the rest credible.

## File in / out

Every text field has a **Load file…** button (`.txt`, `.md`, `.docx`, `.odt`),
and cleaned text can be downloaded, not just copied:

- `.txt` / `.md` are read entirely **client-side** in the browser — they
  never touch the local server.
- `.docx` and `.odt` are zip archives, which a browser can't unpack without a
  third-party library, so those go to a small stdlib-only server endpoint
  (`zipfile` + `xml.etree`, both standard library — no dependencies added).
  The format is detected from the file's *contents*, not its extension, so a
  document saved under the wrong suffix still reads correctly.
- `.odt` (LibreOffice) needs slightly different handling from `.docx`:
  OpenDocument stores text as XML *mixed content*, sitting directly in a
  paragraph and in the tail of every inline element, rather than in dedicated
  run elements. A reader that only collects element text silently drops
  everything after the first bit of bold or italic — quiet data loss a
  hidden-character scanner must not have — so `odt_extract.py` walks tails
  too, and there's a test for exactly that case against a real
  LibreOffice-produced file.
- Output stays plain text (`.txt`) on purpose: generating a valid `.docx`
  from scratch is a much bigger, more fragile undertaking than reading one,
  for a need that pasting text back into an existing document already
  solves.
- **Not supported:** PDF and legacy `.doc` — neither has a reliable
  stdlib-only extraction path, and pulling in a library for them would break
  the zero-dependency design this tool relies on for trust.

## Accessibility

A tool built for visually-impaired and neurodiverse writers should be
exemplary here, so every page has a dedicated pass: every text field has a
real programmatic label (not just a placeholder); result summaries and
comparison stats are `role="status"` regions so screen readers announce them
as they appear; the invisible-character "chips" are keyboard-focusable with a
full description, not just a mouse-hover tooltip; the minor-fix-vs-rewrite
distinction in the draft comparison is stated in words for screen readers,
not conveyed by color alone; table headers use `scope="col"`; and every
interactive element gets a visible focus ring. This hasn't been tested with
an actual screen reader — if something doesn't work well with yours, say so.

## Test

```bash
python3 -m unittest discover -p "test_*.py"
```

Runs the full suite (270+ tests) covering every module below, including the
real-file regression tests in `test_c2pa_reader_real_files.py`. Those skip
gracefully if the 23 MB `samples/` directory isn't present, so the suite
still passes on a copy without it.

## Layout

```
server.py                       stdlib HTTP server, localhost only, routes for all 3 pages

unwatermark/
  cli.py                        command-line interface (python3 -m unwatermark)
  __main__.py                   entry point so the CLI runs without installation
  scan.py                       hidden-character + homoglyph scanner
  clean.py                      text cleaning (invisible-char removal, homoglyph fix)
  provenance.py                 draft diff + provenance record/statement builder
  docx_extract.py               .docx -> plain text (zipfile + xml.etree)
  odt_extract.py                .odt  -> plain text (OpenDocument mixed content)
  documents.py                  detects .docx vs .odt by content, not extension
  c2pa_reader.py                ties the C2PA layers together; summary + sidecar output
  premis.py                     PREMIS 3.0 preservation-metadata export
  fixity.py                     re-check files against recorded PREMIS checksums
  bagit.py                      BagIt (RFC 8493) package writer
  jumbf.py                      JUMBF box parser (the container format C2PA uses)
  cbor_decode.py                minimal CBOR decoder (stdlib has none)
  png_chunks.py                 PNG chunk reader (finds the C2PA 'caBX' chunk)
  jpeg_segments.py              JPEG marker/APP11 segment reader and reassembler
  xmp.py                        finds external-manifest pointers in XMP metadata

static/
  index.html, inspect.js        hidden-character inspector page
  provenance.html, provenance.js  provenance record builder page
  c2pa.html, c2pa.js            C2PA manifest reader page
  detectors.html                printable, sourced explainer on detector false positives
  style.css                     shared styles (light/dark aware)

samples/                        real-world fixtures; ~23 MB, optional (tests skip if absent)
                                29 C2PA images: 26 JPEGs from c2pa-org/public-testfiles
                                (CC BY-SA) + PNGs from contentauth/example-assets & c2pa-rs (MIT)
                                1 LibreOffice-produced .odt for the mixed-content test

test_*.py                       one test file per module above, plus test_c2pa_reader_real_files.py
```

## License

MIT — see [LICENSE](LICENSE). Chosen so that other educators, students and
small archives can reuse and adapt this freely; change it if you'd rather.

The files in `samples/` are third-party test material under their own terms:
the C2PA JPEGs are CC BY-SA from
[c2pa-org/public-testfiles](https://github.com/c2pa-org/public-testfiles), and
the PNGs are MIT from [contentauth](https://github.com/contentauth/example-assets).
