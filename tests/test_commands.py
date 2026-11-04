"""/devices, /status, modes, instructions, /help, /sessions, /project."""
import json
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.harness import OWNER, SECOND, STRANGER, BotCase
from tests.test_helpers import fake_key
from ccbot import config, texts


class Devices(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER)
        (self.tmp / "authorized_keys").write_text("", encoding="ascii")
        config.ETC.mkdir()
        (config.ETC / "commit").write_text("abc123def\n", encoding="ascii")

    def key_text(self, name="laptop"):
        kind, blob = fake_key()
        return f"{kind} {blob} {name}"

    def stored_keys(self):
        return (self.tmp / "authorized_keys").read_text(encoding="ascii")

    def test_menu_buttons(self):
        self.message(text="/devices")
        self.wait_text("Устройства")
        for label in ("Добавить ПК", "Добавить Telegram", "Телефон для VNC"):
            self.assertIsNotNone(self.button_data(label))

    def test_add_pc_line_is_pinned_to_commit_with_host_key(self):
        self.message(text="/devices")
        self.wait_text("Устройства")
        self.press("dv:pc")
        text = self.wait_text("Добавить ПК</b>")
        self.assertIn("/abc123def/windows/add-pc.ps1", text)
        self.assertIn("-Commit 'abc123def'", text)
        self.assertIn("ssh-ed25519 AAAAHOSTKEY", text)
        self.assertNotIn("root@host", text)

    def test_key_needs_confirmation(self):
        line = self.key_text()
        self.message(text=line)
        self.wait_text("Новый ключ SSH")
        self.assertEqual(self.stored_keys(), "")
        self.press(self.button_data("Подтвердить"))
        self.wait_text("Устройство добавлено")
        self.assertIn(line.split()[1], self.stored_keys())
        self.assertEqual(self.calls(), [])  # a key is not a question for Claude

    def test_key_can_be_cancelled(self):
        self.message(text=self.key_text())
        self.wait_text("Новый ключ SSH")
        self.press(self.button_data("Отмена"))
        time.sleep(0.2)
        self.assertEqual(self.stored_keys(), "")

    def test_other_account_cannot_confirm(self):
        self.bind(SECOND)
        self.message(text=self.key_text())
        self.wait_text("Новый ключ SSH")
        self.press(self.button_data("Подтвердить"), user=SECOND)
        time.sleep(0.2)
        self.assertEqual(self.stored_keys(), "")

    def test_stranger_key_is_ignored(self):
        self.message(user=STRANGER, text=self.key_text())
        time.sleep(0.2)
        self.assertEqual(self.fake.sent("sendMessage"), [])

    def test_key_is_listed_and_removed(self):
        self.message(text=self.key_text("ноутбук"))
        self.wait_text("Новый ключ SSH")
        self.press(self.button_data("Подтвердить"))
        self.wait_text("Устройство добавлено")
        self.message(text="/devices")
        self.wait(lambda: self.button_data("Удалить"), what="delete button")
        self.press(self.button_data("Удалить"))
        self.wait_text("Ключ удалён")
        self.assertEqual(self.stored_keys(), "")

    def test_add_telegram_code_is_one_time_and_expires(self):
        self.press("dv:tg")
        text = self.wait_text("Второй Telegram")
        code = text.split("<code>")[1].split("</code>")[0]
        self.assertIn("15 минут", text)
        self.assertIn(f"https://t.me/test_bot?start={code}", text)
        self.message(user=SECOND, text=code)
        self.wait(lambda: self.state.account(SECOND), what="binding")
        self.message(user=STRANGER, text=code)
        time.sleep(0.2)
        self.assertIsNone(self.state.account(STRANGER))
        self.assertEqual(config.CODE_TTL, 15 * 60)

    def test_remove_telegram_account(self):
        self.bind(SECOND)
        self.press(f"dd:t:{SECOND}")
        self.wait_text("Аккаунт удалён")
        self.assertIsNone(self.state.account(SECOND))
        self.message(user=SECOND, text="вопрос")
        time.sleep(0.2)
        self.assertEqual(self.calls(), [])

    def test_last_account_is_kept(self):
        self.press(f"dd:t:{OWNER}")
        time.sleep(0.2)
        self.assertIsNotNone(self.state.account(OWNER))

    def test_phone_vnc_instruction_has_no_password(self):
        (self.home / ".vnc").mkdir()
        (self.home / ".vnc/password").write_text("SECRETPW", encoding="ascii")
        self.press("dv:vnc")
        text = self.wait_text("bVNC")
        self.assertIn("5901", text)
        self.assertNotIn("SECRETPW", text)
        self.assertIn("claude-vps info", text)


