#!/usr/bin/env python3
"""The real bot service, as ccbot, behind real sudo, with a fake Telegram and a stub claude.

Runs inside the test container as root. Covers what unit tests cannot: user separation,
streams and binary data through sudo, tmux under systemd.
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0, "/src")
from tests.harness import FakeTelegram  # noqa: E402

OWNER, STRANGER = 111, 999
HOME = pathlib.Path("/home/claude")
BOT = pathlib.Path("/var/lib/ccbot")
TOKEN = "123:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
failed = []
next_message = [0]


def ok(text):
    print(f"  ok   {text}", flush=True)


def bad(text):
    print(f"  FAIL {text}", flush=True)
    failed.append(text)


def check(condition, text):
    (ok if condition else bad)(text)


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=False, **kwargs)


def as_claude(command):
    return run("sudo", "-u", "claude", "-H", "bash", "-c", command)


fake = FakeTelegram(18090)
run("pkill", "-f", "tests/stubs/telegram.py")
pathlib.Path("/etc/claude-vps/telegram-api").write_text(fake.url + "\n", encoding="ascii")
stub = HOME / ".local/bin/claude"
stub.unlink(missing_ok=True)
shutil.copy("/src/tests/stubs/claude-stub.py", stub)
os.chown(stub, pathlib.Path(HOME).stat().st_uid, pathlib.Path(HOME).stat().st_gid)
stub.chmod(0o755)
for path in (BOT / "state.json",):
    path.unlink(missing_ok=True)
pathlib.Path(BOT / "bind-code").write_text("E2ECODE123\n", encoding="ascii")
run("chown", "ccbot:ccbot", str(BOT / "bind-code"))
run("systemctl", "restart", "ccbot")
os.environ.pop("X", None)


def wait(condition, what, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    bad(f"timed out: {what}. last texts: {fake.texts()[-3:]}")
    return None


def has_text(needle):
    return next((t for t in fake.texts() if needle in t), None)


def message(user=OWNER, text=None, **extra):
    next_message[0] += 1
    payload = {"message_id": next_message[0], "from": {"id": user, "first_name": "Ilya"},
               "chat": {"id": user, "type": "private"}, **extra}
    if text is not None:
        payload["text"] = text
    fake.push("message", payload)


def button(label):
    with fake.lock:
        for entry in reversed(list(fake.messages.values())):
            for row in (entry.get("reply_markup") or {}).get("inline_keyboard", []):
                for item in row:
                    if label in item["text"]:
                        return item["callback_data"]
    return None


def press(data, message_id=1):
    fake.push("callback_query", {"id": "cb", "from": {"id": OWNER}, "data": data,
                                 "message": {"message_id": message_id, "chat": {"id": OWNER}}})


wait(lambda: fake.sent("setMyCommands"), "bot menu")
ok("bot starts as ccbot and sets its menu")
check(run("ps", "-o", "user=", "-C", "python3").stdout.count("ccbot") >= 1, "bot process runs as ccbot")

message(STRANGER, "E2EWRONG")
message(OWNER, "E2ECODE123")
wait(lambda: has_text("аккаунт привязан"), "binding")
ok("first code binds the account")
check(not (BOT / "bind-code").exists(), "bind code is consumed")
check(oct((BOT / "state.json").stat().st_mode & 0o777) == "0o600", "state file is private")

message(text="первый вопрос")
wait(lambda: has_text("Ответ 1:"), "first answer")
wait(lambda: button("Красиво"), "buttons")
ok("question reaches claude through sudo and the answer is streamed")
message(text="второй вопрос")
wait(lambda: has_text("Ответ 2:"), "context")
ok("context is kept")

message(text="РАЗРЕШЕНИЕ")
wait(lambda: has_text("Claude просит разрешение"), "permission")
wait(lambda: button("Разрешить"), "permission button")
press(button("Разрешить"))
wait(lambda: has_text("сделал"), "allowed")
ok("permission round trip through sudo stdin and stdout")

blob = bytes(range(256)) * 4096  # 1 MB with every byte value
fake.files["pic.bin"] = blob
message(caption="что это", document={"file_id": "pic.bin", "file_unique_id": "u1", "file_name": "снимок экрана.bin"})
target = HOME / "inbox/снимок_экрана.bin"
wait(target.exists, "file in inbox")
wait(lambda: target.exists() and target.stat().st_size == len(blob), "complete file")
check(target.read_bytes() == blob, "binary file arrives intact through sudo")
info = target.stat()
check(info.st_uid == HOME.stat().st_uid, "incoming file belongs to claude")
check(not info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), "incoming file is not executable")
target.unlink()

outgoing = bytes(reversed(blob))
pathlib.Path("/tmp/out.bin").write_bytes(outgoing)
as_claude("cp /tmp/out.bin ~/outbox/result.bin")
sent = wait(lambda: fake.sent("sendDocument"), "outbox delivery")
if sent:
    check(sent[0].get("document_bytes") == outgoing, "binary outbox file reaches Telegram intact")
    check(sent[0].get("document_name") == "result.bin", "outbox file keeps its name")
as_claude("rm ~/outbox/result.bin")

message(text="/run СПАВН собери")
wait(lambda: has_text("запущена"), "task start")
name = (button("Смотреть") or "w:").split(":", 1)[1]
ok(f"task {name} started from the bot")
wait(lambda: (pathlib.Path("/home/claude/.stub/daemon").exists() and
              pathlib.Path("/home/claude/.stub/daemon").read_text().strip()), "task children")
time.sleep(0.3)
child = int(pathlib.Path("/home/claude/.stub/child").read_text())
daemon = int(pathlib.Path("/home/claude/.stub/daemon").read_text())
nice = run("ps", "-o", "ni=", "-p", str(child)).stdout.strip()
check(nice == "10", f"task runs with nice 10 (got {nice})")
bot_nice = run("bash", "-c", "ps -o ni= -p $(systemctl show -p MainPID --value ccbot)").stdout.strip()
check(bot_nice == "0", "bot runs with normal priority")
press(button("Смотреть"))
wait(lambda: has_text("STUB TUI"), "task screen")
ok("task screen reaches the phone")
press(f"s:{name}")
wait(lambda: has_text("остановлена"), "stop")
wait(lambda: not pathlib.Path(f"/proc/{child}").exists() and not pathlib.Path(f"/proc/{daemon}").exists(),
     "children to die")
ok("Stop kills children and daemons of the task")

subprocess.run(["sudo", "-u", "claude", "-H", "env", f"CC_TASK=dev", "/usr/local/lib/claude-vps/cc-hook", "Stop"],
               input=b'{"session_id": "s1"}', check=True)
wait(lambda: has_text("<b>dev</b>: готово"), "hook notification")
ok("hook notification reaches the bot")

message(text="/status")
wait(lambda: has_text("Organization ID"), "status")
check("org-123" in (has_text("Organization ID") or ""), "status shows the Organization ID")

keys_file = pathlib.Path("/etc/ssh/authorized_keys/claude")
before = keys_file.read_text(encoding="ascii")
pub = "/tmp/e2e-key"
run("rm", "-f", pub, pub + ".pub")
run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "e2e-laptop", "-f", pub)
message(text=pathlib.Path(pub + ".pub").read_text(encoding="ascii").strip())
wait(lambda: has_text("Новый ключ SSH"), "key offer")
check(keys_file.read_text(encoding="ascii") == before, "key is not added before confirmation")
press(button("Подтвердить"))
wait(lambda: has_text("Устройство добавлено"), "key added")
check("e2e-laptop" in keys_file.read_text(encoding="ascii"), "confirmed key reaches the root-owned file")
run("systemctl", "start", "claude-watch.service")
time.sleep(6)
check(not has_text("Тревога"), "a key added by the bot raises no alert")

with keys_file.open("a", encoding="ascii") as f:
    f.write("ssh-ed25519 AAAAmanual evil\n")
run("systemctl", "start", "claude-watch.service")
wait(lambda: has_text("Тревога"), "alert", 15)
ok("manual key reaches the phone as an alert")
keys_file.write_text(before, encoding="ascii")
run("systemctl", "start", "claude-watch.service")

everything = json.dumps([p for _, p in fake.calls], ensure_ascii=False, default=str)
check("TESTTOKEN" not in everything, "the token never appears in messages")
vnc = (HOME / ".vnc/password").read_text(encoding="ascii").strip()
check(vnc not in everything, "the VNC password never appears in messages")
journal = run("journalctl", "-u", "ccbot", "--no-pager").stdout
check("TESTTOKEN" not in journal, "the token never appears in the journal")
sudo_pw = pathlib.Path("/etc/claude-vps/sudo-password").read_text(encoding="ascii").strip()
check(sudo_pw not in everything and sudo_pw not in journal, "the sudo password appears nowhere")
strangers = [p for p in fake.sent("sendMessage") if p.get("chat_id") == STRANGER]
check(not strangers, "a stranger got no reply")

run("systemctl", "stop", "ccbot")
pathlib.Path("/etc/claude-vps/telegram-api").write_text("http://127.0.0.1:18080\n", encoding="ascii")
run("systemctl", "start", "ccbot")
sys.exit(1 if failed else 0)
