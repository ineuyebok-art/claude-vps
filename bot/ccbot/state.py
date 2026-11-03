"""Состояние бота: один JSON-файл с атомарной записью. Базы данных нет намеренно."""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time

MAX_MESSAGES = 3000
MAX_UPDATES = 1000


def new_account(name, chat_id):
    return {"name": name, "chat_id": chat_id, "model": "opus", "cwd": None,
            "session": None, "fork_next": False}


class State:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"accounts": {}, "codes": {}, "messages": {}, "updates": [],
                     "offset": 0, "always": []}
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self):
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.rename(tmp, self.path)

    # ------------------------------------------------------------ аккаунты ---
    def account(self, user_id):
        return self.data["accounts"].get(str(user_id))

    def accounts(self):
        return dict(self.data["accounts"])

    def add_account(self, user_id, name, chat_id):
        with self.lock:
            self.data["accounts"][str(user_id)] = new_account(name, chat_id)
            self.save()

    def remove_account(self, user_id):
        with self.lock:
            self.data["accounts"].pop(str(user_id), None)
            self.save()

    def update_account(self, user_id, **fields):
        with self.lock:
            self.data["accounts"][str(user_id)].update(fields)
            self.save()

    # ---------------------------------------------------- одноразовые коды ---
    @staticmethod
    def _hash(code):
        return hashlib.sha256(code.encode()).hexdigest()

    def new_code(self, ttl):
        code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(10))
        with self.lock:
            self.data["codes"][self._hash(code)] = time.time() + ttl
            self.save()
        return code

    def use_code(self, code):
        digest = self._hash(code)
        with self.lock:
            now = time.time()
            self.data["codes"] = {k: v for k, v in self.data["codes"].items() if v > now}
            match = next((k for k in self.data["codes"] if hmac.compare_digest(k, digest)), None)
            if match is None:
                return False
            del self.data["codes"][match]
            self.save()
            return True

    # ------------------------------------------------------------- updates ---
    def seen_update(self, update_id):
        """True, если update уже обрабатывался. Запоминает его до обработки."""
        with self.lock:
            if update_id in self.data["updates"]:
                return True
            self.data["updates"].append(update_id)
            self.data["updates"] = self.data["updates"][-MAX_UPDATES:]
            self.data["offset"] = max(self.data["offset"], update_id + 1)
            self.save()
            return False

    # ----------------------------------------------------- карта сообщений ---
    def remember_message(self, chat_id, message_id, **info):
        with self.lock:
            messages = self.data["messages"]
            messages[f"{chat_id}:{message_id}"] = info
            if len(messages) > MAX_MESSAGES:
                for key in list(messages)[:len(messages) - MAX_MESSAGES]:
                    del messages[key]
            self.save()

    def message(self, chat_id, message_id):
        return self.data["messages"].get(f"{chat_id}:{message_id}")

    # ------------------------------------------------------------- прочее ---
    def always_allowed(self, key):
        return key in self.data["always"]

    def allow_always(self, key):
        with self.lock:
            if key not in self.data["always"]:
                self.data["always"].append(key)
                self.save()
