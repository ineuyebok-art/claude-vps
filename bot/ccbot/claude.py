"""Запуск `claude -p` и разбор его потока stream-json."""
import json
import logging
import subprocess
import threading
import uuid

from . import config, helper

log = logging.getLogger("claude")
LOGIN_HINTS = ("not logged in", "/login", "invalid api key", "please run", "oauth token")


def new_session_id():
    return str(uuid.uuid4())


class Run:
    """Один запуск Claude. run() блокирует вызывающий поток до конца ответа."""

    def __init__(self, cwd, prompt, model, session=None, resume=False, fork=False,
                 on_text=None, on_tool=None, on_permission=None):
        self.cwd = str(cwd)
        self.prompt = prompt
        self.model = model
        self.session = session or new_session_id()
        self.resume = resume
        self.fork = fork
        self.on_text = on_text or (lambda text: None)
        self.on_tool = on_tool or (lambda name, data: None)
        self.on_permission = on_permission
        self.text = ""
        self.error = ""
        self.started = threading.Event()   # разговор уже записан и его можно ветвить
        self.finished = threading.Event()
        self.result_session = None
        self._stdin_lock = threading.Lock()
        self.proc = None

    def argv(self):
        args = ["claude", self.cwd, "-p", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--permission-prompt-tool", "stdio", "--permission-mode", "acceptEdits",
                "--model", self.model, "--allowedTools", ",".join(config.ALLOWED_TOOLS)]
        if self.resume:
            args += ["--resume", self.session]
            if self.fork:
                args.append("--fork-session")
        else:
            args += ["--session-id", self.session]
        return args

    def _write(self, obj):
        with self._stdin_lock:
            try:
                self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def _answer_permission(self, event):
        request = event["request"]
        allow, reason = False, "Отклонено"
        try:
            allow, reason = self.on_permission(request.get("tool_name", ""), request.get("input") or {})
        except Exception:  # noqa: BLE001 — при любой ошибке безопаснее запретить
            log.exception("permission handler failed")
        if allow:
            body = {"behavior": "allow", "updatedInput": request.get("input") or {}}
        else:
            body = {"behavior": "deny", "message": reason or "Отклонено пользователем"}
        self._write({"type": "control_response",
                     "response": {"subtype": "success", "request_id": event["request_id"], "response": body}})

    def _handle(self, event):
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.result_session = event.get("session_id") or self.result_session
            self.started.set()
        elif kind == "stream_event":
            inner = event.get("event") or {}
            kind2 = inner.get("type")
            if kind2 == "content_block_delta" and (inner.get("delta") or {}).get("type") == "text_delta":
                self.text += inner["delta"].get("text", "")
                self.on_text(self.text)
            elif kind2 == "message_start" and self.text and not self.text.endswith("\n\n"):
                self.text += "\n\n"
            elif kind2 == "content_block_start":
                block = inner.get("content_block") or {}
                if block.get("type") == "tool_use":
                    self.on_tool(block.get("name", ""), {})
        elif kind == "control_request":
            request = event.get("request") or {}
            if request.get("subtype") == "can_use_tool" and self.on_permission:
                threading.Thread(target=self._answer_permission, args=(event,), daemon=True).start()
            elif request.get("subtype") == "can_use_tool":
                self._write({"type": "control_response", "response": {
                    "subtype": "success", "request_id": event.get("request_id"),
                    "response": {"behavior": "deny", "message": "Нет обработчика разрешений"}}})
        elif kind == "result":
            self.result_session = event.get("session_id") or self.result_session
            if event.get("is_error") or str(event.get("subtype", "")).startswith("error"):
                self.error = str(event.get("result") or event.get("subtype") or "ошибка Claude")
            elif not self.text.strip():
                self.text = str(event.get("result") or "")
                self.on_text(self.text)
            with self._stdin_lock:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass

    def run(self):
        try:
            self.proc = helper.popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        except OSError as exc:
            self.error = f"Не удалось запустить Claude: {exc}"
            self.started.set()
            self.finished.set()
            return self
        self._write({"type": "user", "message": {"role": "user", "content": self.prompt},
                     "parent_tool_use_id": None, "session_id": ""})
        stderr_tail = []

        def drain():
            for line in self.proc.stderr:
                stderr_tail.append(line.decode("utf-8", "replace"))
                del stderr_tail[:-20]

        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()
        for line in self.proc.stdout:
            try:
                self._handle(json.loads(line))
            except ValueError:
                continue
            except Exception:  # noqa: BLE001 — разбор одного события не должен рвать ответ
                log.exception("bad event")
        code = self.proc.wait()
        drainer.join(2)
        self._close()
        if code != 0 and not self.error and not self.text.strip():
            self.error = "".join(stderr_tail).strip()[-500:] or f"Claude завершился с кодом {code}"
        self.started.set()
        self.finished.set()
        return self

    def _close(self):
        with self._stdin_lock:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass

    def needs_login(self):
        return any(hint in self.error.lower() for hint in LOGIN_HINTS)
