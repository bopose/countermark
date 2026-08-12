"use strict";

const $ = (id) => document.getElementById(id);

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

let lastResult = null;

async function readManifest() {
  const file = $("image-file").files[0];
  if (!file) {
    alert("Choose a PNG file first.");
    return;
  }
  const buf = await file.arrayBuffer();
  // The filename travels in the query string so it can be recorded in the
  // PREMIS objectIdentifier and originalName.
  const res = await fetch(
    "/api/read-c2pa?filename=" + encodeURIComponent(file.name), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: buf,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Could not read " + file.name + ": " + (err.error || res.status));
    return;
  }
  const result = await res.json();
  lastResult = result;
  render(result, file.name);
}

function render(result, filename) {
  $("result-panel").hidden = false;
  $("caveat-banner").textContent = "⚠ " + result.caveat;

  const status = $("status");
  if (!result.found) {
    status.textContent = result.error
      ? `This file could not be fully read: ${result.error}`
      : "No C2PA manifest is embedded in this file.";
  } else if (result.error) {
    status.textContent = `A C2PA chunk was found but could not be fully parsed: ${result.error}`;
  } else {
    status.textContent = "C2PA manifest found and read (not verified — see above).";
  }
  announce(status.textContent);

  // A file can carry no embedded manifest yet still point at an external one.
  // Saying only "no manifest" would hide real provenance, so surface it.
  const ext = $("external-note");
  if (result.external_manifest_url) {
    ext.hidden = false;
    ext.textContent = "";
    const strong = document.createElement("strong");
    strong.textContent = "This file declares an external manifest. ";
    ext.appendChild(strong);
    ext.appendChild(document.createTextNode(
      "Its provenance is stored in a separate file, not inside this one:"));
    const code = document.createElement("code");
    code.className = "external-url";
    code.textContent = result.external_manifest_url;
    ext.appendChild(code);
    ext.appendChild(document.createTextNode(
      "This tool does not fetch it — that would mean a network request. " +
      "To inspect it, retrieve that file yourself and open it here."));
  } else {
    ext.hidden = true;
    ext.textContent = "";
  }

  const view = $("manifest-view");
  if (result.found && !result.error) {
    view.hidden = false;
    view.textContent = result.summary_text;
  } else {
    view.hidden = true;
    view.textContent = "";
  }
}

function init() {
  $("read").addEventListener("click", readManifest);
  $("download-sidecar").addEventListener("click", () => {
    if (!lastResult) return;
    const file = $("image-file").files[0];
    const name = (file ? file.name.replace(/\.[^.]+$/, "") : "image") + ".c2pa-sidecar.json";
    download(name, JSON.stringify(lastResult.sidecar, null, 2), "application/json");
  });
  $("download-premis").addEventListener("click", () => {
    if (!lastResult) return;
    const file = $("image-file").files[0];
    const name = (file ? file.name : "image") + ".premis.xml";
    download(name, lastResult.premis_xml, "application/xml");
  });
}

document.addEventListener("DOMContentLoaded", init);
