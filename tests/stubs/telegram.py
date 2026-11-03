#!/usr/bin/env python3
"""Minimal Telegram API stub for installer tests: getMe works for any token, other calls no-op."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self):
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        if "BADTOKEN" in self.path:
            code, payload = 401, {"ok": False, "description": "Unauthorized"}
        elif self.path.endswith("/getMe"):
            code, payload = 200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_bot"}}
        elif self.path.endswith("/getUpdates"):
            code, payload = 200, {"ok": True, "result": []}
        else:
            code, payload = 200, {"ok": True, "result": True}
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Handler).serve_forever()
