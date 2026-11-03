#!/usr/bin/env python3
"""Stands in for `claude`. Speaks the stream-json protocol of `claude -p`.

The prompt picks the behaviour, see the keywords below. Conversation history is kept in
$STUB_DIR/sessions/<id>, so tests can check that context survives resume and fork.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

DIR = Path(os.environ["STUB_DIR"])


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def history_path(session):
    return DIR / "sessions" / session


def interactive(args):
    """`cc-run` starts the interactive TUI: echo lines so that tests can see the screen."""
    print("STUB TUI", *args, flush=True)
    if any("СПАВН" in a for a in args):
        script = "sleep 300 & echo $! > child; setsid nohup sleep 301 >/dev/null 2>&1 & echo $! > daemon; wait"
        subprocess.Popen(["bash", "-c", script], cwd=DIR)
    for line in sys.stdin:
        print("GOT", line.strip(), flush=True)


def main():
    args = sys.argv[1:]
    with open(DIR / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(args, ensure_ascii=False) + "\n")
    if args[:2] == ["auth", "status"]:
        emit({"loggedIn": True, "orgId": "org-123", "email": "me@example.com"})
        return 0
    if "-p" not in args:
        interactive(args)
        return 0
    session = str(uuid.uuid4())
    history = []
    if "--session-id" in args:
        session = args[args.index("--session-id") + 1]
    if "--resume" in args:
        parent = args[args.index("--resume") + 1]
        if not history_path(parent).exists():
            print(f"No conversation found with session ID: {parent}", file=sys.stderr)
            return 1
        history = history_path(parent).read_text(encoding="utf-8").splitlines()
        if "--fork-session" not in args:
            session = parent
    message = json.loads(sys.stdin.readline())
    prompt = message["message"]["content"]
    if "ПАДЕНИЕВЕТКИ" in prompt and "--fork-session" in args:
        print("fork failed", file=sys.stderr)
        return 1
    history.append(prompt.replace("\n", " "))
    history_path(session).parent.mkdir(exist_ok=True)
    history_path(session).write_text("\n".join(history), encoding="utf-8")
    emit({"type": "system", "subtype": "init", "session_id": session, "model": "stub"})
    if "ОШИБКА" in prompt:
        print("boom", file=sys.stderr)
        return 3
    if "ВХОД" in prompt:
        emit({"type": "result", "subtype": "success", "is_error": True, "session_id": session,
              "result": "Not logged in · Please run /login"})
        return 1
    if "МЕДЛЕННО" in prompt:
        deadline = time.time() + 10
        while time.time() < deadline and not (DIR / "release").exists():
            time.sleep(0.02)
    text = f"Ответ {len(history)}: **{prompt[:300]}**"
    if "РАЗРЕШЕНИЕ" in prompt:
        emit({"type": "control_request", "request_id": "r1",
              "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "rm -rf build"}}})
        reply = json.loads(sys.stdin.readline())
        behavior = reply["response"]["response"]["behavior"]
        text = "сделал" if behavior == "allow" else "не разрешили"
    if "ДЛИННЫЙ" in prompt:
        text = "\n".join(f"строка {i} " + "текст " * 10 for i in range(200))
    emit({"type": "stream_event", "event": {"type": "message_start"}, "session_id": session})
    for start in range(0, len(text), 400):
        emit({"type": "stream_event", "session_id": session, "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text[start:start + 400]}}})
        time.sleep(0.005)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, "session_id": session})
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": text})
    sys.stdin.read()  # the host closes stdin after the result
    return 0


sys.exit(main())
