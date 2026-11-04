"""Tasks in real tmux: /run, /tasks, watching, stopping, hooks, outbox."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from tests.harness import OWNER, ROOT, SECOND, BotCase
from ccbot import config


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class TaskCase(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER, SECOND)
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "claude").symlink_to(ROOT / "tests/stubs/claude-stub.py")
        self.sock = str(self.home / ".cc/tmux.sock")
        self.addCleanup(self.tmux, "kill-server")
        self.addCleanup(self.bot.stop)

    def tmux(self, *args):
        return subprocess.run(["tmux", "-S", self.sock, *args], capture_output=True, text=True, check=False)

    def task_names(self):
        return self.tmux("list-sessions", "-F", "#{session_name}").stdout.split()

    def start(self, prompt="собери проект"):
        self.message(text=f"/run {prompt}")
        self.wait(self.task_names, what="tmux session")
        return self.task_names()[0]

    def screen(self, name):
        return self.tmux("capture-pane", "-p", "-t", f"={name}:").stdout

    def hook(self, event, task, **data):
        env = dict(os.environ, CC_TASK=task)
        subprocess.run([sys.executable, str(ROOT / "server/bin/cc-hook"), event], input=json.dumps(data).encode(),
                       env=env, check=True)


class Run(TaskCase):
    def test_run_starts_task_in_current_project(self):
        name = self.start()
        self.wait_text(f"{name}</b> запущена")
        self.wait(lambda: "STUB TUI" in self.screen(name), what="TUI")
        self.assertIn("собери проект", self.screen(name))
        path = self.tmux("display-message", "-p", "-t", f"={name}:", "#{pane_current_path}").stdout.strip()
        self.assertEqual(os.path.realpath(path), os.path.realpath(self.home / "chat"))

    def test_not_logged_in_refuses_instead_of_hanging(self):
        real = ROOT / "tests/stubs/claude-stub.py"
        link = self.home / ".local/bin/claude"
        link.unlink()
        link.write_text(f'#!/bin/sh\n[ "$1" = auth ] && echo \'{{"loggedIn": false}}\' && exit 1\nexec {real} "$@"\n',
                        encoding="utf-8")
        link.chmod(0o755)
        os.environ["CCVPS_CLAUDE_BIN"] = str(link)
        self.message(text="/run задача")
        self.wait_text("ещё не вошёл")
        self.assertEqual(self.task_names(), [])

    def test_run_without_text_explains(self):
        self.message(text="/run")
        self.wait_text("Напиши задачу")

    def test_warns_when_project_is_busy(self):
        self.start()
        self.message(text="/run ещё одна")
        self.wait_text("уже идёт задача")
        self.assertEqual(len(self.task_names()), 1)
        self.press(self.button_data("Запустить"))
        self.wait(lambda: len(self.task_names()) == 2, what="second task")

    def test_warning_can_be_cancelled(self):
        self.start()
        self.message(text="/run ещё одна")
        self.wait_text("уже идёт задача")
        self.press(self.button_data("Отмена"))
        time.sleep(0.2)
        self.assertEqual(len(self.task_names()), 1)

    def test_tasks_lists_states(self):
        name = self.start()
        self.message(text="/tasks")
        self.wait_text("работает")
        self.hook("Stop", name, session_id="s1")
        self.message(text="/tasks")
        self.wait_text(f"{name}</b>: готово")

    def test_tasks_empty(self):
        self.message(text="/tasks")
        self.wait_text("Задач нет")


class Watch(TaskCase):
    def test_watch_updates_keys_and_reply(self):
        name = self.start()
        self.wait(lambda: "STUB TUI" in self.screen(name), what="TUI")
        self.press(f"w:{name}")
        self.wait_text("STUB TUI")
        with self.fake.lock:
            screen_id, entry = next((i, m) for i, m in self.fake.messages.items() if "STUB TUI" in m.get("text", ""))
        self.assertTrue(self.button_data("Enter"))
        self.message(text="ответ из телефона", reply_to_message={"message_id": screen_id})
        self.wait_text("GOT ответ из телефона")  # the same message updates itself
        self.assertIn("GOT", self.fake.messages[screen_id]["text"])
        self.press(f"k:{name}:1")
        self.press(f"k:{name}:enter")
        self.wait_text("GOT 1")
        self.bot.watches.clear()

    def test_finished_task_cannot_be_watched(self):
        self.press("w:nosuchtask")
        time.sleep(0.2)
        answered = self.fake.sent("answerCallbackQuery")
        self.assertIn("завершена", answered[-1].get("text", ""))


class Stop(TaskCase):
    def alive(self, pid):
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
            return state != "Z"
        except OSError:
            return False

    def test_stop_kills_all_descendants_and_daemons(self):
        name = self.start("СПАВН дети")
        child_file, daemon_file = self.stub / "child", self.stub / "daemon"
        self.wait(lambda: child_file.exists() and daemon_file.exists() and daemon_file.read_text().strip(), what="children")
        time.sleep(0.2)
        child, daemon = int(child_file.read_text()), int(daemon_file.read_text())
        self.assertTrue(self.alive(child) and self.alive(daemon))
        self.press(f"s:{name}")
        self.wait_text("остановлена", 10)
        self.wait(lambda: not self.alive(child), 5, "child to die")
        self.wait(lambda: not self.alive(daemon), 5, "daemon to die")
        self.assertEqual(self.task_names(), [])
        self.message(text="/tasks")
        self.wait_text("Задач нет")


class Reboot(TaskCase):
    def test_interrupted_after_restart_and_continues_by_button(self):
        name = self.start()
        self.hook("UserPromptSubmit", name, session_id="sess-1", cwd=str(self.home / "chat"), prompt="собери проект")
        self.tmux("kill-server")  # what a reboot does to tmux
        self.message(text="/tasks")
        self.wait_text("прервана")
        self.assertEqual(self.task_names(), [])  # nothing restarts by itself
        self.press(self.button_data("Продолжить"))
        self.wait_text("продолжена")
        self.wait(lambda: "--resume" in self.screen(name), what="resumed TUI")
        self.assertIn("sess-1", self.screen(name))

    def test_finished_task_is_forgotten_after_restart(self):
        name = self.start()
        self.hook("Stop", name, session_id="s")
        self.tmux("kill-server")
        self.message(text="/tasks")
        self.wait_text("Задач нет")


class Notifications(TaskCase):
    def watch(self):
        thread = threading.Thread(target=self.bot.events_loop, daemon=True)
        thread.start()
        self.wait(lambda: self.bot.watch_proc, what="watch process")

    def test_ready_goes_to_both_accounts_with_answer_start(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Всё собрано, тесты зелёные."}]}}) + "\n", encoding="utf-8")
        self.watch()
        self.hook("Stop", "dev", session_id="s", transcript_path=str(transcript))
        self.wait_text("Всё собрано")
        self.wait(lambda: {m.get("chat_id") for m in self.fake.messages.values()} == {OWNER, SECOND}, what="both")
        self.assertEqual(sum("<b>dev</b>: готово" in t for t in self.fake.texts()), 2)
        self.assertEqual(self.button_data("Смотреть"), "w:dev")

    def test_waiting_notification(self):
        self.watch()
        self.hook("Notification", "dev", message="Claude needs your permission to use Bash")
        self.wait_text("ждёт тебя")
        self.wait_text("needs your permission")

    def test_idle_prompt_after_stop_is_not_repeated(self):
        self.watch()
        self.hook("Stop", "dev")
        self.hook("Notification", "dev", message="Claude is waiting for your input")
        self.wait_text("готово")
        time.sleep(0.4)
        self.assertFalse(any("ждёт тебя" in t for t in self.fake.texts()))

    def test_bot_runs_without_tmux_do_not_notify(self):
        env = {k: v for k, v in os.environ.items() if k not in ("CC_TASK", "TMUX")}
        subprocess.run([sys.executable, str(ROOT / "server/bin/cc-hook"), "Stop"], input=b"{}", env=env, check=True)
        self.assertEqual(list((self.home / ".cc").glob("events/*")), [])

    def test_html_in_task_text_is_escaped(self):
        self.watch()
        self.hook("Notification", "dev", message="<script>x</script>")
        self.wait_text("&lt;script&gt;")


class OutboxDelivery(TaskCase):
    def watch(self):
        thread = threading.Thread(target=self.bot.events_loop, daemon=True)
        thread.start()
        self.wait(lambda: self.bot.watch_proc, what="watch process")
        time.sleep(0.3)  # baseline taken

    def sent_names(self):
        return [p.get("document_name") or p.get("document") for p in self.fake.sent("sendDocument")]

    def test_new_file_goes_to_both_accounts_and_is_uploaded_once(self):
        self.watch()
        (self.home / "outbox/report.txt").write_text("готово", encoding="utf-8")
        self.wait(lambda: len(self.fake.sent("sendDocument")) == 2, what="two sends")
        first, second = self.fake.sent("sendDocument")
        self.assertEqual(first["document_name"], "report.txt")
        self.assertEqual(first["document_bytes"].decode(), "готово")
        self.assertTrue(second["document"].startswith("F"))  # reuses file_id

    def test_existing_files_are_not_resent_after_start(self):
        (self.home / "outbox/old.txt").write_text("старое", encoding="utf-8")
        self.watch()
        time.sleep(0.4)
        self.assertEqual(self.fake.sent("sendDocument"), [])

    def test_changed_file_is_sent_again(self):
        path = self.home / "outbox/a.txt"
        self.watch()
        path.write_text("1", encoding="utf-8")
        self.wait(lambda: len(self.fake.sent("sendDocument")) == 2, what="first")
        path.write_text("22", encoding="utf-8")
        self.wait(lambda: len(self.fake.sent("sendDocument")) == 4, what="second")

    def test_symlink_and_noise_are_ignored(self):
        self.watch()
        secret = self.tmp / "secret"
        secret.write_text("TOKEN", encoding="utf-8")
        outbox = self.home / "outbox"
        (outbox / "link.txt").symlink_to(secret)
        for name in ("~$отчёт.docx", ".hidden", "desktop.ini", "a.part", ".mutagen-temp"):
            (outbox / name).write_text("x", encoding="utf-8")
        (outbox / "real.txt").write_text("y", encoding="utf-8")
        self.wait(lambda: self.fake.sent("sendDocument"), what="real file")
        time.sleep(0.3)
        self.assertEqual({p.get("document_name") for p in self.fake.sent("sendDocument")} - {None}, {"real.txt"})

    def test_too_big_file_sends_path(self):
        saved = config.TG_UPLOAD_LIMIT
        self.addCleanup(lambda: setattr(config, "TG_UPLOAD_LIMIT", saved))
        config.TG_UPLOAD_LIMIT = 3
        self.watch()
        (self.home / "outbox/big.bin").write_text("12345", encoding="utf-8")
        self.wait_text("C:\\Claude\\outbox\\big.bin")
        self.assertEqual(self.fake.sent("sendDocument"), [])

    def test_file_still_growing_waits(self):
        self.watch()
        path = self.home / "outbox/grow.txt"
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(5):
                f.write("x")
                f.flush()
                time.sleep(0.05)
        self.wait(lambda: self.fake.sent("sendDocument"), what="send")
        self.assertEqual(self.fake.sent("sendDocument")[0]["document_bytes"], b"xxxxx")


class CcCommand(TaskCase):
    def run_cc(self, *args):
        env = dict(os.environ, HOME=str(self.home), TERM="xterm")
        env.pop("TMUX", None)
        command = " ".join([str(ROOT / "server/bin/cc"), *args])
        proc = subprocess.Popen(["script", "-qfc", command, "/dev/null"], env=env, cwd=self.home,
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    @unittest.skipUnless(shutil.which("script"), "script not installed")
    def test_cc_creates_and_reattaches_same_session(self):
        first = self.run_cc("dev")
        self.wait(lambda: "dev" in self.task_names(), what="session")
        self.wait(lambda: "STUB TUI" in self.screen("dev"), what="TUI")
        first.kill()  # closing the window keeps the session
        first.wait()
        time.sleep(0.2)
        self.assertIn("dev", self.task_names())
        self.run_cc("dev")
        time.sleep(0.3)
        self.assertEqual(self.task_names().count("dev"), 1)

    def test_cc_refuses_bad_name(self):
        res = subprocess.run([str(ROOT / "server/bin/cc"), "a;b"], capture_output=True, check=False)
        self.assertEqual(res.returncode, 2)

    @unittest.skipUnless(shutil.which("script"), "script not installed")
    def test_task_from_terminal_is_visible_in_bot(self):
        self.run_cc("dev")
        self.wait(lambda: "dev" in self.task_names(), what="session")
        self.message(text="/tasks")
        self.wait_text("<b>dev</b>")


if __name__ == "__main__":
    unittest.main()
