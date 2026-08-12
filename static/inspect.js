"use strict";

// Short labels shown on the chip that stands in for an invisible character.
const CHIP_LABEL = {
  "zero-width": "ZW",
  "invisible-math": "∅",
  "invisible-tag": "TAG",
  "variation-selector": "VS",
  "soft-hyphen": "SHY",
  "bidi": "BIDI",
  "nonstandard-space": "SP",
  "line-separator": "LSEP",
  "control": "CTRL",
  "format": "FMT",
  "other": "?",
};

const $ = (id) => document.getElementById(id);

// Announce a short message to screen reader users via the sr-only status region,
// without moving keyboard focus away from whatever the user is doing.
function announce(message) {
  $("sr-status").textContent = message;
}

function download(filename, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// .txt/.md are read entirely client-side — nothing touches the server for
// those. .docx/.odt are zip archives, which the browser can't unpack without
// a library, so those go to the local /api/extract-document endpoint.
async function loadFileInto(file, textareaId, whatLabel) {
  if (/\.(docx|odt)$/i.test(file.name)) {
    const buf = await file.arrayBuffer();
    const res = await fetch("/api/extract-document", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: buf,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert("Could not read " + file.name + ": " + (err.error || res.status));
      return;
    }
    const data = await res.json();
    $(textareaId).value = data.text;
  } else {
    $(textareaId).value = await file.text();
  }
  announce(`${whatLabel} loaded from ${file.name}.`);
}

async function inspect() {
  const text = $("input").value;
  const res = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    alert("Scan failed: " + res.status);
    return;
  }
  render(await res.json());
}

async function cleanText() {
  const text = $("input").value;
  const res = await fetch("/api/clean", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      normalize_homoglyphs: $("fix-homoglyphs").checked,
    }),
  });
  if (!res.ok) {
    alert("Clean failed: " + res.status);
    return;
  }
  renderClean(await res.json());
}

