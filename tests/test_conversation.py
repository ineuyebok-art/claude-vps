import io
import json
import unittest
from email.message import Message
from unittest import mock

from tests.harness import OWNER, SECOND, STRANGER, BotCase
from ccbot import app, config, files


class Binding(BotCase):
    def test_first_code_binds_and_is_one_time(self):
        (self.bot_home / "bind-code").write_text("SECRET1234\n", encoding="utf-8")
        self.message(text="SECRET1234")
        self.wait(lambda: self.state.account(OWNER), what="binding")
        self.assertFalse((self.bot_home / "bind-code").exists())
        self.message(user=SECOND, text="SECRET1234")
        self.wait_text("привязан")
        self.assertIsNone(self.state.account(SECOND))

    def test_stranger_is_ignored_silently(self):
        (self.bot_home / "bind-code").write_text("SECRET1234", encoding="utf-8")
        for text in ("привет", "/status", "/devices", "wrong-code"):
            self.message(user=STRANGER, text=text)
        self.fake.push("callback_query", {"id": "x", "from": {"id": STRANGER}, "data": "dv:tg",
                                          "message": {"message_id": 1, "chat": {"id": STRANGER}}})
        self.bot.poll_once(0)
        import time
        time.sleep(0.3)
        self.assertEqual(self.fake.sent("sendMessage"), [])
        self.assertEqual(self.calls(), [])

    def test_group_chat_is_ignored(self):
        self.bind(OWNER)
        self.message(text="вопрос", chat={"id": -5, "type": "group"})
        import time
        time.sleep(0.2)
        self.assertEqual(self.calls(), [])

    def test_expired_code_refused(self):
        code = self.state.new_code(-1)
        self.message(user=SECOND, text=code)
        import time
        time.sleep(0.2)
        self.assertIsNone(self.state.account(SECOND))


