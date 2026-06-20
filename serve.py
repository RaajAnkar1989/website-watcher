#!/usr/bin/env python3
"""Local dashboard server — no GitHub token needed."""

import json
import subprocess
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
CONFIG_PATH = ROOT / "config.json"
DOCS_CONFIG_PATH = DOCS / "config.json"
PORT = 8888


def sync_config_to_docs():
    """Keep docs/config.json in sync for static-file fallback."""
    if CONFIG_PATH.exists():
        DOCS_CONFIG_PATH.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            self._send_json(200, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            return
        super().do_GET()

    def do_PUT(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            try:
                data = self._read_body()
                CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                sync_config_to_docs()
                self._send_json(200, {"ok": True})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        self.send_error(405)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/check":
            try:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scraper.py")],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    self._send_json(
                        500,
                        {"error": result.stderr.strip() or result.stdout.strip() or "scraper failed"},
                    )
                    return
                self._send_json(200, {"ok": True, "message": result.stdout.strip()})
            except subprocess.TimeoutExpired:
                self._send_json(504, {"error": "Check timed out after 5 minutes."})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        self.send_error(405)


def main():
    sync_config_to_docs()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = HTTPServer(("", port), Handler)
    print(f"Watchboard local server: http://localhost:{port}/")
    print("Add watches and run checks here — no GitHub token required.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
