"""Text cleaning — the removal side of the tool.

`clean(text)` strips the hidden characters the scanner flags and normalises
odd spaces, returning the cleaned text plus a full log of every change. This
is text hygiene (accessibility, Trojan-Source defence, tidy copy-paste): it
removes invisible Unicode junk.

Stated precisely, because the distinction is the honest one: an edit-based
watermark made of invisible Unicode is invisible Unicode, so this removes it —
stripping zero-width characters for a screen-reader user while preserving a
mark built out of zero-width characters is not something one function can do.
What it does NOT — and cannot — remove is a statistical AI watermark, the kind
carried in word choice itself, which this tool never sees.

Homoglyph normalisation is opt-in and deliberately limited to high-confidence
"disguised" words, so a genuinely foreign word is never latinised.
"""

import unicodedata

from .scan import classify, _homoglyphs


def _apply_homoglyph_normalization(text):
    """Replace look-alike letters with their Latin equivalents, but only inside
    high-confidence `disguised` words (e.g. "paѕsword" -> "password").

    Low-confidence "might be legitimate foreign text" words are left untouched:
    latinising them would corrupt a multilingual writer's text.
    """
    disguised = [h for h in _homoglyphs(text) if h["kind"] == "disguised"]
    if not disguised:
        return text, []

    chars = list(text)  # index by codepoint, matching _homoglyphs offsets
    changes = []
    for h in disguised:
        for swap in h["swaps"]:
            idx = h["offset"] + swap["index"]
            # Guard against any drift between detection and this rewrite.
            if idx < len(chars) and chars[idx] == swap["char"]:
                chars[idx] = swap["maps_to"]
                changes.append({
                    "offset": idx,
                    "char": swap["char"],
                    "codepoint": swap["codepoint"],
                    "name": swap["name"],
                    "category": "homoglyph",
                    "action": "replaced",
                    "replacement": swap["maps_to"],
                    "token": h["token"],
                    "looks_like": h["looks_like"],
                })
    return "".join(chars), changes


def clean(text, normalize_homoglyphs=False):
    """Return cleaned text and a record of what changed.

    Keys:
      cleaned            the cleaned text
      changes            hidden-character removals/replacements, in order
      homoglyph_changes  look-alike substitutions (only if normalize_homoglyphs)
      summary            counts
    """
    out = []
    changes = []
    for i, ch in enumerate(text):
        res = classify(ch)
        if res is None:
            out.append(ch)
            continue
        category = res[0]
        if category == "nonstandard-space":
            out.append(" ")
            action, replacement = "replaced", "normal space"
        elif category == "line-separator":
            out.append("\n")
            action, replacement = "replaced", "newline"
        else:
            # Invisible / formatting / control character: safe to delete, since
            # it contributes nothing to the visible text.
            action, replacement = "removed", None
        changes.append({
            "offset": i,
            "char": ch,
            "codepoint": "U+%04X" % ord(ch),
            "name": unicodedata.name(ch, "UNNAMED CONTROL CHARACTER"),
            "category": category,
            "action": action,
            "replacement": replacement,
        })
    cleaned = "".join(out)

    homoglyph_changes = []
    if normalize_homoglyphs:
        cleaned, homoglyph_changes = _apply_homoglyph_normalization(cleaned)

    return {
        "cleaned": cleaned,
        "changes": changes,
        "homoglyph_changes": homoglyph_changes,
        "summary": {
            "removed": sum(1 for c in changes if c["action"] == "removed"),
            "replaced": sum(1 for c in changes if c["action"] == "replaced"),
            "homoglyphs_normalized": len(homoglyph_changes),
            "original_len": len(text),
            "cleaned_len": len(cleaned),
        },
    }
