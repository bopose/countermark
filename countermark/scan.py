"""The honest scanner.

`analyze(text)` returns a dict describing every invisible / non-standard /
suspicious character in `text`, plus a separate low-confidence pass for
mixed-script tokens (the signature of a homoglyph swap). It never claims a
piece of text is or isn't AI-generated.
"""

import re
import unicodedata

# Codepoints that warrant a tailored explanation. Anything not listed here is
# still caught by the category-based fallback in `classify`, just with a
# generic note. Tuple is (our_category, severity, note).
KNOWN = {
    0x200B: ("zero-width", "high", "Invisible zero-width space. No legitimate reason to appear inside normal prose; often used to hide markers or break up words to fool matching."),
    0x200C: ("zero-width", "high", "Zero-width non-joiner. Invisible; legitimate only in a few scripts (e.g. Persian), otherwise suspicious in English text."),
    0x200D: ("zero-width", "high", "Zero-width joiner. Invisible; legitimate inside emoji sequences, suspicious in plain prose."),
    0x2060: ("zero-width", "high", "Word joiner. Invisible; rarely used deliberately in ordinary writing."),
    0xFEFF: ("zero-width", "high", "Byte-order mark / zero-width no-break space. Invisible; only expected at the very start of a file, suspicious mid-text."),
    0x2061: ("invisible-math", "medium", "Invisible mathematical operator (function application)."),
    0x2062: ("invisible-math", "medium", "Invisible mathematical operator (times)."),
    0x2063: ("invisible-math", "medium", "Invisible mathematical operator (separator)."),
    0x2064: ("invisible-math", "medium", "Invisible mathematical operator (plus)."),
    0x00AD: ("soft-hyphen", "medium", "Soft hyphen. Invisible unless the word wraps; can be sprinkled through text as a hidden marker."),
    0x034F: ("zero-width", "medium", "Combining grapheme joiner. Invisible; almost never needed in ordinary text."),
    0x180E: ("zero-width", "high", "Mongolian vowel separator. Invisible in most fonts; a classic hiding spot."),

    # Bidirectional controls — reorder how text is displayed vs. stored.
    0x200E: ("bidi", "medium", "Left-to-right mark. Invisible; controls text direction."),
    0x200F: ("bidi", "medium", "Right-to-left mark. Invisible; controls text direction."),
    0x202A: ("bidi", "high", "Left-to-right embedding. Invisible; can make displayed text differ from stored text."),
    0x202B: ("bidi", "high", "Right-to-left embedding. Invisible; can make displayed text differ from stored text."),
    0x202C: ("bidi", "high", "Pop directional formatting. Invisible bidi control."),
    0x202D: ("bidi", "high", "Left-to-right override. Invisible; can reorder characters deceptively."),
    0x202E: ("bidi", "high", "Right-to-left override. Invisible; classic trick to disguise text or filenames."),
    0x2066: ("bidi", "high", "Left-to-right isolate. Invisible bidi control."),
    0x2067: ("bidi", "high", "Right-to-left isolate. Invisible bidi control."),
    0x2068: ("bidi", "high", "First-strong isolate. Invisible bidi control."),
    0x2069: ("bidi", "high", "Pop directional isolate. Invisible bidi control."),

    # Non-standard spaces — visible as gaps, but not a plain ASCII space.
    0x00A0: ("nonstandard-space", "low", "Non-breaking space (looks like a normal space but isn't)."),
    0x202F: ("nonstandard-space", "low", "Narrow no-break space (looks like a normal space but isn't)."),
    0x2007: ("nonstandard-space", "low", "Figure space (looks like a normal space but isn't)."),
    0x2000: ("nonstandard-space", "low", "En quad space."),
    0x2001: ("nonstandard-space", "low", "Em quad space."),
    0x2002: ("nonstandard-space", "low", "En space."),
    0x2003: ("nonstandard-space", "low", "Em space."),
    0x2004: ("nonstandard-space", "low", "Three-per-em space."),
    0x2005: ("nonstandard-space", "low", "Four-per-em space."),
    0x2006: ("nonstandard-space", "low", "Six-per-em space."),
    0x2008: ("nonstandard-space", "low", "Punctuation space."),
    0x2009: ("nonstandard-space", "low", "Thin space."),
    0x200A: ("nonstandard-space", "low", "Hair space."),
    0x205F: ("nonstandard-space", "low", "Medium mathematical space."),
    0x3000: ("nonstandard-space", "low", "Ideographic space (full-width; normal in CJK text, odd in English)."),
    0x2800: ("nonstandard-space", "low", "Braille pattern blank — renders as empty space."),
    0x2028: ("line-separator", "medium", "Line separator. Invisible; behaves like a newline but isn't one."),
    0x2029: ("line-separator", "medium", "Paragraph separator. Invisible; behaves like a paragraph break but isn't one."),
}