function renderClean(data) {
  $("clean-panel").hidden = false;
  const s = data.summary;

  const bits = [];
  if (s.removed) bits.push(`${s.removed} hidden character${s.removed === 1 ? "" : "s"} removed`);
  if (s.replaced) bits.push(`${s.replaced} odd space${s.replaced === 1 ? "" : "s"} normalised`);
  if (s.homoglyphs_normalized) bits.push(`${s.homoglyphs_normalized} disguised letter${s.homoglyphs_normalized === 1 ? "" : "s"} fixed`);
  $("clean-summary").textContent = bits.length
    ? "Cleaned: " + bits.join(", ") + "."
    : "Nothing to clean — no hidden characters found.";

  $("clean-output").value = data.cleaned;

  // Merge both change lists into one ordered log.
  const all = data.changes.concat(data.homoglyph_changes).sort((a, b) => a.offset - b.offset);
  const log = $("change-log");
  const tbody = log.querySelector("tbody");
  tbody.textContent = "";
  $("change-count").textContent = String(all.length);
  log.hidden = all.length === 0;
  for (const c of all) {
    const tr = document.createElement("tr");
    const action =
      c.action === "removed" ? "removed"
      : c.category === "homoglyph" ? `→ “${c.replacement}” (was disguising ${c.looks_like})`
      : `→ ${c.replacement}`;
    [String(c.offset), c.codepoint, c.name, action].forEach((value, idx) => {
      const td = document.createElement("td");
      if (idx === 1) {
        const code = document.createElement("code");
        code.textContent = value;
        td.appendChild(code);
      } else {
        td.textContent = value;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
}

async function copyClean() {
  const text = $("clean-output").value;
  try {
    await navigator.clipboard.writeText(text);
    flashCopied();
  } catch (e) {
    // Fallback for browsers without the async clipboard API.
    const out = $("clean-output");
    out.focus();
    out.select();
    document.execCommand("copy");
    flashCopied();
  }
}

function flashCopied() {
  const btn = $("copy-clean");
  const original = btn.textContent;
  btn.textContent = "Copied ✓";
  announce("Cleaned text copied to clipboard.");
  setTimeout(() => { btn.textContent = original; }, 1500);
}

function render(data) {
  $("results").hidden = false;
  renderSummary(data.summary);
  renderText(data.segments);
  renderTable(data.findings);
  renderHomoglyphs(data.homoglyphs);
}

function renderSummary(s) {
  const box = $("summary");
  const clean = s.flag_count === 0 && s.homoglyph_count === 0;
  box.classList.toggle("clean", clean);
  if (clean) {
    box.innerHTML =
      "No hidden or disguised characters found in " +
      `${s.total_chars} characters. ` +
      "<em>(Remember: this does not rule out a statistical watermark — see the note above.)</em>";
    return;
  }
  const parts = [];
  if (s.flag_count) {
    const kinds = Object.entries(s.counts)
      .map(([k, v]) => `${v} ${k}`)
      .join(", ");
    parts.push(`<span class="count">${s.flag_count}</span> hidden/disguised character${s.flag_count === 1 ? "" : "s"} (${kinds})`);
  }
  if (s.homoglyph_count) {
    parts.push(`<span class="count">${s.homoglyph_count}</span> look-alike word${s.homoglyph_count === 1 ? "" : "s"}`);
  }
  box.innerHTML = "Found " + parts.join(" and ") + `, in ${s.total_chars} characters.`;
}

function renderText(segments) {
  const container = $("rendered");
  container.textContent = "";
  for (const seg of segments) {
    if (seg.type === "plain") {
      container.appendChild(document.createTextNode(seg.text));
    } else {
      const chip = document.createElement("span");
      chip.className = "chip " + seg.severity;
      chip.textContent = CHIP_LABEL[seg.category] || "?";
      chip.title = `${seg.codepoint} ${seg.name}\n${seg.note}`;
      // tabindex makes the detail reachable by keyboard, not just mouse hover;
      // aria-label gives screen reader users the full description in one go,
      // instead of spelling out an abbreviation like "Z W".
      chip.tabIndex = 0;
      chip.setAttribute("aria-label",
        `Hidden character, ${seg.severity} severity: ${seg.codepoint} ${seg.name}. ${seg.note}`);
      container.appendChild(chip);
    }
  }
}

function renderTable(findings) {
  const table = $("details");
  const heading = $("details-heading");
  const tbody = table.querySelector("tbody");
  tbody.textContent = "";
  const show = findings.length > 0;
  table.hidden = !show;
  heading.hidden = !show;
  findings.forEach((f, i) => {
    const tr = document.createElement("tr");
    const cells = [
      String(i + 1),
      String(f.offset),
      f.codepoint,
      f.name,
      f.note,
    ];
    cells.forEach((value, idx) => {
      const td = document.createElement("td");
      if (idx === 2) {
        const code = document.createElement("code");
        code.textContent = value;
        td.appendChild(code);
      } else {
        td.textContent = value;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

function renderHomoglyphs(items) {
  const box = $("homoglyphs");
  const list = $("homoglyph-list");
  list.textContent = "";
  box.hidden = items.length === 0;
  for (const h of items) {
    const li = document.createElement("li");

    const badge = document.createElement("span");
    badge.className = "conf " + h.confidence;
    badge.textContent = h.confidence;
    li.appendChild(badge);
    li.appendChild(document.createTextNode(" "));

    const code = document.createElement("code");
    code.textContent = h.token;
    li.appendChild(code);

    let detail = ` — at position ${h.offset}`;
    if (h.looks_like) detail += `, looks like “${h.looks_like}”`;
    detail += `. Mixes ${h.scripts.join(" + ")}.`;
    li.appendChild(document.createTextNode(detail));

    // Per-character breakdown of exactly which letters are disguised.
    if (h.swaps && h.swaps.length) {
      const ul = document.createElement("ul");
      for (const s of h.swaps) {
        const sli = document.createElement("li");
        sli.textContent = `${s.char} (${s.codepoint} ${s.name}) → ${s.maps_to}`;
        ul.appendChild(sli);
      }
      li.appendChild(ul);
    }
    list.appendChild(li);
  }
}

// An example built from explicit escape codes, so it is visible in the source
// and guaranteed to carry the hidden characters it claims to.
const EXAMPLE =
  "This sentence looks​ ordinary but hides a zero-width space.\n" +
  "Here is a non-breaking space, and a soft­hyphen inside a word.\n" +
  "A paѕsword field (that s is Cyrillic) shows a look-alike letter.\n" +
  "A link to раура (all Cyrillic) imitates a Latin brand name.\n" +
  "The next word smuggles a tag character\u{E0048}\u{E0049} after it.";

function init() {
  $("inspect").addEventListener("click", inspect);
  $("clean").addEventListener("click", cleanText);
  $("copy-clean").addEventListener("click", copyClean);
  $("download-clean").addEventListener("click", () =>
    download("cleaned-text.txt", $("clean-output").value, "text/plain"));
  $("load-file").addEventListener("click", () => $("input-file").click());
  $("input-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    await loadFileInto(file, "input", "Text");
    e.target.value = ""; // reset so re-selecting the same file still fires change
  });
  $("clear").addEventListener("click", () => {
    $("input").value = "";
    $("results").hidden = true;
    $("clean-panel").hidden = true;
  });
  $("sample").addEventListener("click", () => {
    $("input").value = EXAMPLE;
    inspect();
  });
}

document.addEventListener("DOMContentLoaded", init);
