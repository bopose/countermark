"""Companion mode: a positive provenance record the author generates themselves.

Instead of scanning text for someone else's marks, this helps a writer *show
their own process* — flipping the burden from "prove you're innocent" to
"here is how I made this."

Two parts:

* `diff_drafts` compares an original draft with an AI-assisted version and
  reports exactly what changed — the credible, hard-to-fake core. It lets a
  grammar-checked writer demonstrate that the substance is theirs and the AI
  touched only surface form.
* `build_record` assembles a full record: per-section provenance labels (a
  good-faith declaration), the optional draft comparison (computed evidence),
  and metadata — emitted as both a human-readable statement and a
  machine-readable sidecar that can be preserved alongside the work.

Nothing here claims to *prove* authorship on its own. The statement says so
plainly: annotations are self-reported; the diff statistics are the evidence.
"""

import difflib
import re

# Canonical provenance labels. The key is stored in the sidecar; the text is
# what a reader sees. Callers may also pass a free-text label.
LABELS = {
    "self": "Written by me",
    "ai-grammar": "My draft — AI corrected grammar/spelling only",
    "dictated": "Dictated by me (speech-to-text)",
    "ai-drafted": "AI-drafted, then edited by me",
    "quoted": "Quoted or cited source",
}

_DISCLAIMER = (
    "This record is a good-faith declaration by the author. The section labels "
    "are self-reported. Where an original draft is included, the comparison "
    "statistics are computed directly from the two texts and show what actually "
    "changed."
)


def _words(text):
    return re.findall(r"\S+", text)


# A changed run whose text is at least this character-similar (case-folded) to
# the original is treated as a minor fix — a spelling/casing/grammar tweak —
# rather than a substantive rewrite. 0.8 keeps "freind"->"friend" and
# "the"->"The" as minor while a genuine rephrase falls through as rewritten.
_MINOR_THRESHOLD = 0.8


def _change_kind(original_run, revised_run):
    """Classify a replaced run as 'minor' (surface fix) or 'substantive'."""
    ratio = difflib.SequenceMatcher(
        None, original_run.casefold(), revised_run.casefold()
    ).ratio()
    return "minor" if ratio >= _MINOR_THRESHOLD else "substantive"


def diff_drafts(original, revised):
    """Word-level comparison of two drafts.

    Returns {"ops": [...], "stats": {...}}. Each op is one run of the diff,
    tagged equal / insert / delete / replace; replace runs also carry a
    "change" of "minor" or "substantive". Stats separate minor surface fixes
    from substantive rewrites so the headline number reflects how much of the
    wording is genuinely the author's.
    """
    a = _words(original)
    b = _words(revised)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)

    ops = []
    equal = inserted = deleted = minor = rewritten = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        seg = {
            "op": tag,
            "original": " ".join(a[i1:i2]),
            "revised": " ".join(b[j1:j2]),
        }
        if tag == "equal":
            equal += i2 - i1
        elif tag == "insert":
            inserted += j2 - j1
        elif tag == "delete":
            deleted += i2 - i1
        elif tag == "replace":
            words = max(i2 - i1, j2 - j1)
            kind = _change_kind(seg["original"], seg["revised"])
            seg["change"] = kind
            if kind == "minor":
                minor += words
            else:
                rewritten += words
        ops.append(seg)

    revised_words = len(b)
    pct = lambda n: round(100 * n / revised_words, 1) if revised_words else (100.0 if n else 0.0)
    return {
        "ops": ops,
        "stats": {
            "original_words": len(a),
            "revised_words": revised_words,
            "unchanged_words": equal,
            "minor_fixes": minor,
            "rewritten": rewritten,
            "inserted": inserted,
            "deleted": deleted,
            "percent_unchanged": pct(equal),
            "percent_your_wording": pct(equal + minor),
        },
    }


def _label_text(label):
    return LABELS.get(label, label or "Unlabelled")


def _statement(annotations, diff, metadata):
    """Render the human-readable disclosure statement."""
    lines = ["PROVENANCE DECLARATION", "=" * 22, ""]
    for field, key in (("Author", "author"),
                       ("Assignment", "assignment"),
                       ("Date", "date"),
                       ("AI tool used", "ai_tool")):
        value = (metadata or {}).get(key)
        if value:
            lines.append(f"{field}: {value}")
    lines.append("")

    if annotations:
        lines.append("How this text was produced, section by section:")
        lines.append("")
        for n, ann in enumerate(annotations, 1):
            lines.append(f"[{n}] {_label_text(ann.get('label'))}")
            text = (ann.get("text") or "").strip()
            if text:
                lines.append(text)
            lines.append("")

    if diff:
        s = diff["stats"]
        lines.append("Comparison with my original draft:")
        lines.append(
            f"  {s['percent_unchanged']}% of the final text is word-for-word from my "
            f"own draft ({s['unchanged_words']} of {s['revised_words']} words)."
        )
        lines.append(
            f"  Counting minor spelling and grammar fixes as my own wording, "
            f"{s['percent_your_wording']}% is mine."
        )
        lines.append(
            f"  Changes from draft to final: {s['inserted']} added, "
            f"{s['deleted']} removed, {s['minor_fixes']} minor fixes, "
            f"{s['rewritten']} rewritten."
        )
        lines.append("")

    lines.append("Note: " + _DISCLAIMER)
    return "\n".join(lines)


def build_record(final_text, annotations=None, original_draft="", metadata=None):
    """Assemble a provenance record.

    Returns {"statement": str, "sidecar": dict, "diff": dict|None}.
    """
    annotations = annotations or []
    metadata = metadata or {}
    diff = None
    if original_draft and original_draft.strip():
        diff = diff_drafts(original_draft, final_text)

    statement = _statement(annotations, diff, metadata)
    sidecar = {
        "record_type": "unwatermark-provenance",
        "version": 1,
        "author": metadata.get("author") or None,
        "assignment": metadata.get("assignment") or None,
        "date": metadata.get("date") or None,
        "ai_tool": metadata.get("ai_tool") or None,
        "sections": [
            {"label": ann.get("label"),
             "label_text": _label_text(ann.get("label")),
             "text": ann.get("text", "")}
            for ann in annotations
        ],
        "draft_comparison": diff["stats"] if diff else None,
        "disclaimer": _DISCLAIMER,
    }
    return {"statement": statement, "sidecar": sidecar, "diff": diff}
