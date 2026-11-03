"""Пути и настройки бота. Переопределяются переменными окружения в тестах."""
import os
import shlex
from pathlib import Path

HOME = Path(os.environ.get("CCBOT_HOME", "/var/lib/ccbot"))
ETC = Path(os.environ.get("CCBOT_ETC", "/etc/claude-vps"))
LIB = "/usr/local/lib/claude-vps"
CLAUDE_HOME = Path(os.environ.get("CCVPS_HOME") or "/home/claude")
AS_CLAUDE = shlex.split(os.environ.get("CCBOT_AS_CLAUDE", f"sudo -n -H -u claude {LIB}/as-claude"))
KEYS = shlex.split(os.environ.get("CCBOT_KEYS", f"sudo -n {LIB}/keys"))
REPO_RAW = "https://raw.githubusercontent.com/ineuyebok-art/claude-vps"
GEO_URL = os.environ.get("CCBOT_GEO_URL", "https://ipinfo.io/json")
HOST_KEY = os.environ.get("CCBOT_HOST_KEY", "/etc/ssh/ssh_host_ed25519_key.pub")

TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024
TG_UPLOAD_LIMIT = 50 * 1024 * 1024
LINK_LIMIT = 2 * 1024 * 1024 * 1024
CODE_TTL = 15 * 60
PERMISSION_TIMEOUT = 30 * 60
EDIT_INTERVAL = float(os.environ.get("CCBOT_EDIT_INTERVAL", "1.2"))
ALBUM_WAIT = float(os.environ.get("CCBOT_ALBUM_WAIT", "1.2"))
WATCH_INTERVAL = float(os.environ.get("CCBOT_SCREEN_INTERVAL", "3"))
WATCH_DURATION = 15 * 60

# Без вопросов: чтение и поиск. Правки в проекте, inbox и outbox разрешает режим acceptEdits.
ALLOWED_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "NotebookRead"]


def telegram_base():
    override = ETC / "telegram-api"
    if override.exists():
        return override.read_text(encoding="utf-8").strip()
    return os.environ.get("CCBOT_TELEGRAM_API", "https://api.telegram.org")


def commit():
    try:
        return (ETC / "commit").read_text(encoding="utf-8").strip()
    except OSError:
        return "main"
