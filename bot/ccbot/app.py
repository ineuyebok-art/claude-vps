"""Логика бота: сообщения, разговоры, задачи, устройства."""
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import config, files, helper, render, texts
from .claude import Run
from .state import State
from .tg import Telegram, TelegramError

log = logging.getLogger("bot")
KEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com) "
                    r"([A-Za-z0-9+/]{16,}={0,2})(?: (.*))?$")
SCREEN_LIMIT = 3600
MEDIA_KINDS = ("document", "video", "audio", "animation", "video_note")


def esc(text):
    return html.escape(str(text), quote=False)


def button(label, data):
    return {"text": label, "callback_data": data}


def keyboard(*rows):
    return {"inline_keyboard": [list(r) for r in rows]}


def human_size(num):
    return f"{num / 1024 ** 3:.1f} ГБ" if num > 1024 ** 3 else f"{num / 1024 ** 2:.0f} МБ"


class Stream:
    """Показывает ответ по ходу, редактируя сообщение. Длинный ответ занимает несколько сообщений."""

    def __init__(self, bot, chat_id, message_id):
        self.bot, self.chat_id = bot, chat_id
        self.ids = [message_id]
        self.shown = [""]
        self.text = ""
        self.last = 0.0
        self.lock = threading.Lock()

    def status(self, note):
        with self.lock:
            if not self.text:
                self._put(0, f"<i>{esc(note)}</i>", None)

    def update(self, text):
        self.text = text
        if time.monotonic() - self.last >= config.EDIT_INTERVAL:
            self.flush()

    def _put(self, index, body, markup):
        if index < len(self.ids):
            if self.shown[index] == body and markup is None:
                return
            try:
                self.bot.tg.edit_html(self.chat_id, self.ids[index], body, reply_markup=markup)
            except TelegramError:
                self.bot.tg.call("editMessageText", chat_id=self.chat_id, message_id=self.ids[index],
                                 text=re.sub(r"<[^>]+>", "", body)[:4096], reply_markup=markup)
            self.shown[index] = body
            return
        try:
            sent = self.bot.tg.send_html(self.chat_id, body, reply_markup=markup)
        except TelegramError:
            sent = self.bot.tg.send(self.chat_id, re.sub(r"<[^>]+>", "", body)[:4096], reply_markup=markup)
        self.ids.append(sent["message_id"])
        self.shown.append(body)

    def flush(self, markup=None):
        with self.lock:
            self.last = time.monotonic()
            chunks = [render.to_telegram(c) for c in render.split_markdown(self.text)]
            for index, body in enumerate(chunks):
                is_last = index == len(chunks) - 1
                self._put(index, body or "…", markup if is_last else None)

    def fail(self, body):
        with self.lock:
            self._put(0, body, None)


