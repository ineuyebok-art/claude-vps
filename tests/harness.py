"""Test harness: fake Telegram API, stubbed `claude`, real as-claude helper, temp home."""
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))

from ccbot import app, config  # noqa: E402
from ccbot.state import State  # noqa: E402
from ccbot.tg import Telegram  # noqa: E402

logging.disable(logging.CRITICAL)  # threads outliving a test would only add noise

OWNER = 111
SECOND = 222
STRANGER = 999


class FakeTelegram:
    """Implements the Bot API methods the bot uses and records every call."""

    def __init__(self):
        self.calls = []
        self.updates = []
        self.files = {}
        self.next_id = 1000
        self.next_update = 1
        self.lock = threading.Lock()
        self.messages = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                match = re.match(r"/file/bot[^/]+/(.+)", self.path)
                data = fake.files.get(match.group(1)) if match else None
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                ctype = self.headers.get("content-type", "")
                if ctype.startswith("multipart"):
                    params = fake.parse_multipart(body, ctype)
                else:
                    params = json.loads(body or b"{}")
                status, payload = fake.handle(method, params)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def parse_multipart(body, ctype):
        boundary = ctype.split("boundary=")[1].encode()
        params = {}
        for part in body.split(b"--" + boundary):
            head, _, value = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]+)"', head)
            if not name:
                continue
            value = value[:-2]
            if b"filename=" in head:
                filename = re.search(rb'filename="([^"]+)"', head).group(1).decode()
                params["document_name"] = urllib.parse.unquote(filename)
                params["document_bytes"] = value
            else:
                params[name.group(1).decode()] = value.decode()
        return params

    def handle(self, method, params):
        with self.lock:
            self.calls.append((method, params))
            if method == "getUpdates":
                updates, self.updates = self.updates, []
                return 200, {"ok": True, "result": updates}
            if method in ("sendMessage", "sendDocument"):
                self.next_id += 1
                result = {"message_id": self.next_id, "chat": {"id": params.get("chat_id")}}
                if method == "sendDocument":
                    result["document"] = {"file_id": f"F{self.next_id}"}
                self.messages[self.next_id] = dict(params)
                return 200, {"ok": True, "result": result}
            if method == "editMessageText":
                entry = self.messages[int(params["message_id"])]
                if entry.get("text") == params["text"] and "reply_markup" not in params:
                    return 400, {"ok": False, "description": "Bad Request: message is not modified"}
                entry.update(params)
                return 200, {"ok": True, "result": {"message_id": params["message_id"]}}
            if method == "getFile":
                return 200, {"ok": True, "result": {"file_path": params["file_id"]}}
            return 200, {"ok": True, "result": True}

    def push(self, kind, payload):
        with self.lock:
            update = {"update_id": self.next_update, kind: payload}
            self.next_update += 1
            self.updates.append(update)
            return update

    def texts(self):
        with self.lock:
            return [m.get("text", "") for m in self.messages.values()]

    def sent(self, method):
        with self.lock:
            return [p for m, p in self.calls if m == method]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class BotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ccbot-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.bot_home = self.tmp / "bot"
        self.stub = self.tmp / "stub"
        for folder in (self.home / "chat", self.home / "projects" / "demo", self.home / "inbox",
                       self.home / "outbox", self.home / ".cc", self.home / ".claude", self.bot_home,
                       self.stub):
            folder.mkdir(parents=True)
        self.env = {
            "CCVPS_HOME": str(self.home), "CCVPS_CLAUDE_BIN": str(ROOT / "tests/stubs/claude-stub.py"),
            "CCVPS_LIB": str(ROOT / "server/bin"), "STUB_DIR": str(self.stub),
            "CCVPS_KEYS_FILE": str(self.tmp / "authorized_keys"),
            "CCVPS_KEYS_BASELINE": str(self.tmp / "keys.sha256"), "CCVPS_WATCH_INTERVAL": "0.1",
            "CCVPS_NO_PKILL": "1",
        }
        patcher = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(patcher)))
        os.environ.update(self.env)
        os.environ["PATH"] = f"{ROOT / 'tests/stubs'}:{os.environ['PATH']}"
        host_key = self.tmp / "host.pub"
        host_key.write_text("ssh-ed25519 AAAAHOSTKEY root@host\n", encoding="ascii")
        self.saved = {k: getattr(config, k) for k in (
            "HOME", "CLAUDE_HOME", "AS_CLAUDE", "KEYS", "EDIT_INTERVAL", "ALBUM_WAIT", "GEO_URL",
            "HOST_KEY", "ETC", "WATCH_INTERVAL")}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self.saved.items()])
        config.HOME = self.bot_home
        config.CLAUDE_HOME = self.home
        config.AS_CLAUDE = [sys.executable, str(ROOT / "server/bin/as-claude")]
        config.KEYS = [sys.executable, str(ROOT / "server/bin/keys")]
        config.EDIT_INTERVAL = 0.01
        config.ALBUM_WAIT = 0.15
        config.WATCH_INTERVAL = 0.1
        config.GEO_URL = "http://127.0.0.1:9/none"
        config.HOST_KEY = str(host_key)
        config.ETC = self.tmp / "etc"
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.state = State(self.bot_home / "state.json")
        self.bot = app.Bot(Telegram("TOKEN", self.fake.url), self.state)

    # --------------------------------------------------------------- helpers ---
    def bind(self, *users):
        for user in users:
            self.state.add_account(user, f"user{user}", user)

    def message(self, user=OWNER, text=None, **extra):
        self.next_message = getattr(self, "next_message", 0) + 1
        payload = {"message_id": self.next_message, "from": {"id": user, "first_name": "Ilya"},
                   "chat": {"id": user, "type": "private"}}
        if text is not None:
            payload["text"] = text
        payload.update(extra)
        self.fake.push("message", payload)
        self.bot.poll_once(0)
        return payload

    def press(self, data, user=OWNER, message_id=None):
        self.fake.push("callback_query", {"id": "cb", "from": {"id": user}, "data": data,
                                          "message": {"message_id": message_id or 1, "chat": {"id": user}}})
        self.bot.poll_once(0)

    def wait(self, condition, timeout=10, what="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.01)
        self.fail(f"timed out waiting for {what}; texts: {self.fake.texts()[-6:]}")

    def wait_text(self, needle, timeout=10):
        return self.wait(lambda: next((t for t in self.fake.texts() if needle in t), None), timeout, needle)

    def calls(self):
        path = self.stub / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def button_data(self, label):
        """Latest callback data for a button whose text contains label."""
        with self.fake.lock:
            for message in reversed(list(self.fake.messages.values())):
                markup = message.get("reply_markup") or {}
                for row in markup.get("inline_keyboard", []):
                    for item in row:
                        if label in item["text"]:
                            return item["callback_data"]
        return None
