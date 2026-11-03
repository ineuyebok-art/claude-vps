"""Fake Anthropic Messages API, so that the real `claude` binary can run offline in tests.

`RUNTOOL` in the conversation makes the first reply a Bash tool call; `SLOW` delays the answer.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeAnthropic:
    def __init__(self, tool_command="python3 -c 'print(1)'"):
        fake = self
        self.tool_command = tool_command
        self.requests = []
        self.release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
                    data = b'{"input_tokens":10}'
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                fake.requests.append(body)
                self.stream(body)

            def event(self, name, data):
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

            def stream(self, body):
                messages = body.get("messages", [])
                text = json.dumps(messages, ensure_ascii=False)
                has_result = any(isinstance(m.get("content"), list)
                                 and any(b.get("type") == "tool_result" for b in m["content"]) for m in messages)
                tool = "RUNTOOL" in text and not has_result
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                self.event("message_start", {"type": "message_start", "message": {
                    "id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model", "x"),
                    "content": [], "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 5, "output_tokens": 1}}})
                if tool:
                    self.event("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                        "type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}})
                    self.event("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps({"command": fake.tool_command, "description": "test"})}})
                    self.event("content_block_stop", {"type": "content_block_stop", "index": 0})
                    self.event("message_delta", {"type": "message_delta", "delta": {
                        "stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 5}})
                else:
                    last = messages[-1]["content"] if messages else ""
                    last_text = json.dumps(last, ensure_ascii=False)
                    if "SLOW" in text[-3000:] and "SECOND" not in last_text:
                        fake.release.wait(20)
                    turns = sum(1 for m in messages if m.get("role") == "assistant")
                    self.event("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                        "type": "text", "text": ""}})
                    for word in ("Настоящий ", "ответ ", f"после {turns} ходов"):
                        self.event("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                            "type": "text_delta", "text": word}})
                        time.sleep(0.02)
                    self.event("content_block_stop", {"type": "content_block_stop", "index": 0})
                    self.event("message_delta", {"type": "message_delta", "delta": {
                        "stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}})
                self.event("message_stop", {"type": "message_stop"})
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