class Bot:
    def __init__(self, tg, state):
        self.tg, self.state = tg, state
        self.lock = threading.RLock()
        self.active = {}       # разговор → идущие запуски
        self.albums = {}
        self.pending = {}      # запросы разрешений
        self.pending_keys = {}
        self.pending_runs = {}
        self.pending_input = {}
        self.project_lists = {}
        self.watches = {}
        self.watch_proc = None
        self.stopped = threading.Event()

    # ---------------------------------------------------------------- цикл ---
    def setup(self):
        try:
            self.tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in texts.COMMANDS])
        except TelegramError as exc:
            log.warning("setMyCommands: %s", exc)

    def poll_once(self, timeout=50):
        updates = self.tg.call("getUpdates", http_timeout=timeout + 15, timeout=timeout,
                               offset=self.state.data["offset"],
                               allowed_updates=["message", "callback_query"]) or []
        for update in updates:
            if self.state.seen_update(update["update_id"]):
                continue  # повтор не должен второй раз запускать Claude
            self.dispatch(update)
        return updates

    def run_forever(self):
        self.setup()
        for target in (self.events_loop, self.alerts_loop):
            threading.Thread(target=target, daemon=True).start()
        while not self.stopped.is_set():
            try:
                self.poll_once()
            except TelegramError as exc:
                log.warning("getUpdates: %s", exc)
                time.sleep(5)
            except Exception:  # noqa: BLE001 — цикл опроса не должен падать
                log.exception("poll")
                time.sleep(5)

    def stop(self):
        self.stopped.set()
        if self.watch_proc:
            self.watch_proc.kill()

    def dispatch(self, update):
        message = update.get("message")
        callback = update.get("callback_query")
        if message:
            if message.get("chat", {}).get("type") != "private":
                return
            group = message.get("media_group_id")
            if group:
                self.add_to_album(group, message)
            else:
                self.spawn(self.on_message, [message])
        elif callback:
            self.spawn(self.on_callback, callback)

    def spawn(self, target, *args):
        def guarded():
            try:
                target(*args)
            except Exception:  # noqa: BLE001 — ошибка в одном обработчике не трогает остальные
                log.exception("handler %s", getattr(target, "__name__", "?"))

        thread = threading.Thread(target=guarded, daemon=True)
        thread.start()
        return thread

    def add_to_album(self, group, message):
        with self.lock:
            album = self.albums.setdefault(group, [])
            album.append(message)
            first = len(album) == 1
        if first:
            def fire():
                time.sleep(config.ALBUM_WAIT)
                with self.lock:
                    messages = self.albums.pop(group, [])
                self.on_message(messages)

            self.spawn(fire)

    # ------------------------------------------------------------ привязка ---
    def try_bind(self, message):
        text = (message.get("text") or "").strip()
        if text.startswith("/start"):
            text = text[6:].strip()
        if not text:
            return False
        user = message["from"]
        bound = False
        first = config.HOME / "bind-code"
        if first.exists() and not self.state.accounts():
            code = first.read_text(encoding="utf-8").strip()
            if code and hmac.compare_digest(code.encode(), text.encode()):
                first.unlink()
                bound = True
        if not bound:
            bound = self.state.use_code(text)
        if bound:
            name = user.get("username") or user.get("first_name") or str(user["id"])
            self.state.add_account(user["id"], name, message["chat"]["id"])
            self.tg.send_html(message["chat"]["id"], texts.WELCOME)
        return bound

    # ---------------------------------------------------------- сообщения ---
    def on_message(self, messages):
        message = messages[0]
        user_id = message["from"]["id"]
        chat_id = message["chat"]["id"]
        account = self.state.account(user_id)
        if not account:
            self.try_bind(message)  # чужим аккаунтам бот не отвечает вообще
            return
        try:
            self.handle_message(user_id, chat_id, messages)
        except (helper.HelperError, TelegramError, OSError, subprocess.SubprocessError) as exc:
            log.warning("message: %s", type(exc).__name__)
            self.tg.send_html(chat_id, f"Не получилось: {esc(exc)}")

    def handle_message(self, user_id, chat_id, messages):
        message = messages[0]
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            command, _, rest = text[1:].partition(" ")
            handler = getattr(self, "cmd_" + command.split("@")[0].lower(), None)
            if handler:
                handler(user_id, chat_id, rest.strip())
                return
        if text and self.pending_input.pop(user_id, None) == "instructions":
            helper.text(["instructions-set"], stdin=text)
            self.tg.send(chat_id, "Инструкции сохранены.")
            return
        reply = message.get("reply_to_message")
        info = self.state.message(chat_id, reply["message_id"]) if reply else None
        if info and info.get("kind") == "task" and text:
            helper.text(["task-text", info["task"]], stdin=text)
            self.tg.send(chat_id, "Отправил в задачу.", reply_parameters=self.reply_to(message))
            return
        key = KEY_RE.match(text)
        if key:
            self.offer_key(user_id, chat_id, key)
            return
        self.ask_from_messages(user_id, chat_id, messages, info)

    @staticmethod
    def reply_to(message):
        return {"message_id": message["message_id"], "allow_sending_without_reply": True}

    def collect_text(self, messages):
        parts = []
        for message in messages:
            body = (message.get("text") or message.get("caption") or "").strip()
            origin = message.get("forward_origin") or {}
            if origin:
                who = (origin.get("chat") or {}).get("title") or (origin.get("sender_user") or {}).get(
                    "first_name") or origin.get("sender_user_name") or "канал"
                body = f"Пересланный пост, источник: {who}.\n{body}"
            if body:
                parts.append(body)
        return "\n\n".join(parts)

    def save_attachments(self, messages, notices):
        paths = []
        for message in messages:
            if message.get("voice"):
                notices.append("Голосовые сообщения я не принимаю.")
            if message.get("photo"):
                photo = message["photo"][-1]
                name = f"photo-{time.strftime('%Y%m%d-%H%M%S')}-{photo['file_unique_id'][:6]}.jpg"
                paths.append(self.save_telegram_file(photo["file_id"], name))
            for kind in MEDIA_KINDS:
                media = message.get(kind)
                if not media:
                    continue
                if media.get("file_size", 0) > config.TG_DOWNLOAD_LIMIT:
                    notices.append(f"Файл {esc(media.get('file_name', ''))} больше 20 МБ: Telegram не отдаёт "
                                   "такие боту. Пришли ссылку на Google Диск, Яндекс Диск или Dropbox.")
                    continue
                name = files.sanitize_name(media.get("file_name") or f"{kind}-{media['file_unique_id'][:6]}")
                paths.append(self.save_telegram_file(media["file_id"], name))
        return [p for p in paths if p]

    def save_telegram_file(self, file_id, name):
        with self.tg.open_file(file_id) as stream:
            return helper.write_inbox(name, stream)

    def ask_from_messages(self, user_id, chat_id, messages, info):
        notices = []
        placeholder = self.tg.send_html(chat_id, "<i>Думаю…</i>", reply_parameters=self.reply_to(messages[0]))
        stream = Stream(self, chat_id, placeholder["message_id"])
        text = self.collect_text(messages)
        try:
            paths = self.save_attachments(messages, notices)
        except (OSError, TelegramError, helper.HelperError) as exc:
            log.warning("attachment: %s", type(exc).__name__)
            stream.fail("Не удалось получить файл. Пришли его ещё раз.")
            return
        allowed, foreign = files.find_links(text)
        for url in allowed:
            stream.status("Скачиваю файл…")
            try:
                paths.append(files.download_link(url))
            except files.LinkError as exc:
                notices.append(esc(exc))
            except helper.HelperError as exc:
                notices.append(esc(exc))
        if foreign:
            notices.append("Файлы скачиваю только с Google Диска, Яндекс Диска и Dropbox. "
                           "Остальные ссылки передал Claude как текст.")
        if not text and not paths:
            stream.fail("\n".join(notices) or "Здесь нет ни текста, ни файла.")
            return
        for note in notices:
            self.tg.send_html(chat_id, note)
        if paths:
            block = "Файлы:\n" + "\n".join(f"- {p}" for p in paths)
            text = f"{text}\n\n{block}" if text else f"Посмотри файлы.\n\n{block}"
        self.converse(user_id, chat_id, text, stream, info)

    # ---------------------------------------------------------- разговоры ---
    def default_cwd(self, account):
        return account.get("cwd") or str(config.CLAUDE_HOME / "chat")

    def plan_run(self, user_id, prompt, info):
        """Решает, куда идёт сообщение: новый разговор, продолжение или параллельная ветка."""
        with self.lock:
            account = self.state.account(user_id)
            if info and info.get("session"):
                session, cwd = info["session"], info.get("cwd") or self.default_cwd(account)
            else:
                session, cwd = account.get("session"), self.default_cwd(account)
            model = account.get("model") or "opus"
            fork_next = account.get("fork_next")
            parents = list(self.active.get(session, [])) if session else []
            if not session:
                run = Run(cwd, prompt, model)
                session = run.session
                self.state.update_account(user_id, session=session, cwd=cwd)
            else:
                run = Run(cwd, prompt, model, session=session, resume=True, fork=bool(parents or fork_next))
                if fork_next:
                    self.state.update_account(user_id, fork_next=False)
            self.active.setdefault(session, []).append(run)
        return run, session, parents

    def converse(self, user_id, chat_id, prompt, stream, info=None):
        run, session, parents = self.plan_run(user_id, prompt, info)
        for parent in parents:
            parent.started.wait(20)
        run.on_text = stream.update
        run.on_tool = lambda name, _data: stream.status(f"Работаю: {name}…")
        run.on_permission = lambda tool, data: self.ask_permission(user_id, chat_id, tool, data)
        try:
            run.run()
            if run.fork and run.error and not run.text.strip() and parents:
                # Ветка не получилась: дожидаемся основного ответа и продолжаем его.
                for parent in parents:
                    parent.finished.wait(1800)
                base = parents[-1].result_session or session
                retry = Run(run.cwd, prompt, run.model, session=base, resume=True,
                            on_text=stream.update, on_tool=run.on_tool, on_permission=run.on_permission)
                run = retry.run()
        finally:
            with self.lock:
                runs = self.active.get(session, [])
                self.active[session] = [r for r in runs if not r.finished.is_set()]
                if not self.active[session]:
                    del self.active[session]
        self.finish(user_id, chat_id, run, stream)

    def finish(self, user_id, chat_id, run, stream):
        if run.error and not run.text.strip():
            if run.needs_login():
                stream.fail(texts.NOT_LOGGED_IN)
            else:
                stream.fail("Claude не ответил: " + esc(run.error[:800]))
            return
        answer = secrets.token_hex(4)
        answers = config.HOME / "answers"
        answers.mkdir(exist_ok=True)
        (answers / f"{answer}.md").write_text(run.text, encoding="utf-8")
        for old in sorted(answers.iterdir(), key=lambda p: p.stat().st_mtime)[:-500]:
            old.unlink(missing_ok=True)
        if run.error:
            stream.text += f"\n\n*Ответ оборвался: {run.error[:300]}*"
        stream.flush(keyboard((button("Короче", f"a:short:{answer}"), button("Подробнее", f"a:long:{answer}")),
                              (button("Файлом", f"a:file:{answer}"), button("Красиво", f"a:html:{answer}"))))
        session = run.result_session or run.session
        for message_id in stream.ids:
            self.state.remember_message(chat_id, message_id, kind="answer", session=session,
                                        cwd=run.cwd, answer=answer)
        self.state.update_account(user_id, session=session, cwd=run.cwd)

    # ---------------------------------------------------------- разрешения ---
    @staticmethod
    def permission_key(tool, data):
        for field in ("command", "file_path", "notebook_path", "url", "path"):
            if data.get(field):
                return f"{tool}:{data[field]}"
        return tool

    def ask_permission(self, user_id, chat_id, tool, data):
        key = self.permission_key(tool, data)
        if self.state.always_allowed(key):
            return True, ""
        request = secrets.token_hex(4)
        event = threading.Event()
        entry = {"user": user_id, "event": event, "answer": None, "key": key}
        self.pending[request] = entry
        detail = data.get("command") or data.get("file_path") or data.get("url") or json.dumps(data, ensure_ascii=False)
        sent = self.tg.send_html(
            chat_id, f"Claude просит разрешение: <b>{esc(tool)}</b>\n<pre>{esc(str(detail)[:3000])}</pre>",
            reply_markup=keyboard((button("Разрешить", f"p:{request}:yes"),
                                   button("Разрешать всегда", f"p:{request}:always"),
                                   button("Нет", f"p:{request}:no"))))
        event.wait(config.PERMISSION_TIMEOUT)
        self.pending.pop(request, None)
        answer = entry["answer"]
        label = {"yes": "разрешено", "always": "разрешено всегда", "no": "отклонено"}.get(answer, "нет ответа, отклонено")
        self.tg.edit_html(chat_id, sent["message_id"],
                          f"<b>{esc(tool)}</b>: {label}\n<pre>{esc(str(detail)[:1000])}</pre>")
        return answer in ("yes", "always"), "Пользователь не разрешил это действие"

    # ------------------------------------------------------------- кнопки ---
    def on_callback(self, callback):
        user_id = callback["from"]["id"]
        if not self.state.account(user_id):
            return
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        kind, _, rest = data.partition(":")
        handler = getattr(self, "btn_" + kind, None)
        note = None
        if handler:
            try:
                note = handler(user_id, chat_id, message, rest)
            except (helper.HelperError, TelegramError, OSError) as exc:
                log.warning("button: %s", type(exc).__name__)
                note = f"Не получилось: {str(exc)[:150]}"
        self.tg.call("answerCallbackQuery", callback_query_id=callback["id"], text=note)

    def btn_p(self, user_id, _chat_id, _message, rest):
        request, _, answer = rest.partition(":")
        entry = self.pending.get(request)
        if not entry or entry["user"] != user_id:
            return "Запрос устарел"
        if answer == "always":
            self.state.allow_always(entry["key"])
        entry["answer"] = answer
        entry["event"].set()
        return None

    def btn_a(self, user_id, chat_id, message, rest):
        action, _, answer = rest.partition(":")
        path = config.HOME / "answers" / f"{re.sub(r'[^0-9a-f]', '', answer)}.md"
        if not path.exists():
            return "Ответ уже удалён"
        body = path.read_text(encoding="utf-8")
        if action == "file":
            self.tg.send_document(chat_id, f"answer-{answer}.md", body.encode())
        elif action == "html":
            self.tg.send_document(chat_id, f"answer-{answer}.html", render.to_html_page(body).encode())
        elif action in ("short", "long"):
            prompt = ("Перескажи свой последний ответ короче." if action == "short"
                      else "Объясни свой последний ответ подробнее.")
            info = self.state.message(chat_id, message.get("message_id"))
            placeholder = self.tg.send_html(chat_id, "<i>Думаю…</i>")
            self.converse(user_id, chat_id, prompt, Stream(self, chat_id, placeholder["message_id"]), info)
        return None

    # ------------------------------------------------------ простые команды ---
    def cmd_start(self, _user_id, chat_id, _rest):
        self.tg.send(chat_id, "Я на связи. Пиши вопрос. Шпаргалка: /help")

    def cmd_new(self, user_id, chat_id, _rest):
        self.state.update_account(user_id, session=None, fork_next=False)
        self.tg.send(chat_id, "Новый разговор.")

    def cmd_help(self, _user_id, chat_id, _rest):
        self.tg.send_html(chat_id, texts.HELP)
        self.tg.send_html(chat_id, texts.SAFETY)

    def cmd_project(self, user_id, chat_id, _rest):
        names = helper.data(["list-projects"])
        self.project_lists[user_id] = names
        rows = [[button("~/chat (по умолчанию)", "pj:chat")]]
        rows += [[button(name, f"pj:{i}")] for i, name in enumerate(names[:40])]
        note = "" if names else "\nПроектов пока нет: они создаются на ПК в ~/projects."
        self.tg.send(chat_id, "Выбери проект:" + note, reply_markup={"inline_keyboard": rows})

    def btn_pj(self, user_id, chat_id, _message, rest):
        if rest == "chat":
            cwd, label = str(config.CLAUDE_HOME / "chat"), "~/chat"
        else:
            names = self.project_lists.get(user_id) or []
            if not rest.isdigit() or int(rest) >= len(names):
                return "Список устарел"
            label = names[int(rest)]
            cwd = str(config.CLAUDE_HOME / "projects" / label)
        # Разговоры Claude привязаны к папке: новый проект — новый разговор.
        self.state.update_account(user_id, cwd=cwd, session=None, fork_next=False)
        self.tg.send(chat_id, f"Проект: {label}. Начат новый разговор.")
        return None

    def cmd_sessions(self, _user_id, chat_id, _rest):
        items = helper.data(["sessions", "10"])
        if not items:
            self.tg.send(chat_id, "Разговоров пока нет.")
            return
        lines, rows = [], []
        for number, item in enumerate(items, 1):
            where = "Telegram" if item["source"] == "telegram" else "терминал"
            when = time.strftime("%d.%m %H:%M", time.localtime(item["mtime"]))
            lines.append(f"{number}. {esc(item['title'])}\n   <i>{where}, {when}</i>")
            rows.append([button(f"Продолжить {number}", f"c:{item['id']}")])
        self.tg.send_html(chat_id, "<b>Последние разговоры</b>\n\n" + "\n".join(lines),
                          reply_markup={"inline_keyboard": rows})

    def btn_c(self, user_id, chat_id, _message, rest):
        item = next((i for i in helper.data(["sessions", "30"]) if i["id"] == rest), None)
        if not item:
            return "Разговор не найден"
        # Разговор может быть открыт в терминале, поэтому продолжаем его веткой.
        self.state.update_account(user_id, session=item["id"], cwd=item["cwd"], fork_next=True)
        self.tg.send_html(chat_id, f"Продолжаем: {esc(item['title'])}\nПиши следующее сообщение.")
        return None

    # -------------------------------------------------------------- задачи ---
    def cmd_run(self, user_id, chat_id, rest):
        if not rest:
            self.tg.send(chat_id, "Напиши задачу после команды: /run собери проект и прогони тесты")
            return
        if not helper.data(["auth-status"]).get("loggedIn"):
            self.tg.send_html(chat_id, texts.NOT_LOGGED_IN)  # иначе задача молча ждала бы входа
            return
        account = self.state.account(user_id)
        cwd = self.default_cwd(account)
        tasks = helper.data(["task-list"])
        busy = [t for t in tasks if t["cwd"] == cwd and t["state"] in ("working", "waiting")]
        if busy:
            request = secrets.token_hex(4)
            self.pending_runs[request] = (user_id, rest)
            names = ", ".join(esc(t["name"]) for t in busy)
            self.tg.send_html(chat_id, f"В этой папке уже идёт задача: {names}. Запустить ещё одну?",
                              reply_markup=keyboard((button("Запустить", f"rc:{request}"),
                                                     button("Отмена", f"rx:{request}"))))
            return
        self.start_task(user_id, chat_id, rest, tasks)

    def btn_rc(self, user_id, chat_id, _message, rest):
        entry = self.pending_runs.pop(rest, None)
        if not entry or entry[0] != user_id:
            return "Запрос устарел"
        self.start_task(user_id, chat_id, entry[1], helper.data(["task-list"]))
        return None

    def btn_rx(self, _user_id, _chat_id, _message, rest):
        self.pending_runs.pop(rest, None)
        return "Отменено"

    def start_task(self, user_id, chat_id, prompt, tasks):
        account = self.state.account(user_id)
        taken = {t["name"] for t in tasks}
        base = time.strftime("run-%H%M")
        name, number = base, 2
        while name in taken:
            name, number = f"{base}-{number}", number + 1
        helper.text(["task-start", name, self.default_cwd(account), account.get("model") or "opus"], stdin=prompt)
        sent = self.tg.send_html(chat_id, f"Задача <b>{name}</b> запущена.", reply_markup=self.task_buttons(name))
        self.state.remember_message(chat_id, sent["message_id"], kind="task", task=name)

    @staticmethod
    def task_buttons(name):
        return keyboard((button("Смотреть", f"w:{name}"), button("Стоп", f"s:{name}")))

    def cmd_tasks(self, _user_id, chat_id, _rest):
        tasks = helper.data(["task-list"])
        if not tasks:
            self.tg.send(chat_id, "Задач нет. Запустить: /run что сделать")
            return
        lines, rows = [], []
        for task in tasks:
            label = esc(texts.STATES.get(task["state"], task["state"]))
            lines.append(f"<b>{esc(task['name'])}</b>: {label}\n{esc(task['title'][:80])}".rstrip())
            if task["state"] == "interrupted":
                rows.append([button(f"Продолжить {task['name']}", f"r:{task['name']}")])
            else:
                rows.append([button(f"Смотреть {task['name']}", f"w:{task['name']}"),
                             button("Стоп", f"s:{task['name']}")])
        self.tg.send_html(chat_id, "\n\n".join(lines), reply_markup={"inline_keyboard": rows})

    def btn_r(self, _user_id, chat_id, _message, name):
        helper.text(["task-resume", name])
        sent = self.tg.send_html(chat_id, f"Задача <b>{esc(name)}</b> продолжена.", reply_markup=self.task_buttons(name))
        self.state.remember_message(chat_id, sent["message_id"], kind="task", task=name)
        return None

    def btn_s(self, _user_id, chat_id, _message, name):
        helper.text(["task-stop", name])
        self.tg.send_html(chat_id, f"Задача <b>{esc(name)}</b> остановлена.")
        return None

    def btn_k(self, _user_id, _chat_id, _message, rest):
        name, _, key = rest.rpartition(":")
        helper.text(["task-keys", name, key])
        return None

    def screen_html(self, name):
        try:
            screen = helper.text(["task-screen", name])
        except helper.HelperError:
            return None
        return f"<b>{esc(name)}</b>\n<pre>{esc(screen[-SCREEN_LIMIT:]) or '(пусто)'}</pre>"

    @staticmethod
    def screen_buttons(name):
        return keyboard([button(label, f"k:{name}:{key}") for label, key in
                         (("1", "1"), ("2", "2"), ("3", "3"), ("Enter", "enter"), ("Esc", "esc"))],
                        [button("Стоп", f"s:{name}")])

    def btn_w(self, _user_id, chat_id, _message, name):
        body = self.screen_html(name)
        if body is None:
            return "Задача уже завершена"
        sent = self.tg.send_html(chat_id, body, reply_markup=self.screen_buttons(name))
        message_id = sent["message_id"]
        self.state.remember_message(chat_id, message_id, kind="task", task=name)
        with self.lock:
            self.watches[(chat_id, name)] = message_id
        self.spawn(self.watch_screen, chat_id, name, message_id, body)
        return None

    def watch_screen(self, chat_id, name, message_id, shown):
        deadline = time.monotonic() + config.WATCH_DURATION
        while time.monotonic() < deadline and self.watches.get((chat_id, name)) == message_id:
            time.sleep(config.WATCH_INTERVAL)
            body = self.screen_html(name)
            if body is None:
                self.tg.edit_html(chat_id, message_id, shown + "\n<i>Задача завершена.</i>")
                break
            if body != shown:
                shown = body
                try:
                    self.tg.edit_html(chat_id, message_id, body, reply_markup=self.screen_buttons(name))
                except TelegramError as exc:
                    log.warning("screen edit: %s", exc)
        with self.lock:
            if self.watches.get((chat_id, name)) == message_id:
                del self.watches[(chat_id, name)]

    # ------------------------------------------------------------- события ---
    def events_loop(self):
        while not self.stopped.is_set():
            try:
                proc = self.watch_proc = helper.popen(["watch"], stdout=subprocess.PIPE,
                                                      stderr=subprocess.DEVNULL)
                for line in proc.stdout:
                    try:
                        self.on_event(json.loads(line))
                    except ValueError:
                        continue
                    except Exception:  # noqa: BLE001 — одно событие не рвёт поток
                        log.exception("event")
                proc.wait()
                proc.stdout.close()
            except OSError as exc:
                log.warning("watch: %s", exc)
            self.stopped.wait(3)

    def on_event(self, event):
        if event.get("type") == "hook":
            self.notify_task(event)
        elif event.get("type") == "outbox":
            self.send_outbox(event["path"], event.get("size", 0))

    def notify_task(self, event):
        name = str(event.get("task", ""))
        ready = event.get("event") == "Stop"
        head = "готово" if ready else "ждёт тебя"
        detail = event.get("answer") if ready else (event.get("message") or event.get("answer"))
        body = f"<b>{esc(name)}</b>: {head}"
        if detail:
            body += f"\n{esc(str(detail)[:300])}"
        for account in self.state.accounts().values():
            sent = self.tg.send_html(account["chat_id"], body,
                                     reply_markup=keyboard([button("Смотреть", f"w:{name}")]))
            self.state.remember_message(account["chat_id"], sent["message_id"], kind="task", task=name)

    def send_outbox(self, path, size):
        name = os.path.basename(path)
        accounts = list(self.state.accounts().values())
        if size > config.TG_UPLOAD_LIMIT:
            windows = "C:\\Claude\\outbox\\" + path.replace("/", "\\")
            for account in accounts:
                self.tg.send_html(account["chat_id"], f"Файл больше 50 МБ, в Telegram не отправить:\n"
                                                      f"<code>{esc(windows)}</code> ({human_size(size)})")
            return
        data = helper.run(["read-outbox", path], timeout=120)
        payload = data
        for account in accounts:
            sent = self.tg.send_document(account["chat_id"], name, payload)
            document = (sent or {}).get("document") or {}
            payload = document.get("file_id") or payload

    def alerts_loop(self):
        folder = config.HOME / "alerts"
        while not self.stopped.is_set():
            try:
                self.deliver_alerts(folder)
            except Exception:  # noqa: BLE001 — сторож важнее одной ошибки
                log.exception("alerts")
            time.sleep(5)

    def deliver_alerts(self, folder):
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.txt")):
            body = path.read_text(encoding="utf-8", errors="replace")
            for account in self.state.accounts().values():
                self.tg.send_html(account["chat_id"], f"⚠️ <b>Тревога</b>\n{esc(body[:1500])}\n\nЧто делать: /help")
            path.unlink(missing_ok=True)

    # ---------------------------------------------------------- устройства ---
    def device_view(self):
        keys = json.loads(helper.keys(["list"]) or "[]")
        accounts = self.state.accounts()
        lines = ["<b>Устройства</b>", "", "Telegram:"]
        rows = [[button("Добавить ПК", "dv:pc"), button("Добавить Telegram", "dv:tg")],
                [button("Телефон для VNC", "dv:vnc")]]
        for user_id, account in accounts.items():
            lines.append(f"• {esc(account['name'])}")
            if len(accounts) > 1:
                rows.append([button(f"Удалить {account['name']}"[:40], f"dd:t:{user_id}")])
        lines += ["", "Ключи SSH:"]
        for key in keys:
            label = key.get("comment") or key["fingerprint"][:20]
            lines.append(f"• {esc(label)}")
            rows.append([button(f"Удалить {label}"[:40], f"dd:k:{key['fingerprint']}")])
        if not keys:
            lines.append("пока нет")
        return "\n".join(lines), {"inline_keyboard": rows}

    def cmd_devices(self, _user_id, chat_id, _rest):
        body, markup = self.device_view()
        self.tg.send_html(chat_id, body, reply_markup=markup)

    def server_ip(self):
        try:
            with urllib.request.urlopen(config.GEO_URL, timeout=8) as resp:
                info = json.load(resp)
            return info.get("ip") or "", info.get("country") or ""
        except (urllib.error.URLError, OSError, ValueError):
            return "", ""

    def add_pc_line(self):
        ip, _ = self.server_ip()
        with open(config.HOST_KEY, encoding="ascii") as f:
            parts = f.read().split()
        return texts.ADD_PC_LINE.format(raw=config.REPO_RAW, commit=config.commit(),
                                        ip=ip or "IP_СЕРВЕРА", host_key=" ".join(parts[:2]))

    def bind_link(self, code):
        name = self.tg.call("getMe").get("username", "")
        return esc(f"https://t.me/{name}?start={code}")

    def btn_dv(self, _user_id, chat_id, _message, rest):
        if rest == "pc":
            self.tg.send_html(chat_id, texts.ADD_PC.format(line=esc(self.add_pc_line())))
        elif rest == "tg":
            code = self.state.new_code(config.CODE_TTL)
            self.tg.send_html(chat_id, texts.ADD_TELEGRAM.format(code=code, link=self.bind_link(code)))
        elif rest == "vnc":
            ip, _ = self.server_ip()
            self.tg.send_html(chat_id, texts.PHONE_VNC.format(ip=esc(ip or "IP_СЕРВЕРА")))
        return None

    def btn_dd(self, user_id, chat_id, _message, rest):
        kind, _, target = rest.partition(":")
        if kind == "t":
            if len(self.state.accounts()) <= 1:
                return "Нельзя удалить последний аккаунт"
            self.state.remove_account(target)
            done = "Аккаунт удалён."
        else:
            helper.keys(["remove", target])
            done = "Ключ удалён, соединения этого устройства разорваны."
        self.tg.send(chat_id, done)
        if str(user_id) != target:
            self.cmd_devices(user_id, chat_id, "")
        return None

    def offer_key(self, user_id, chat_id, match):
        request = secrets.token_hex(4)
        self.pending_keys[request] = {"user": user_id, "type": match.group(1), "b64": match.group(2),
                                      "name": match.group(3) or ""}
        name = esc(match.group(3) or "без имени")
        self.tg.send_html(chat_id, f"Новый ключ SSH: <code>{name}</code>\nДобавить это устройство?",
                          reply_markup=keyboard((button("Подтвердить", f"kc:{request}"),
                                                 button("Отмена", f"kx:{request}"))))

    def btn_kc(self, user_id, chat_id, _message, rest):
        entry = self.pending_keys.pop(rest, None)
        if not entry or entry["user"] != user_id:
            return "Запрос устарел"
        helper.keys(["add", entry["type"], entry["b64"], entry["name"]])
        self.tg.send(chat_id, "Устройство добавлено. Вернись к компьютеру: скрипт продолжит сам.")
        return None

    def btn_kx(self, _user_id, _chat_id, _message, rest):
        self.pending_keys.pop(rest, None)
        return "Отменено"

    # -------------------------------------------------------------- статус ---
    def status_view(self, user_id):
        ip, country = self.server_ip()
        try:
            auth = helper.data(["auth-status"])
        except helper.HelperError:
            auth = {}
        disk = shutil.disk_usage("/")
        load = os.getloadavg()[0]
        model = self.state.account(user_id).get("model", "opus")
        org = auth.get("orgId") or "не определён"
        login = "вошёл" if auth.get("loggedIn") else "не вошёл"
        lines = [
            f"<b>Сервер</b>: {esc(ip or 'не определён')}, страна: {esc(country or 'не определена')}",
            f"<b>Claude</b>: {login}",
            f"<b>Organization ID</b>: <code>{esc(org)}</code>",
            f"<b>Диск</b>: занято {disk.used / 1024 ** 3:.1f} из {disk.total / 1024 ** 3:.0f} ГБ",
            f"<b>Загрузка</b>: {load:.2f}",
            f"<b>Режим</b>: {'Умно (opus)' if model == 'opus' else 'Быстро (sonnet)'}",
        ]
        markup = keyboard((button(("✓ " if model == "sonnet" else "") + "Быстро", "m:sonnet"),
                           button(("✓ " if model == "opus" else "") + "Умно", "m:opus")),
                          [button("Мои инструкции", "i:show")])
        return "\n".join(lines), markup

    def cmd_status(self, user_id, chat_id, _rest):
        body, markup = self.status_view(user_id)
        self.tg.send_html(chat_id, body, reply_markup=markup)

    def btn_m(self, user_id, chat_id, message, rest):
        if rest not in ("sonnet", "opus"):
            return None
        self.state.update_account(user_id, model=rest)
        body, markup = self.status_view(user_id)
        self.tg.edit_html(chat_id, message["message_id"], body, reply_markup=markup)
        return "Быстро" if rest == "sonnet" else "Умно"

    def btn_i(self, user_id, chat_id, _message, rest):
        if rest == "show":
            current = helper.text(["instructions-get"]).strip() or "(пусто)"
            self.tg.send_html(chat_id, f"<b>Мои инструкции</b>\n<pre>{esc(current[:3500])}</pre>",
                              reply_markup=keyboard((button("Изменить", "i:edit"), button("Очистить", "i:clear"))))
        elif rest == "edit":
            self.pending_input[user_id] = "instructions"
            self.tg.send(chat_id, "Пришли новый текст инструкций одним сообщением.")
        elif rest == "clear":
            helper.text(["instructions-set"], stdin="")
            self.tg.send(chat_id, "Инструкции очищены.")
        return None


def create():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    token = (config.HOME / "token").read_text(encoding="utf-8").strip()
    return Bot(Telegram(token, config.telegram_base()), State(config.HOME / "state.json"))
