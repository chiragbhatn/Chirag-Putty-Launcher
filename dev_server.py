"""Development harness: serve the launcher UI in a normal browser.

    set CHIRAG_LAUNCHER_HOME=%TEMP%\launcher-test   (keeps your real data untouched)
    python dev_server.py 8765

The UI talks to the same LauncherApi over HTTP on 127.0.0.1. File dialogs are not
available here. This file is not bundled into the EXE.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from device_launcher_app import LauncherApi, resource_dir

EVENTS: "queue.Queue[dict]" = queue.Queue()
API = LauncherApi(emit=lambda event, payload: EVENTS.put({"event": event, "payload": payload}))
UI = resource_dir()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/_events"):
            items = []
            while not EVENTS.empty():
                items.append(EVENTS.get_nowait())
            self._send(200, json.dumps(items).encode(), "application/json")
            return
        name = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        target = (UI / name).resolve()
        if UI.resolve() not in target.parents or not target.is_file():
            self._send(404, b"Not found", "text/plain")
            return
        self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def do_POST(self) -> None:
        name = self.path.split("/api/", 1)[-1]
        method = getattr(API, name, None)
        if name.startswith("_") or not callable(method):
            self._send(404, b'{"__error": "Unknown method"}', "application/json")
            return
        length = int(self.headers.get("Content-Length") or 0)
        args = json.loads(self.rfile.read(length) or b"{}").get("args", [])
        self._send(200, json.dumps(method(*args)).encode(), "application/json")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    API.start_background()
    print(f"Serving launcher UI on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