class Conversation(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER, SECOND)

    def test_answer_appears_with_formatting_and_buttons(self):
        self.message(text="вопрос")
        self.wait_text("<b>вопрос</b>")
        self.wait(lambda: self.button_data("Красиво"), what="buttons")
        for label in ("Короче", "Подробнее", "Файлом", "Красиво"):
            self.assertIsNotNone(self.button_data(label))
        args = self.calls()[0]
        self.assertIn("--session-id", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertEqual(args[args.index("--model") + 1], "opus")

    def test_answer_is_streamed_by_editing_one_message(self):
        self.message(text="ДЛИННЫЙ ответ")
        self.wait_text("строка 199")
        edits = self.fake.sent("editMessageText")
        self.assertGreater(len(edits), 2)
        self.assertGreater(len(self.fake.sent("sendMessage")), 1)  # long answer spans messages
        for params in edits:
            self.assertLessEqual(len(params["text"]), 4096)

    def test_context_is_kept_between_messages(self):
        self.message(text="первый")
        self.wait_text("Ответ 1:")
        self.wait(lambda: self.state.account(OWNER)["session"] and not self.bot.active, what="finish")
        self.message(text="второй")
        self.wait_text("Ответ 2:")
        self.assertIn("--resume", self.calls()[1])

    def test_repeated_update_does_not_run_claude_twice(self):
        update = self.fake.push("message", {"message_id": 1, "from": {"id": OWNER}, "text": "раз",
                                            "chat": {"id": OWNER, "type": "private"}})
        self.bot.poll_once(0)
        self.wait_text("Ответ 1:")
        with self.fake.lock:
            self.fake.updates.append(update)
        self.bot.poll_once(0)
        import time
        time.sleep(0.3)
        self.assertEqual(len(self.calls()), 1)

    def test_long_polling_is_really_long(self):
        self.bot.poll_once(7)
        params = self.fake.sent("getUpdates")[-1]
        self.assertEqual(params["timeout"], 7)  # without it the bot would hammer Telegram

    def test_cyrillic_document_name_is_sent_raw(self):
        self.bot.tg.send_document(OWNER, "отчёт.txt", b"x")
        self.assertEqual(self.fake.sent("sendDocument")[0]["document_name"], "отчёт.txt")

    def test_seen_updates_survive_restart(self):
        self.state.seen_update(77)
        again = type(self.state)(self.state.path)
        self.assertTrue(again.seen_update(77))
        self.assertEqual(again.data["offset"], 78)

    def test_parallel_message_forks_and_both_answer(self):
        self.message(text="МЕДЛЕННО первый")
        self.wait(lambda: self.calls(), what="first run")
        self.wait(lambda: any(r.started.is_set() for runs in self.bot.active.values() for r in runs), what="init")
        self.message(text="второй")
        self.wait_text("Ответ 2:", timeout=5)  # the fork answers while the first still runs
        self.assertFalse(any("Ответ 1:" in t for t in self.fake.texts()))
        args = self.calls()[1]
        self.assertIn("--fork-session", args)
        self.assertIn("--resume", args)
        (self.stub / "release").touch()
        self.wait_text("Ответ 1:")

    def test_failed_fork_falls_back_to_queue(self):
        self.message(text="МЕДЛЕННО первый")
        self.wait(lambda: any(r.started.is_set() for runs in self.bot.active.values() for r in runs), what="init")
        self.message(text="ПАДЕНИЕВЕТКИ второй")
        self.wait(lambda: len(self.calls()) >= 2, what="fork attempt")
        (self.stub / "release").touch()
        self.wait_text("Ответ 2:")
        self.assertNotIn("--fork-session", self.calls()[2])

    def test_reply_goes_to_that_conversation(self):
        self.message(text="разговор А")
        self.wait_text("разговор А</b>")
        self.wait(lambda: not self.bot.active, what="finish")
        first_session = self.state.account(OWNER)["session"]
        target = next(i for i, m in self.fake.messages.items() if "разговор А" in m.get("text", ""))
        self.message(text="/new")
        self.wait_text("Новый разговор")
        self.message(text="разговор Б")
        self.wait_text("разговор Б</b>")
        self.wait(lambda: not self.bot.active, what="finish")
        self.message(text="к первому", reply_to_message={"message_id": target})
        self.wait_text("Ответ 2:")
        args = self.calls()[-1]
        self.assertEqual(args[args.index("--resume") + 1], first_session)

    def test_new_starts_fresh(self):
        self.message(text="раз")
        self.wait_text("Ответ 1:")
        self.wait(lambda: not self.bot.active, what="finish")
        self.message(text="/new")
        self.wait_text("Новый разговор")
        self.message(text="два")
        self.wait(lambda: len(self.calls()) == 2, what="second run")
        self.assertNotIn("--resume", self.calls()[1])

    def test_two_accounts_have_separate_conversations(self):
        self.message(text="мой вопрос")
        self.wait_text("Ответ 1:")
        self.wait(lambda: not self.bot.active, what="finish")
        self.message(user=SECOND, text="чужой вопрос")
        self.wait(lambda: len(self.calls()) == 2, what="second")
        self.assertNotIn("--resume", self.calls()[1])
        self.assertNotEqual(self.state.account(OWNER)["session"], self.state.account(SECOND)["session"])

    def test_error_is_reported_without_buttons(self):
        self.message(text="ОШИБКА")
        self.wait_text("Claude не ответил")

    def test_not_logged_in_points_to_desktop(self):
        self.message(text="ВХОД")
        self.wait_text("ещё не вошёл")

    def test_shorter_button_continues_conversation(self):
        self.message(text="вопрос")
        self.wait(lambda: self.button_data("Короче"), what="buttons")
        answer_id = next(i for i, m in self.fake.messages.items() if m.get("reply_markup"))
        self.wait(lambda: not self.bot.active, what="finish")
        self.press(self.button_data("Короче"), message_id=answer_id)
        self.wait_text("Перескажи свой последний ответ короче")
        self.assertIn("--resume", self.calls()[1])

    def test_file_button_sends_markdown(self):
        self.message(text="вопрос")
        self.wait(lambda: self.button_data("Файлом"), what="buttons")
        self.press(self.button_data("Файлом"))
        sent = self.wait(lambda: self.fake.sent("sendDocument"), what="document")
        self.assertTrue(sent[0]["document_name"].endswith(".md"))
        self.assertIn("вопрос".encode(), sent[0]["document_bytes"])

    def test_pretty_button_sends_offline_html(self):
        self.message(text="вопрос")
        self.wait(lambda: self.button_data("Красиво"), what="buttons")
        self.press(self.button_data("Красиво"))
        sent = self.wait(lambda: self.fake.sent("sendDocument"), what="document")
        self.assertTrue(sent[0]["document_name"].endswith(".html"))
        self.assertIn(b"renderMathInElement", sent[0]["document_bytes"])
        self.assertNotRegex(sent[0]["document_bytes"].decode(), r'src=["\']https?:')


class Attachments(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER)

    def prompt(self, index=0):
        return json.loads((self.stub / "last_prompt").read_text()) if False else self.fake.texts()

    def test_photo_is_saved_to_inbox_and_passed(self):
        self.fake.files["big"] = b"JPEGDATA"
        self.message(caption="что на фото?", photo=[
            {"file_id": "small", "file_unique_id": "s1"}, {"file_id": "big", "file_unique_id": "abcdef"}])
        self.wait_text("Ответ 1:")
        saved = list((self.home / "inbox").iterdir())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b"JPEGDATA")
        self.assertEqual(saved[0].stat().st_mode & 0o111, 0)  # incoming files are not executable
        self.wait_text("что на фото?")
        self.wait_text(saved[0].name.replace("_", "")[:5])

    def test_document_name_is_sanitized_and_not_overwritten(self):
        self.fake.files["d1"] = b"one"
        self.fake.files["d2"] = b"two"
        document = {"file_id": "d1", "file_unique_id": "u1", "file_name": "../../.ssh/authorized_keys"}
        self.message(document=document)
        self.wait_text("Ответ 1:")
        self.wait(lambda: not self.bot.active, what="finish")
        self.message(document={**document, "file_id": "d2"})
        self.wait_text("Ответ 2:")
        names = sorted(p.name for p in (self.home / "inbox").iterdir())
        self.assertEqual(names, ["authorized_keys", "authorized_keys-1"])
        self.assertFalse((self.home / ".ssh").exists())

    def test_file_over_20mb_is_refused_without_download(self):
        self.message(document={"file_id": "huge", "file_unique_id": "u", "file_name": "big.zip",
                               "file_size": config.TG_DOWNLOAD_LIMIT + 1})
        self.wait_text("больше 20 МБ")
        self.assertEqual(self.fake.sent("getFile"), [])
        self.assertEqual(self.calls(), [])

    def test_file_over_20mb_with_question_still_answers(self):
        self.message(caption="вопрос", document={"file_id": "huge", "file_unique_id": "u", "file_name": "b.zip",
                                                 "file_size": config.TG_DOWNLOAD_LIMIT + 1})
        self.wait_text("больше 20 МБ")
        self.wait_text("Ответ 1:")

    def test_album_is_one_question(self):
        self.fake.files.update(a=b"1", b=b"2")
        self.message(caption="сравни", media_group_id="g1", photo=[{"file_id": "a", "file_unique_id": "aaaaaa"}])
        self.message(media_group_id="g1", photo=[{"file_id": "b", "file_unique_id": "bbbbbb"}])
        self.wait_text("Ответ 1:")
        import time
        time.sleep(0.3)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(list((self.home / "inbox").iterdir())), 2)

    def test_forwarded_post_keeps_source(self):
        self.message(text="текст поста", forward_origin={"type": "channel", "chat": {"title": "Канал"}})
        self.wait_text("Пересланный пост")

    def test_voice_is_declined(self):
        self.message(voice={"file_id": "v", "file_unique_id": "v"})
        self.wait_text("Голосовые")
        self.assertEqual(self.calls(), [])

    def test_foreign_link_is_not_downloaded(self):
        with mock.patch.object(files, "_open") as opened:
            self.message(text="вот файл https://evil.com/report.pdf")
            self.wait_text("Файлы скачиваю только")
            self.wait_text("Ответ 1:")
        opened.assert_not_called()
        self.assertEqual(list((self.home / "inbox").iterdir()), [])

    def test_disk_link_is_downloaded(self):
        response = io.BytesIO(b"PDFDATA")
        response.headers = Message()
        response.headers["Content-Disposition"] = 'attachment; filename="otchet maya.pdf"'
        response.headers["Content-Type"] = "application/pdf"
        response.geturl = lambda: "https://doc.googleusercontent.com/x"
        with mock.patch.object(files, "_open", return_value=response) as opened:
            self.message(text="разбери https://drive.google.com/file/d/abc123/view")
            self.wait_text("Ответ 1:")
        self.assertIn("id=abc123", opened.call_args[0][0])
        self.assertEqual((self.home / "inbox" / "otchet_maya.pdf").read_bytes(), b"PDFDATA")

    def test_disk_html_page_is_refused(self):
        response = io.BytesIO(b"<html>login</html>")
        response.headers = Message()
        response.headers["Content-Type"] = "text/html"
        response.geturl = lambda: "https://drive.google.com/x"
        with mock.patch.object(files, "_open", return_value=response):
            self.message(text="https://drive.google.com/file/d/abc/view")
            self.wait_text("Вместо файла диск отдал страницу")
        self.assertEqual(list((self.home / "inbox").iterdir()), [])