class Status(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER, SECOND)

        class Geo(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                data = json.dumps({"ip": "203.0.113.7", "country": "FI"}).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = HTTPServer(("127.0.0.1", 0), Geo)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        config.GEO_URL = f"http://127.0.0.1:{server.server_port}/"

    def test_status_shows_everything(self):
        self.message(text="/status")
        text = self.wait_text("Organization ID")
        for part in ("203.0.113.7", "FI", "вошёл", "org-123", "Диск", "Загрузка"):
            self.assertIn(part, text)

    def test_fast_and_smart_are_per_account(self):
        self.message(text="/status")
        self.wait_text("Organization ID")
        self.press("m:sonnet", message_id=max(self.fake.messages))
        self.wait(lambda: self.state.account(OWNER)["model"] == "sonnet", what="model")
        self.assertEqual(self.state.account(SECOND)["model"], "opus")
        self.wait(lambda: self.button_data("✓ Быстро"), what="tick")
        self.message(text="вопрос")
        self.wait(lambda: self.calls() and "-p" in self.calls()[-1], what="run")
        args = [c for c in self.calls() if "-p" in c][-1]
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.wait_text("Ответ 1:")
        self.message(user=SECOND, text="вопрос 2")
        self.wait(lambda: len([c for c in self.calls() if "-p" in c]) == 2, what="second")
        args = [c for c in self.calls() if "-p" in c][-1]
        self.assertEqual(args[args.index("--model") + 1], "opus")

    def test_instructions_flow_keeps_system_part(self):
        subprocess.run(config.AS_CLAUDE + ["claude-md-system"], input=b"system text", check=True)
        self.press("i:show")
        self.wait_text("Мои инструкции")
        self.press(self.button_data("Изменить"))
        self.wait_text("Пришли новый текст")
        self.message(text="отвечай коротко")
        self.wait_text("Инструкции сохранены")
        text = (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("system text", text)
        self.assertIn("отвечай коротко", text)
        self.assertEqual([c for c in self.calls() if "-p" in c], [])  # not sent to Claude
        self.press("i:clear")
        self.wait_text("Инструкции очищены")
        text = (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("system text", text)
        self.assertNotIn("отвечай коротко", text)


class Sessions(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER)

    def write_session(self, sid, text, source=None, cwd=None):
        folder = self.home / ".claude/projects/-demo"
        folder.mkdir(parents=True, exist_ok=True)
        entry = {"type": "user", "isSidechain": False, "cwd": cwd or str(self.home / "projects/demo"),
                 "message": {"role": "user", "content": text}}
        if source:
            entry["promptSource"] = source
        (folder / f"{sid}.jsonl").write_text(
            json.dumps({"type": "queue-operation"}) + "\n" + json.dumps(entry) + "\n", encoding="utf-8")

    def test_lists_terminal_and_telegram_sessions(self):
        self.write_session("11111111-1111-1111-1111-111111111111", "из терминала")
        self.write_session("22222222-2222-2222-2222-222222222222", "из бота", source="sdk")
        self.message(text="/sessions")
        text = self.wait_text("Последние разговоры")
        self.assertIn("из терминала", text)
        self.assertIn("из бота", text)
        self.assertIn("Telegram", text)
        self.assertIn("терминал", text)

    def test_continue_forks_in_session_folder(self):
        sid = "11111111-1111-1111-1111-111111111111"
        self.write_session(sid, "начал на ПК")
        (self.stub / "sessions").mkdir()
        (self.stub / "sessions" / sid).write_text("начал на ПК", encoding="utf-8")
        self.message(text="/sessions")
        self.wait_text("Последние разговоры")
        self.press(f"c:{sid}")
        self.wait_text("Продолжаем")
        self.message(text="продолжаю с телефона")
        self.wait_text("Ответ 2:")  # the context from the PC is there
        args = self.calls()[-1]
        self.assertEqual(args[args.index("--resume") + 1], sid)
        self.assertIn("--fork-session", args)

    def test_no_sessions(self):
        self.message(text="/sessions")
        self.wait_text("Разговоров пока нет")


class Project(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER)

    def test_choose_project_starts_new_conversation_there(self):
        self.message(text="старт")
        self.wait_text("Ответ 1:")
        self.wait(lambda: not self.bot.active, what="finish")
        self.message(text="/project")
        self.wait_text("Выбери проект")
        self.press(self.button_data("demo"))
        self.wait_text("Проект: demo")
        self.assertIsNone(self.state.account(OWNER)["session"])
        self.message(text="в проекте")
        self.wait(lambda: len(self.calls()) == 2, what="run")
        self.wait_text("Ответ 1: <b>в проекте")
        account = self.state.account(OWNER)
        self.assertTrue(account["cwd"].endswith("projects/demo"))

    def test_default_is_chat(self):
        self.message(text="вопрос")
        self.wait_text("Ответ 1:")
        self.wait(lambda: self.state.account(OWNER)["cwd"], what="cwd")
        self.assertTrue(self.state.account(OWNER)["cwd"].endswith("/chat"))


class Help(BotCase):
    def test_help_has_cheatsheet_and_safety(self):
        self.bind(OWNER)
        self.message(text="/help")
        self.wait_text("Шпаргалка")
        self.wait_text("Безопасность")
        joined = "\n".join(self.fake.texts())
        for part in ("@файл", "Ctrl+Alt+V", "Esc", "Shift+Tab", "/resume", "/clear", "/model", "!команда",
                     "cc [имя]", "1/2/3", "Marketplace", "облачный пароль", "роуминг", "claude-vps token",
                     "--dangerously-skip-permissions", "VNC наружу"):
            self.assertIn(part, joined)
        for message in self.fake.sent("sendMessage"):
            self.assertLessEqual(len(message["text"]), 4096)

    def test_menu_is_set_up(self):
        self.bot.setup()
        commands = self.fake.sent("setMyCommands")[0]["commands"]
        self.assertEqual({c["command"] for c in commands}, {c for c, _ in texts.COMMANDS})
        self.assertEqual(len(commands), 8)


class Alerts(BotCase):
    def test_alert_reaches_all_accounts_and_is_consumed(self):
        self.bind(OWNER, SECOND)
        folder = self.bot_home / "alerts"
        folder.mkdir()
        (folder / "1.txt").write_text("Изменились SSH-ключи", encoding="utf-8")
        self.bot.deliver_alerts(folder)
        self.assertEqual(sum("Изменились SSH-ключи" in t for t in self.fake.texts()), 2)
        self.assertEqual(list(folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