# Whitespace we treat as ordinary and never flag.
_PLAIN_WHITESPACE = {0x09, 0x0A, 0x0D, 0x20}  # tab, LF, CR, space

_TOKEN = re.compile(r"\w+", re.UNICODE)

# Look-alike ("confusable") characters, grouped by the Latin letter they
# imitate. Deliberately restricted to *strong*, near-identical matches from
# Cyrillic and Greek — the scripts used in real homoglyph swaps against Latin
# text. Loose resemblances (e.g. Cyrillic в for b) are left out on purpose:
# including them would let ordinary Russian/Greek words skeletonise to Latin
# and get flagged, which is exactly the false accusation we refuse to make.
_LOOKALIKE_GROUPS = {
    "a": "аα",   # а  α
    "c": "сϲ",   # с  ϲ
    "d": "ԁ",         # ԁ
    "e": "еε",   # е  ε
    "h": "һ",         # һ
    "i": "іι",   # і  ι
    "j": "ј",         # ј
    "k": "κ",         # κ
    "o": "оο",   # о  ο
    "p": "рρ",   # р  ρ
    "q": "ԛ",         # ԛ
    "s": "ѕ",         # ѕ
    "u": "υ",         # υ
    "v": "ν",         # ν
    "w": "ԝ",         # ԝ
    "x": "хχ",   # х  χ
    "y": "у",         # у
    "A": "АΑ",   # А  Α
    "B": "ВΒ",   # В  Β
    "C": "С",         # С
    "E": "ЕΕ",   # Е  Ε
    "H": "НΗ",   # Н  Η
    "I": "ІΙ",   # І  Ι
    "J": "Ј",         # Ј
    "K": "КΚ",   # К  Κ
    "M": "МΜ",   # М  Μ
    "N": "Ν",         # Ν
    "O": "ОΟ",   # О  Ο
    "P": "РΡ",   # Р  Ρ
    "S": "Ѕ",         # Ѕ
    "T": "ТΤ",   # Т  Τ
    "X": "ХΧ",   # Х  Χ
    "Y": "УΥ",   # У  Υ
    "Z": "Ζ",         # Ζ
}

# Inverted lookup: one look-alike character -> the Latin letter it imitates.
CONFUSABLES = {ch: latin for latin, group in _LOOKALIKE_GROUPS.items() for ch in group}


def classify(ch):
    """Return (category, severity, note) if `ch` is suspicious, else None."""
    cp = ord(ch)

    if cp in KNOWN:
        return KNOWN[cp]

    # Unicode "tag" characters — used to smuggle hidden text inside emoji.
    if 0xE0000 <= cp <= 0xE007F:
        return ("invisible-tag", "high",
                "Unicode tag character — invisible; can smuggle an entire hidden message (often appended to an emoji).")

    # Variation selectors — normally tweak-attach to the previous glyph, but a
    # long run of them can encode hidden data.
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return ("variation-selector", "medium",
                "Variation selector — normally tweaks how the previous character looks; runs of them can hide data.")

    if cp in _PLAIN_WHITESPACE:
        return None

    cat = unicodedata.category(ch)
    if cat == "Cc":
        return ("control", "medium", "Control character (non-printing).")
    if cat == "Cf":
        return ("format", "high", "Invisible formatting character.")
    if cat in ("Cs", "Co"):
        return ("other", "medium", "Surrogate or private-use codepoint — should not appear in normal text.")
    if cat in ("Zs", "Zl", "Zp"):
        return ("nonstandard-space", "low", "Non-standard whitespace (not a plain space).")
    return None


def _script_of(ch):
    """Best-effort script name for a letter, from its Unicode name prefix.

    e.g. 'LATIN SMALL LETTER A' -> 'LATIN', 'CYRILLIC SMALL LETTER A' -> 'CYRILLIC'.
    Returns None for non-letters. This is an approximation, but it is exactly
    what we need: distinguishing Latin from Cyrillic/Greek look-alikes.
    """
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    return name.split(" ", 1)[0] if name else None