class Permissions(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER, SECOND)

    def ask(self):
        self.message(text="РАЗРЕШЕНИЕ")
        self.wait_text("Claude просит разрешение")
        self.wait(lambda: self.button_data("Разрешить"), what="permission buttons")

    def test_allow(self):
        self.ask()
        self.press(self.button_data("Разрешить"))
        self.wait_text("сделал")

    def test_deny(self):
        self.ask()
        self.press(self.button_data("Нет"))
        self.wait_text("не разрешили")

    def test_other_account_cannot_answer(self):
        self.ask()
        self.press(self.button_data("Разрешить"), user=SECOND)
        import time
        time.sleep(0.3)
        self.assertFalse(any("сделал" in t for t in self.fake.texts()))
        self.press(self.button_data("Нет"))
        self.wait_text("не разрешили")

    def test_always_skips_the_question_next_time(self):
        self.ask()
        self.press(self.button_data("Разрешать всегда"))
        self.wait_text("сделал")
        self.wait(lambda: not self.bot.active, what="finish")
        count = sum("Claude просит" in t for t in self.fake.texts())
        self.message(text="РАЗРЕШЕНИЕ снова")
        self.wait(lambda: sum("сделал" in t for t in self.fake.texts()) >= 2, what="second answer")
        self.assertEqual(sum("Claude просит" in t for t in self.fake.texts()), count)

    def test_always_is_per_command(self):
        self.assertEqual(app.Bot.permission_key("Bash", {"command": "rm x"}), "Bash:rm x")
        self.assertNotEqual(app.Bot.permission_key("Bash", {"command": "rm y"}), "Bash:rm x")

    def test_default_allowed_tools_exclude_bash_and_edits(self):
        self.message(text="вопрос")
        self.wait(lambda: self.calls(), what="run")
        args = self.calls()[0]
        allowed = args[args.index("--allowedTools") + 1].split(",")
        for tool in ("Read", "Grep", "Glob", "WebSearch", "WebFetch"):
            self.assertIn(tool, allowed)
        for tool in ("Bash", "Write", "Edit"):
            self.assertNotIn(tool, allowed)
        self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits")


if __name__ == "__main__":
    unittest.main()
