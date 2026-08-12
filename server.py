#!/usr/bin/env python3
"""Local, no-dependency web UI for the hidden-character inspector.

    python3 server.py

Serves on http://127.0.0.1:8765 by default (override with --port). Binds to
loopback only: nothing is exposed to the network, and no request ever leaves
this machine. Uses only the Python standard library.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import urllib.parse

from countermark import (
    analyze, clean, diff_drafts, build_record, extract_document_text,
    read_c2pa, c2pa_to_sidecar, c2pa_to_summary_text, to_premis_xml,
)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

# Only these files may be served, by exact name. Keeps a local tool from
# turning into an accidental "read any file on disk" endpoint.
_ALLOWED = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/provenance": ("provenance.html", "text/html; charset=utf-8"),
    "/provenance.html": ("provenance.html", "text/html; charset=utf-8"),
    "/c2pa": ("c2pa.html", "text/html; charset=utf-8"),
    "/c2pa.html": ("c2pa.html", "text/html; charset=utf-8"),
    "/detectors": ("detectors.html", "text/html; charset=utf-8"),
    "/detectors.html": ("detectors.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/inspect.js": ("inspect.js", "application/javascript; charset=utf-8"),
    "/provenance.js": ("provenance.js", "application/javascript; charset=utf-8"),
    "/c2pa.js": ("c2pa.js", "application/javascript; charset=utf-8"),
}

_JSON = "application/json; charset=utf-8"
_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 20 MB is generous for a text document
_MAX_IMAGE_BYTES = 50 * 1024 * 1024  # 50 MB is generous for an image file


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        entry = _ALLOWED.get(self.path)
        if entry is None:
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        filename, content_type = entry
        try:
            data = (STATIC / filename).read_bytes()
        except OSError:
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        self._send(200, data, content_type)

    def do_POST(self):
        # Split off any query string before routing (used to pass a filename).
        route, _, query = self.path.partition("?")
        self.route, self.query = route, query
        if route == "/api/extract-document":
            self._handle_extract_document()
            return
        if route == "/api/read-c2pa":
            self._handle_read_c2pa()
            return
        if route not in ("/api/scan", "/api/clean", "/api/diff", "/api/record"):
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
            result = self._dispatch(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, json.dumps({"error": str(exc)}), _JSON)
            return
        self._send(200, json.dumps(result), _JSON)

    def _handle_extract_document(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            self._send(400, json.dumps({"error": "empty request body"}), _JSON)
            return
        if length > _MAX_DOCUMENT_BYTES:
            self._send(413, json.dumps({"error": "file too large (max 20 MB)"}), _JSON)
            return
        data = self.rfile.read(length)
        try:
            text = extract_document_text(data)
        except ValueError as exc:
            self._send(400, json.dumps({"error": str(exc)}), _JSON)
            return
        self._send(200, json.dumps({"text": text}), _JSON)

    def _handle_read_c2pa(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            self._send(400, json.dumps({"error": "empty request body"}), _JSON)
            return
        if length > _MAX_IMAGE_BYTES:
            self._send(413, json.dumps({"error": "file too large (max 50 MB)"}), _JSON)
            return
        data = self.rfile.read(length)
        # The filename rides in the query string so it can appear in the
        # PREMIS record's objectIdentifier and originalName.
        params = urllib.parse.parse_qs(self.query)
        filename = (params.get("filename") or ["image"])[0]
        result = read_c2pa(data)
        response = dict(result)
        response["summary_text"] = c2pa_to_summary_text(result)
        response["sidecar"] = c2pa_to_sidecar(result, source_filename=filename)
        response["premis_xml"] = to_premis_xml(data, result, filename=filename)
        self._send(200, json.dumps(response), _JSON)

    def _dispatch(self, payload):
        def req_str(key):
            value = payload.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"`{key}` must be a string")
            return value

        if self.route == "/api/scan":
            return analyze(req_str("text"))
        if self.route == "/api/clean":
            return clean(req_str("text"),
                         normalize_homoglyphs=bool(payload.get("normalize_homoglyphs")))
        if self.route == "/api/diff":
            return diff_drafts(req_str("original"), req_str("revised"))
        # /api/record
        return build_record(
            req_str("final_text"),
            annotations=payload.get("annotations") or [],
            original_draft=payload.get("original_draft") or "",
            metadata=payload.get("metadata") or {},
        )

    def log_message(self, *args):
        pass  # keep the terminal quiet; this is a single-user local tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1",
                        help="loopback by default; change only if you know why")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Hidden-character inspector running at {url}")
    print("Paste text in the browser. Nothing leaves this machine. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
