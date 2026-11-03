"""Клиент Telegram Bot API на urllib."""
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger("tg")


class TelegramError(Exception):
    def __init__(self, description, code=0):
        super().__init__(description)
        self.code = code


class Telegram:
    def __init__(self, token, base):
        self.token = token
        self.base = base.rstrip("/")

    def _url(self, method):
        return f"{self.base}/bot{self.token}/{method}"

    def _post(self, method, data, content_type, timeout):
        req = urllib.request.Request(self._url(method), data=data,
                                     headers={"Content-Type": content_type})
        attempt = 0
        while True:
            attempt += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.load(resp).get("result")
            except urllib.error.HTTPError as exc:
                try:
                    body = json.load(exc)
                except ValueError:
                    body = {}
                retry = (body.get("parameters") or {}).get("retry_after")
                if exc.code == 429 and attempt < 6:
                    time.sleep(float(retry or 1))
                    continue
                if exc.code >= 500 and attempt < 4:
                    time.sleep(attempt)
                    continue
                raise TelegramError(body.get("description", str(exc.code)), exc.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                if attempt < 4:
                    time.sleep(attempt)
                    continue
                # В тексте ошибки urllib может быть адрес с токеном: наружу не отдаём.
                raise TelegramError(f"сеть: {type(exc).__name__}") from None

    def call(self, method, http_timeout=40, **params):
        params = {k: v for k, v in params.items() if v is not None}
        return self._post(method, json.dumps(params).encode(), "application/json", http_timeout)

    def send(self, chat_id, text, **params):
        return self.call("sendMessage", chat_id=chat_id, text=text, **params)

    def send_html(self, chat_id, html_text, **params):
        return self.send(chat_id, html_text, parse_mode="HTML",
                         link_preview_options={"is_disabled": True}, **params)

    def edit_html(self, chat_id, message_id, html_text, **params):
        try:
            return self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                             text=html_text, parse_mode="HTML",
                             link_preview_options={"is_disabled": True}, **params)
        except TelegramError as exc:
            if "message is not modified" in str(exc):
                return None
            raise

    def send_document(self, chat_id, name, payload, caption=None, **params):
        """payload — bytes, либо file_id уже загруженного файла (str)."""
        if isinstance(payload, str):
            return self.call("sendDocument", chat_id=chat_id, document=payload, caption=caption, **params)
        boundary = uuid.uuid4().hex
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        for key, value in params.items():
            fields[key] = value if isinstance(value, str) else json.dumps(value)
        chunks = []
        for key, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        safe = re.sub(r'["\r\n\\]', "_", name)
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                       f'filename="{safe}"\r\n'
                       "Content-Type: application/octet-stream\r\n\r\n").encode())
        chunks.append(payload)
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        return self._post("sendDocument", b"".join(chunks),
                          f"multipart/form-data; boundary={boundary}", 300)

    def open_file(self, file_id):
        """Открывает файл Telegram для чтения. Возвращает HTTP-ответ."""
        info = self.call("getFile", file_id=file_id)
        url = f"{self.base}/file/bot{self.token}/{info['file_path']}"
        return urllib.request.urlopen(url, timeout=120)
