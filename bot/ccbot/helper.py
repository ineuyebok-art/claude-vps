"""Вызовы помощников: as-claude (от имени claude) и keys (root)."""
import json
import subprocess

from . import config


class HelperError(Exception):
    pass


def run(args, stdin=None, timeout=60):
    res = subprocess.run(config.AS_CLAUDE + args, input=stdin, capture_output=True,
                         timeout=timeout, check=False)
    if res.returncode != 0:
        raise HelperError(res.stderr.decode("utf-8", "replace").strip()[:300] or "ошибка помощника")
    return res.stdout


def text(args, stdin=None, timeout=60):
    data = stdin.encode() if isinstance(stdin, str) else stdin
    return run(args, data, timeout).decode("utf-8", "replace")


def data(args, timeout=60):
    return json.loads(run(args, None, timeout) or b"null")


def popen(args, **kwargs):
    return subprocess.Popen(config.AS_CLAUDE + args, **kwargs)


def keys(args):
    res = subprocess.run(config.KEYS + args, capture_output=True, text=True, timeout=30, check=False)
    if res.returncode != 0:
        raise HelperError(res.stderr.strip()[:300] or "ошибка ключей")
    return res.stdout


def write_inbox(name, stream):
    """Потоково пишет файл в ~/inbox. stream — объект с read(). Возвращает путь на сервере."""
    proc = popen(["write-inbox", name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE)
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            proc.stdin.write(chunk)
        proc.stdin.close()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    output = proc.stdout.read().decode("utf-8", "replace").strip()
    error = proc.stderr.read().decode("utf-8", "replace").strip()
    proc.stdout.close()
    proc.stderr.close()
    if proc.wait() != 0:
        raise HelperError(error[:300] or "не удалось сохранить файл")
    return output