def _skeleton(token):
    """Replace each look-alike character with the Latin letter it imitates.

    Returns (skeleton, swaps). `swaps` lists each substitution made, with the
    exact position and codepoint — so the UI can point at every disguised
    character and say what it stands in for.
    """
    out = []
    swaps = []
    for idx, ch in enumerate(token):
        latin = CONFUSABLES.get(ch)
        if latin is None:
            out.append(ch)
        else:
            out.append(latin)
            swaps.append({
                "index": idx,
                "char": ch,
                "codepoint": "U+%04X" % ord(ch),
                "name": unicodedata.name(ch, "?"),
                "maps_to": latin,
            })
    return "".join(out), swaps


def _letters_all_ascii(text):
    """True if `text` has at least one letter and every letter is ASCII."""
    seen = False
    for ch in text:
        if ch.isalpha():
            seen = True
            if not ch.isascii():
                return False
    return seen


def _homoglyphs(text):
    """Find look-alike (homoglyph) words in three honestly-tiered buckets.

    disguised       word mixes real Latin letters with look-alikes and reads
                    as a Latin word (e.g. "paѕsword"). High confidence: no
                    legitimate word blends alphabets.
    lookalike-word  word is entirely non-Latin yet every letter reads as Latin
                    (e.g. all-Cyrillic "раѕѕwоrd"). Low confidence, and only for
                    length >= 4, so ordinary short foreign words (Greek "και",
                    Russian "сос") are left alone.
    mixed-script    blends alphabets but does not cleanly read as Latin. Low.

    Ordinary multilingual words (привет, Straße, café) never skeletonise to
    all-Latin — they always contain a letter with no Latin look-alike — so they
    are not flagged.
    """
    out = []
    for m in _TOKEN.finditer(text):
        tok = m.group()
        letters = [c for c in tok if c.isalpha()]
        if len(letters) < 2:
            continue
        scripts = sorted({s for s in (_script_of(c) for c in tok) if s})
        skeleton, swaps = _skeleton(tok)
        reads_as_latin = bool(swaps) and _letters_all_ascii(skeleton)
        has_latin = "LATIN" in scripts

        if reads_as_latin and has_latin:
            kind, confidence = "disguised", "high"
        elif reads_as_latin and not has_latin and len(letters) >= 4:
            kind, confidence = "lookalike-word", "low"
        elif len(scripts) > 1:
            kind, confidence = "mixed-script", "low"
        else:
            continue

        item = {
            "token": tok,
            "offset": m.start(),
            "kind": kind,
            "confidence": confidence,
            "scripts": scripts,
            "swaps": swaps,
        }
        if reads_as_latin:
            item["looks_like"] = skeleton
        out.append(item)
    return out


def analyze(text):
    """Inspect `text` and return a JSON-serialisable report.

    Keys:
      segments      ordered runs of the text; each is either {type:'plain',text}
                    or {type:'flag', ...} for one suspicious character. Rendering
                    these in order reproduces the text with flags highlighted,
                    with no fragile offset arithmetic on the client.
      findings      the flag segments, in order, each with a codepoint index.
      homoglyphs    look-alike words, tiered by confidence (see _homoglyphs).
      summary       counts.
    """
    segments = []
    findings = []
    buf = []

    def flush():
        if buf:
            segments.append({"type": "plain", "text": "".join(buf)})
            buf.clear()

    for i, ch in enumerate(text):
        res = classify(ch)
        if res is None:
            buf.append(ch)
            continue
        flush()
        category, severity, note = res
        seg = {
            "type": "flag",
            "text": ch,
            "offset": i,
            "codepoint": "U+%04X" % ord(ch),
            "name": unicodedata.name(ch, "UNNAMED CONTROL CHARACTER"),
            "category": category,
            "severity": severity,
            "note": note,
        }
        segments.append(seg)
        findings.append(seg)
    flush()

    counts = {}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1

    homoglyphs = _homoglyphs(text)

    return {
        "segments": segments,
        "findings": findings,
        "homoglyphs": homoglyphs,
        "summary": {
            "total_chars": len(text),
            "flag_count": len(findings),
            "counts": counts,
            "homoglyph_count": len(homoglyphs),
        },
    }
