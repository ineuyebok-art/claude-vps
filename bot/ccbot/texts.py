"""Тексты бота."""

COMMANDS = [
    ("new", "новый разговор"),
    ("project", "выбрать проект"),
    ("sessions", "последние разговоры"),
    ("run", "долгая задача: /run что сделать"),
    ("tasks", "задачи: смотреть и останавливать"),
    ("devices", "устройства"),
    ("status", "сервер, вход в Claude, настройки"),
    ("help", "шпаргалка и безопасность"),
]

STATES = {"working": "работает", "waiting": "ждёт тебя", "done": "готово",
          "interrupted": "прервана", "idle": "открыта"}

WELCOME = (
    "Готово, аккаунт привязан.\n\n"
    "Пиши вопрос, присылай фото, файл или ссылку на диск.\n"
    "Сначала войди в Claude: рабочий стол сервера → терминал → <code>claude</code>.\n"
    "Подключить компьютер: /devices."
)

NOT_LOGGED_IN = (
    "Claude ещё не вошёл в аккаунт.\n"
    "Открой рабочий стол сервера → терминал → <code>claude</code>: вход откроется в том же Chromium."
)

ADD_PC = (
    "<b>Добавить ПК</b>\n\n"
    "1. Открой PowerShell на компьютере (Win+X → Terminal) и вставь строку ниже.\n"
    "2. Когда скрипт напишет, что ключ в буфере обмена, вставь его сюда и нажми «Подтвердить».\n"
    "3. Скрипт продолжит сам. Повторный запуск безопасен.\n\n"
    "<pre>{line}</pre>"
)

ADD_TELEGRAM = (
    "<b>Второй Telegram</b>\n\n"
    "Открой этого бота из другого аккаунта и отправь код:\n<code>{code}</code>\n\n"
    "Код одноразовый, действует 15 минут."
)

PHONE_VNC = (
    "<b>Рабочий стол на телефоне Android</b>\n\n"
    "1. Поставь bVNC из Google Play.\n"
    "2. Новое подключение → тип <b>VNC over SSH</b>.\n"
    "3. SSH-сервер: <code>{ip}</code>, порт <code>22</code>, пользователь <code>claude</code>.\n"
    "4. В bVNC включи вход по ключу, создай ключ (Generate) и скопируй публичную часть.\n"
    "5. Пришли этот ключ сюда и нажми «Подтвердить».\n"
    "6. VNC-сервер: <code>127.0.0.1</code>, порт <code>5901</code>.\n"
    "7. Пароль рабочего стола покажет команда <code>claude-vps info</code> в терминале на ПК: "
    "секреты бот в сообщениях не присылает."
)

HELP = (
    "<b>Шпаргалка</b>\n\n"
    "<b>В Telegram</b>: любое сообщение — вопрос. Reply на ответ бота — в тот же разговор. "
    "Пока Claude отвечает, можно спрашивать ещё: ответы идут параллельно.\n\n"
    "<b>В терминале</b>\n"
    "<code>@файл</code> — приложить файл\n"
    "<code>Ctrl+Alt+V</code> — вставить скриншот (на ПК)\n"
    "<code>Esc</code> — остановить, <code>Esc Esc</code> — назад\n"
    "<code>Shift+Tab</code> — режимы, в том числе «сначала план»\n"
    "<code>/resume</code> — прошлые разговоры, <code>/clear</code> — очистить\n"
    "<code>/model</code> — модель, <code>!команда</code> — команда оболочки\n"
    "<code>cc [имя]</code> — задача в tmux; закрыл окно — работает дальше\n"
    "<code>1/2/3</code> — ответы на вопросы о разрешениях\n"
    "Ссылки на файлы из <code>outbox</code>: Ctrl+клик."
)

SAFETY = (
    "<b>Безопасность</b>\n\n"
    "• VS Code и расширения — только из Microsoft Marketplace, не Cursor, VSCodium или Windsurf.\n"
    "• Поставь облачный пароль в Telegram.\n"
    "• За границей — местная SIM/eSIM или Wi-Fi, не роуминг.\n"
    "• Файл, удалённый в <code>C:\\Claude</code>, удаляется и на сервере.\n"
    "• При тревоге: удали устройство в /devices, выйди из claude.ai во всех сеансах, "
    "смени токен в консоли хостера: <code>sudo claude-vps token</code>. "
    "Консоль нужна, потому что обманутый Claude может перехватить пароль, введённый в его терминале.\n\n"
    "<b>Нельзя</b>: пользоваться Claude вне стран, где он доступен; прокси учётных данных "
    "(CLIProxyAPI и т.п.), модифицированный Claude Code, чужие и общие аккаунты; "
    "<code>--dangerously-skip-permissions</code>; VNC наружу; рабочий код и секреты работодателя на сервере."
)

ADD_PC_LINE = (
    "[Net.ServicePointManager]::SecurityProtocol='Tls12';"
    "$w=New-Object Net.WebClient;$w.Encoding=[Text.Encoding]::UTF8;"
    "$s=$w.DownloadString('{raw}/{commit}/windows/add-pc.ps1');"
    "& ([scriptblock]::Create($s.TrimStart([char]0xFEFF))) -Server '{ip}' -HostKey '{host_key}' -Commit '{commit}'"
)
