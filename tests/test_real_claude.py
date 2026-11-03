"""The bot driving the real `claude` binary against a fake API. Skipped where claude is absent."""
import os
import shutil
import stat
import unittest

from tests.fake_anthropic import FakeAnthropic
from tests.harness import OWNER, BotCase

CLAUDE = shutil.which("claude")


@unittest.skipUnless(CLAUDE, "claude binary not installed")
class RealClaude(BotCase):
    def setUp(self):
        super().setUp()
        self.bind(OWNER)
        self.api = FakeAnthropic(tool_command=f"python3 -c \"open('{self.tmp}/ran','w')\"")
        self.addCleanup(self.api.close)
        wrapper = self.tmp / "real-claude"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'export PATH="{os.path.dirname(CLAUDE)}:$PATH" ANTHROPIC_BASE_URL={self.api.url} '
            "ANTHROPIC_API_KEY=sk-ant-fake CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1\n"
            "unset CLAUDE_CODE_OAUTH_TOKEN\n"
            f'exec "{CLAUDE}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        os.environ["CCVPS_CLAUDE_BIN"] = str(wrapper)

    def test_streamed_answer_and_context(self):
        self.message(text="первый вопрос")
        self.wait_text("Настоящий ответ после 0 ходов", 60)
        self.wait(lambda: not self.bot.active, 30, "finish")
        self.message(text="второй вопрос")
        self.wait_text("после 1 ходов", 60)

    def test_permission_button_allows_command(self):
        self.message(text="RUNTOOL сделай")
        self.wait_text("Claude просит разрешение", 60)
        self.wait(lambda: self.button_data("Разрешить"), 5, "buttons")
        self.assertFalse((self.tmp / "ran").exists())
        self.press(self.button_data("Разрешить"))
        self.wait(lambda: (self.tmp / "ran").exists(), 30, "command to run")
        self.wait_text("Настоящий ответ", 60)

    def test_permission_button_denies_command(self):
        self.message(text="RUNTOOL сделай")
        self.wait_text("Claude просит разрешение", 60)
        self.wait(lambda: self.button_data("Нет"), 5, "buttons")
        self.press(self.button_data("Нет"))
        self.wait_text("Настоящий ответ", 60)
        self.assertFalse((self.tmp / "ran").exists())

    def test_parallel_fork_answers_while_first_runs(self):
        self.message(text="SLOW первый")
        self.wait(lambda: any(r.started.is_set() for runs in self.bot.active.values() for r in runs), 60, "init")
        self.wait(lambda: self.api.requests, 30, "first request")
        self.message(text="SECOND второй")
        self.wait_text("Настоящий ответ", 60)
        self.assertEqual(sum("Настоящий" in t for t in self.fake.texts()), 1)  # the first is still waiting
        self.api.release.set()
        self.wait(lambda: sum("Настоящий" in t for t in self.fake.texts()) == 2, 60, "both answers")


if __name__ == "__main__":
    unittest.main()
