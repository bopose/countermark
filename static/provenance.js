"use strict";

const $ = (id) => document.getElementById(id);

function announce(message) {
  $("sr-status").textContent = message;
}

// Mirror of countermark/provenance.py LABELS (key -> shown text).
const LABELS = [
  ["self", "Written by me"],
  ["ai-grammar", "My draft — AI corrected grammar/spelling only"],
  ["dictated", "Dictated by me (speech-to-text)"],
  ["ai-drafted", "AI-drafted, then edited by me"],
  ["quoted", "Quoted or cited source"],
];

let lastSidecar = null;

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(url + " -> " + res.status);
  return res.json();
}

// .txt/.md are read entirely client-side. .docx/.odt are zip archives, which
// the browser can't unpack without a library, so those go to the local
// /api/extract-document endpoint.
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

async function compare() {
  const original = $("original").value;
  const revised = $("final").value;
  if (!original.trim() || !revised.trim()) {
    alert("Paste both your original draft and the final text to compare them.");
    return;
  }
  const data = await postJSON("/api/diff", { original, revised });
  renderDiff(data);
}

function renderDiff(data) {
  $("diff-panel").hidden = false;
  const s = data.stats;
  $("diff-stats").innerHTML =
    `<strong>${s.percent_unchanged}%</strong> of the final text is word-for-word ` +
    `from your draft (${s.unchanged_words} of ${s.revised_words} words). ` +
    `Counting minor spelling/grammar fixes, <strong>${s.percent_your_wording}%</strong> ` +
    `is your wording.<br>` +
    `Changes: ${s.inserted} added, ${s.deleted} removed, ` +
    `${s.minor_fixes} minor fixes, ${s.rewritten} rewritten.`;

  const view = $("diff-view");
  view.textContent = "";
  for (const op of data.ops) {
    if (op.op === "equal") {
      view.appendChild(document.createTextNode(op.revised + " "));
    } else if (op.op === "insert") {
      view.appendChild(changeSpan(`Added: "${op.revised}"`, [tag("ins", op.revised)]));
    } else if (op.op === "delete") {
      view.appendChild(changeSpan(`Removed: "${op.original}"`, [tag("del", op.original)]));
    } else { // replace: mark minor fixes distinctly from substantive rewrites
      const cls = op.change === "minor" ? "minor" : "";
      const kind = op.change === "minor" ? "Minor fix" : "Rewrite";
      const label = `${kind}: "${op.original}" changed to "${op.revised}"`;
      view.appendChild(changeSpan(label, [tag("del", op.original, cls), tag("ins", op.revised, cls)]));
    }
  }
}

// Wraps a change's visual markup (del/ins) with one clear aria-label, so screen
// reader users get "Minor fix: X changed to Y" instead of two disconnected
// fragments — and so the minor/rewrite distinction isn't color-only.
function changeSpan(label, children) {
  const wrap = document.createElement("span");
  wrap.setAttribute("aria-label", label);
  for (const child of children) {
    child.setAttribute("aria-hidden", "true");
    wrap.appendChild(child);
  }
  return wrap;
}

function tag(name, text, cls) {
  const el = document.createElement(name);
  el.textContent = text + " ";
  if (cls) el.className = cls;
  return el;
}

function splitSections() {
  const text = $("final").value;
  const paras = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const box = $("sections");
  box.textContent = "";
  if (!paras.length) {
    alert("Add some final text first, separated into paragraphs by blank lines.");
    return;
  }
  paras.forEach((para, i) => {
    const row = document.createElement("div");
    row.className = "section-row";

    const p = document.createElement("p");
    p.className = "section-text";
    p.textContent = para;
    p.dataset.index = String(i);
    p.id = `section-text-${i}`;

    const select = document.createElement("select");
    select.className = "section-label";
    // No visible <label> here (the layout ties select-to-paragraph visually);
    // aria-label + aria-describedby give screen reader users the same link.
    select.setAttribute("aria-label", `How was this section produced?`);
    select.setAttribute("aria-describedby", p.id);
    for (const [value, label] of LABELS) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      select.appendChild(opt);
    }

    row.appendChild(select);
    row.appendChild(p);
    box.appendChild(row);
  });
  $("sections-panel").hidden = false;
}

function collectAnnotations() {
  const rows = document.querySelectorAll("#sections .section-row");
  return Array.from(rows).map((row) => ({
    label: row.querySelector(".section-label").value,
    text: row.querySelector(".section-text").textContent,
  }));
}

async function generate() {
  const final_text = $("final").value;
  if (!final_text.trim()) {
    alert("Add your final text first.");
    return;
  }
  const data = await postJSON("/api/record", {
    final_text,
    original_draft: $("original").value,
    annotations: collectAnnotations(),
    metadata: {
      author: $("author").value,
      assignment: $("assignment").value,
      ai_tool: $("ai_tool").value,
      date: $("date").value,
    },
  });
  $("record-panel").hidden = false;
  $("statement").value = data.statement;
  lastSidecar = data.sidecar;
  announce("Provenance record generated below.");
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

async function copyStatement() {
  const text = $("statement").value;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    $("statement").focus();
    $("statement").select();
    document.execCommand("copy");
  }
  const btn = $("copy-statement");
  const original = btn.textContent;
  btn.textContent = "Copied ✓";
  announce("Statement copied to clipboard.");
  setTimeout(() => { btn.textContent = original; }, 1500);
}

function today() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function wireFileLoad(buttonId, inputId, textareaId, whatLabel) {
  $(buttonId).addEventListener("click", () => $(inputId).click());
  $(inputId).addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    await loadFileInto(file, textareaId, whatLabel);
    e.target.value = "";
  });
}

function init() {
  $("date").value = today();
  wireFileLoad("load-original", "original-file", "original", "Original draft");
  wireFileLoad("load-final", "final-file", "final", "Final text");
  $("compare").addEventListener("click", compare);
  $("split").addEventListener("click", splitSections);
  $("generate").addEventListener("click", generate);
  $("copy-statement").addEventListener("click", copyStatement);
  $("download-statement").addEventListener("click", () =>
    download("provenance-statement.txt", $("statement").value, "text/plain"));
  $("download-sidecar").addEventListener("click", () => {
    if (lastSidecar) download("provenance-record.json", JSON.stringify(lastSidecar, null, 2), "application/json");
  });
}

document.addEventListener("DOMContentLoaded", init);
