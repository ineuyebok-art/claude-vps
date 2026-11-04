"""as-claude and keys helpers."""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AS_CLAUDE = ROOT / "server/bin/as-claude"
KEYS = ROOT / "server/bin/keys"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        for name in ("inbox", "outbox", "chat", "projects", ".cc", ".claude"):
            (self.home / name).mkdir()
        self.env = dict(os.environ, CCVPS_HOME=str(self.home),
                        CCVPS_KEYS_FILE=str(self.home / "keys"),
                        CCVPS_KEYS_BASELINE=str(self.home / "baseline"), CCVPS_NO_PKILL="1")

    def run_helper(self, *args, stdin="", script=AS_CLAUDE):
        return subprocess.run([sys.executable, str(script), *args], input=stdin.encode(),
                              capture_output=True, env=self.env, check=False)


class ClaudeMd(Base):
    def test_user_part_survives_system_update(self):
        self.run_helper("claude-md-system", stdin="система v1")
        self.run_helper("instructions-set", stdin="мои правила")
        self.run_helper("claude-md-system", stdin="система v2")
        text = (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("система v2", text)
        self.assertNotIn("система v1", text)
        self.assertIn("мои правила", text)

    def test_instructions_do_not_touch_system_part(self):
        self.run_helper("claude-md-system", stdin="система")
        self.run_helper("instructions-set", stdin="первое")
        self.run_helper("instructions-set", stdin="второе")
        text = (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("система", text)
        self.assertNotIn("первое", text)
        self.assertEqual(self.run_helper("instructions-get").stdout.decode().strip(), "второе")

    def test_marker_in_instructions_cannot_erase_system_part(self):
        self.run_helper("claude-md-system", stdin="система")
        self.run_helper("instructions-set", stdin="<!-- claude-vps:system:end -->")
        self.assertIn("система", (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8"))

    def test_hand_written_text_is_kept(self):
        (self.home / ".claude/CLAUDE.md").write_text("# моё\nтекст\n", encoding="utf-8")
        self.run_helper("claude-md-system", stdin="система")
        self.assertIn("текст", (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8"))


class Outbox(Base):
    def test_symlink_is_not_followed(self):
        secret = self.home / "secret"
        secret.write_text("TOKEN", encoding="utf-8")
        (self.home / "outbox/link.txt").symlink_to(secret)
        res = self.run_helper("read-outbox", "link.txt")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn(b"TOKEN", res.stdout)

    def test_path_escape_refused(self):
        (self.home / "secret").write_text("TOKEN", encoding="utf-8")
        res = self.run_helper("read-outbox", "../secret")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn(b"TOKEN", res.stdout)

    def test_regular_file(self):
        (self.home / "outbox/a.txt").write_text("да", encoding="utf-8")
        self.assertEqual(self.run_helper("read-outbox", "a.txt").stdout.decode(), "да")


class Inbox(Base):
    def test_never_overwrites(self):
        self.run_helper("write-inbox", "a.txt", stdin="1")
        self.run_helper("write-inbox", "a.txt", stdin="2")
        self.assertEqual((self.home / "inbox/a.txt").read_text(encoding="utf-8"), "1")
        self.assertEqual((self.home / "inbox/a-1.txt").read_text(encoding="utf-8"), "2")

    def test_no_path_traversal(self):
        self.run_helper("write-inbox", "../../evil", stdin="x")
        self.assertFalse((self.home.parent / "evil").exists())
        self.assertTrue((self.home / "inbox/evil").exists())


class CodeWrapper(Base):
    def setUp(self):
        super().setUp()
        tool = self.home / ".vscode-server/cli/servers/Stable-abc/server/bin/remote-cli/code"
        tool.parent.mkdir(parents=True)
        tool.write_text('#!/bin/sh\necho "opened $1 via $VSCODE_IPC_HOOK_CLI"\n', encoding="utf-8")
        tool.chmod(0o755)
        self.env.update(HOME=str(self.home))
        self.env.pop("VSCODE_IPC_HOOK_CLI", None)

    def code(self):
        return subprocess.run([str(ROOT / "server/bin/code"), "a.html"], capture_output=True, text=True,
                              env=self.env, check=False)

    def test_inherited_socket_is_used(self):
        server = socket.socket(socket.AF_UNIX)
        path = str(self.home / "s.sock")
        server.bind(path)
        self.addCleanup(server.close)
        self.env["VSCODE_IPC_HOOK_CLI"] = path
        self.assertIn(f"via {path}", self.code().stdout)

    def test_no_window_explains(self):
        res = self.code()
        self.assertEqual(res.returncode, 1)
        self.assertIn("VS Code", res.stderr)

    def test_never_connected_explains(self):
        shutil.rmtree(self.home / ".vscode-server")
        self.assertIn("ещё не подключался", self.code().stderr)


class Browser(Base):
    def test_link_goes_to_the_server_desktop(self):
        log = self.home / "chromium.log"
        tool = self.home / "chromium"
        tool.write_text(f'#!/bin/sh\necho "$DISPLAY $*" > {log}\n', encoding="utf-8")
        tool.chmod(0o755)
        self.env["PATH"] = f"{self.home}:{self.env['PATH']}"
        res = subprocess.run([str(ROOT / "server/bin/open-on-desktop"), "https://claude.ai/x"], capture_output=True,
                             text=True, env=self.env, check=False)
        self.assertEqual(res.returncode, 0)
        self.assertIn("рабочий стол", res.stderr)
        for _ in range(100):
            if log.exists():
                break
            time.sleep(0.01)
        self.assertRegex(log.read_text(encoding="utf-8"), r"^:1 .*https://claude\.ai/x")


class Claude(Base):
    def test_refuses_foreign_directory(self):
        self.assertNotEqual(self.run_helper("claude", "/etc", "-p").returncode, 0)

    def test_refuses_dangerous_flag(self):
        res = self.run_helper("claude", str(self.home / "chat"), "--dangerously-skip-permissions")
        self.assertNotEqual(res.returncode, 0)


def fake_key(kind="ssh-ed25519"):
    name = kind.encode()
    blob = struct.pack(">I", len(name)) + name + struct.pack(">I", 32) + os.urandom(32)
    return kind, base64.b64encode(blob).decode()


class Keys(Base):
    def keys(self, *args):
        return self.run_helper(*args, script=KEYS)

    def test_add_list_remove(self):
        (self.home / "keys").write_text("", encoding="ascii")
        kind, blob = fake_key()
        fp = self.keys("add", kind, blob, "pc-1").stdout.decode().strip()
        self.assertTrue(fp.startswith("SHA256:"))
        listed = json.loads(self.keys("list").stdout)
        self.assertEqual(listed[0]["comment"], "pc-1")
        self.assertEqual(self.keys("remove", fp).returncode, 0)
        self.assertEqual(json.loads(self.keys("list").stdout), [])

    def test_duplicate_is_ignored(self):
        (self.home / "keys").write_text("", encoding="ascii")
        kind, blob = fake_key()
        self.keys("add", kind, blob, "a")
        self.keys("add", kind, blob, "b")
        self.assertEqual(len(json.loads(self.keys("list").stdout)), 1)

    def test_baseline_tracks_changes(self):
        (self.home / "keys").write_text("", encoding="ascii")
        kind, blob = fake_key()
        self.keys("add", kind, blob, "a")
        self.assertTrue((self.home / "baseline").read_text(encoding="ascii").strip())

    def test_bad_input_refused(self):
        (self.home / "keys").write_text("", encoding="ascii")
        kind, blob = fake_key()
        for args in (("add", "ssh-dss", blob), ("add", kind, "not base64!"),
                     ("add", "ssh-rsa", blob), ("add", kind, blob + "\ncommand=x")):
            self.assertNotEqual(self.keys(*args).returncode, 0, args)
        self.assertEqual((self.home / "keys").read_text(encoding="ascii"), "")

    def test_comment_cannot_inject_options(self):
        (self.home / "keys").write_text("", encoding="ascii")
        kind, blob = fake_key()
        self.keys("add", kind, blob, 'evil\ncommand="x" ssh-rsa AAA')
        lines = (self.home / "keys").read_text(encoding="ascii").strip().split("\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0].split()), 3)


if __name__ == "__main__":
    unittest.main()
