#!/usr/bin/env bash
# claude-vps: одна команда ставит Claude Code, Telegram-бота и рабочий стол на VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/ineuyebok-art/claude-vps/main/install.sh | sudo bash
#
# Повторный запуск — обновление: данные и вход в Claude не трогаются.
# Весь код лежит в main(), поэтому оборванная загрузка ничего не выполнит.

set -Eeuo pipefail

REPO_URL="https://github.com/ineuyebok-art/claude-vps.git"
OPT=/opt/claude-vps
ETC=/etc/claude-vps
LIB=/usr/local/lib/claude-vps
BOTHOME=/var/lib/ccbot
VARLIB=/var/lib/claude-vps
HOME_C=/home/claude
TG_API="${CCVPS_TELEGRAM_API:-https://api.telegram.org}"

# Для автотестов: готовый токен, локальный исходник и заглушки внешних загрузок.
TOKEN="${CCVPS_TOKEN:-}"
SRC="${CCVPS_SRC:-}"
CLAUDE_INSTALLER="${CCVPS_CLAUDE_INSTALLER:-https://claude.ai/install.sh}"
export CHROMIUM_MODE="${CCVPS_CHROMIUM:-ppa}"

step() { printf '\n\033[1;32m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die() {
  printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2
  exit 1
}
on_error() {
  printf '\033[1;31m[x]\033[0m Установка остановилась в строке %s (этап: %s).\n' "$1" "${CURRENT:-?}" >&2
  printf '    Причина — в выводе выше. После исправления запусти ту же команду ещё раз: она продолжит безопасно.\n' >&2
}
trap 'on_error $LINENO' ERR

as_claude() { sudo -u claude -H "$@"; }
rand_pw() { tr -dc 'a-zA-Z0-9' </dev/urandom | head -c "${1:-20}" || true; }

# ---------------------------------------------------------------- проверки ---
preflight() {
  CURRENT="проверка системы"
  [[ $EUID -eq 0 ]] || die "Нужны права root. Запусти команду через sudo."
  [[ -r /etc/os-release ]] || die "Не удалось определить систему: нет /etc/os-release."
  local id version pretty
  id="$(. /etc/os-release && echo "${ID:-}")"
  version="$(. /etc/os-release && echo "${VERSION_ID:-}")"
  pretty="$(. /etc/os-release && echo "${PRETTY_NAME:-неизвестно}")"
  [[ "$id" == ubuntu && "$version" == 24.04 ]] ||
    die "Нужна Ubuntu 24.04, а здесь: $pretty. Переустанови систему в панели хостера и запусти снова."
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
  if ! command -v curl >/dev/null || [[ ! -f /etc/ssl/certs/ca-certificates.crt ]]; then
    apt-get update -qq
    apt-get install -y -qq curl ca-certificates >/dev/null
  fi
  local free_kb
  free_kb="$(df --output=avail -k / | tail -1 | tr -d ' ')"
  [[ "$free_kb" -gt 8000000 ]] || die "На диске меньше 8 ГБ свободного места."
}

check_token() {
  curl -fsS --max-time 20 "$TG_API/bot$1/getMe" 2>/dev/null | grep -Eq '"ok": *true'
}

ask_token() {
  CURRENT="токен бота"
  if [[ -s "$BOTHOME/token" && -z "$TOKEN" ]]; then
    info "Токен бота уже сохранён, спрашивать не буду."
    return 0
  fi
  if [[ -n "$TOKEN" ]]; then
    check_token "$TOKEN" || die "Telegram не принял токен бота."
    return 0
  fi
  [[ -r /dev/tty ]] || die "Негде спросить токен: запусти команду в обычном терминале."
  local i
  for i in 1 2 3; do
    printf 'Токен бота от @BotFather: ' >/dev/tty
    IFS= read -rs TOKEN </dev/tty
    printf '\n' >/dev/tty
    TOKEN="${TOKEN//[[:space:]]/}"
    if [[ "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && check_token "$TOKEN"; then
      return 0
    fi
    warn "Telegram не принял этот токен (попытка $i из 3). Скопируй его из @BotFather целиком."
  done
  die "Токен не подошёл. Проверь его в @BotFather и запусти команду снова."
}

# ------------------------------------------------------------------ пакеты ---
install_packages() {
  CURRENT="пакеты"
  step "Ставлю системные пакеты"
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    tmux git jq python3 sudo ufw openssh-server openssh-client cron rsync procps \
    iproute2 unattended-upgrades ca-certificates curl locales >/dev/null
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
}

fetch_source() {
  CURRENT="загрузка кода"
  local script_dir="" tmp
  [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]:-}" ]] &&
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -z "$SRC" && -n "$script_dir" && -d "$script_dir/server" ]]; then
    SRC="$script_dir"
  fi
  if [[ -n "$SRC" ]]; then
    COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo dev)"
  else
    step "Скачиваю код"
    tmp="$(mktemp -d)"
    git clone -q --depth 1 "$REPO_URL" "$tmp/repo"
    SRC="$tmp/repo"
    COMMIT="$(git -C "$SRC" rev-parse HEAD)"
  fi
  install -d -m 755 "$OPT"
  rsync -a --delete --exclude .git --exclude tests "$SRC"/ "$OPT"/
  chown -R root:root "$OPT"
  chmod -R a+rX,go-w "$OPT"
  [[ -z "${tmp:-}" ]] || rm -rf "$tmp"
}

# ------------------------------------------------------------ пользователи ---
setup_users() {
  CURRENT="пользователи"
  step "Создаю пользователей claude и ccbot"
  id claude >/dev/null 2>&1 || useradd --create-home --shell /bin/bash claude
  usermod -aG sudo claude
  id ccbot >/dev/null 2>&1 ||
    useradd --system --create-home --home-dir "$BOTHOME" --shell /usr/sbin/nologin ccbot
  chmod 750 "$HOME_C"
  chmod 700 "$BOTHOME"
  install -d -m 755 -o root -g root "$ETC"
  install -d -m 700 -o root -g root "$VARLIB"
  if [[ ! -s "$ETC/sudo-password" ]]; then
    (umask 077 && rand_pw 20 >"$ETC/sudo-password")
    printf 'claude:%s\n' "$(cat "$ETC/sudo-password")" | chpasswd
  fi
  chown root:root "$ETC/sudo-password"
  chmod 600 "$ETC/sudo-password"
  printf '%s\n' "$COMMIT" >"$ETC/commit"
  chmod 644 "$ETC/commit"

  install -d -m 755 "$LIB"
  local f
  for f in as-claude keys cc-hook cc-run watch; do
    [[ -f "$OPT/server/bin/$f" ]] && install -m 755 -o root -g root "$OPT/server/bin/$f" "$LIB/$f"
  done
  install -m 755 -o root -g root "$OPT/server/bin/cc" /usr/local/bin/cc
  install -m 755 -o root -g root "$OPT/server/bin/claude-vps" /usr/local/bin/claude-vps
  install -m 644 -o root -g root "$OPT/server/tmux.conf" "$ETC/tmux.conf"

  local sudoers
  sudoers="$(mktemp)"
  cat >"$sudoers" <<EOF
# claude-vps. Бот действует от имени claude только через as-claude;
# менять SSH-ключи может только узкий помощник keys.
# Кэш sudo отключён: иначе вредный процесс ввёл бы команду в окно, где ты только что вводил пароль.
Defaults:claude timestamp_timeout=0
ccbot ALL=(claude) NOPASSWD: $LIB/as-claude
ccbot ALL=(root) NOPASSWD: $LIB/keys
EOF
  visudo -cqf "$sudoers" || die "Ошибка в правилах sudo."
  install -m 440 -o root -g root "$sudoers" /etc/sudoers.d/claude-vps
  rm -f "$sudoers"
}

# ------------------------------------------------------ Claude Code и файлы ---
setup_home() {
  CURRENT="папки и настройки"
  step "Настраиваю папки и CLAUDE.md"
  local d
  for d in inbox outbox chat projects .cc .cc/events .cc/status .cc/prompts .claude; do
    install -d -m 755 -o claude -g claude "$HOME_C/$d"
  done
  chmod 700 "$HOME_C/.cc" "$HOME_C/.claude"
  as_claude "$LIB/as-claude" claude-md-system <"$OPT/server/claude-md-system.md"

  # Хуки и папки: только свои ключи, настройки пользователя не трогаем.
  local settings="$HOME_C/.claude/settings.json" tmp hooks
  if ! jq -e . "$settings" >/dev/null 2>&1; then
    echo '{}' >"$settings"
  fi
  hooks="$LIB/cc-hook"
  tmp="$(mktemp)"
  jq --arg h "$hooks" --arg home "$HOME_C" '
    def hook($e): {hooks: [{type: "command", command: ($h + " " + $e)}]};
    .hooks.Stop = [hook("Stop")]
    | .hooks.Notification = [hook("Notification")]
    | .hooks.UserPromptSubmit = [hook("UserPromptSubmit")]
    | .hooks.PreToolUse = [hook("PreToolUse")]
    | .permissions.additionalDirectories =
        (((.permissions.additionalDirectories // []) + [$home + "/inbox", $home + "/outbox"]) | unique)
  ' "$settings" >"$tmp"
  install -m 600 -o claude -g claude "$tmp" "$settings"
  rm -f "$tmp"

  # Windows Terminal и VS Code не ищут ~/.local/bin: он нужен в PATH и для ssh host cmd.
  # shellcheck disable=SC2016 # $HOME раскроется в .bashrc
  local line='export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"'
  grep -qxF "$line" "$HOME_C/.bashrc" 2>/dev/null || {
    sed -i "1i $line" "$HOME_C/.bashrc"
    chown claude:claude "$HOME_C/.bashrc"
  }
}

install_claude_code() {
  CURRENT="Claude Code"
  if [[ -x "$HOME_C/.local/bin/claude" ]]; then
    info "Claude Code уже стоит, обновляется сам."
    return 0
  fi
  step "Ставлю Claude Code (официальный установщик)"
  local tmp
  tmp="$(mktemp -d)"
  chmod 755 "$tmp"
  if [[ "$CLAUDE_INSTALLER" == http* ]]; then
    curl -fsSL "$CLAUDE_INSTALLER" -o "$tmp/install.sh"
  else
    cp "$CLAUDE_INSTALLER" "$tmp/install.sh"
  fi
  chmod 755 "$tmp/install.sh"
  as_claude bash "$tmp/install.sh"
  rm -rf "$tmp"
  [[ -x "$HOME_C/.local/bin/claude" ]] || die "Claude Code не установился: см. вывод выше."
}

# ---------------------------------------------------------- рабочий стол ---
install_chromium() {
  # Chromium из PPA xtradeb: в Ubuntu 24.04 пакет chromium-browser — только обёртка над snap.
  if [[ "$CHROMIUM_MODE" == stub ]]; then
    install -m 755 "$SRC/tests/stubs/chromium" /usr/local/bin/chromium
    return 0
  fi
  if ! command -v chromium >/dev/null; then
    apt-get install -y -qq --no-install-recommends software-properties-common >/dev/null
    add-apt-repository -y -n ppa:xtradeb/apps >/dev/null
    cat >/etc/apt/preferences.d/claude-vps-chromium <<'EOF2'
Package: *
Pin: release o=LP-PPA-xtradeb-apps
Pin-Priority: 100

Package: chromium*
Pin: release o=LP-PPA-xtradeb-apps
Pin-Priority: 700
EOF2
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends chromium >/dev/null
  fi
  command -v chromium >/dev/null || die "Chromium не установился: см. вывод выше."

  # В Ubuntu 24.04 AppArmor запрещает песочнице Chromium создавать user namespace.
  local bin
  bin="$(dpkg -L chromium 2>/dev/null | grep -E '/chromium$' | while read -r f; do
    [[ -f "$f" && -x "$f" ]] && printf '%s\n' "$f"; done | grep -v '^/usr/bin' | head -1 || true)"
  if [[ -n "$bin" ]] && command -v apparmor_parser >/dev/null && [[ ! -e /etc/apparmor.d/chromium ]]; then
    cat >/etc/apparmor.d/claude-vps-chromium <<EOF2
abi <abi/4.0>,
include <tunables/global>
profile claude-vps-chromium $bin flags=(unconfined) {
  userns,
}
EOF2
    apparmor_parser -r /etc/apparmor.d/claude-vps-chromium || warn "AppArmor не принял профиль Chromium."
  fi
}

setup_desktop() {
  CURRENT="рабочий стол"
  step "Ставлю рабочий стол (пакетов много, это несколько минут)"
  apt-get install -y -qq --no-install-recommends \
    tigervnc-standalone-server tigervnc-common tigervnc-tools \
    xfce4 xfce4-terminal dbus-x11 dbus-user-session x11-xkb-utils x11-xserver-utils xauth \
    xdg-utils exo-utils adwaita-icon-theme fonts-dejavu fonts-noto-core fonts-noto-color-emoji >/dev/null
  install_chromium

  # Без пароля на диске VNC не стартует. Классический VNC читает только 8 знаков.
  install -d -m 700 -o claude -g claude "$HOME_C/.vnc"
  if [[ ! -s "$HOME_C/.vnc/password" ]]; then
    (umask 077 && rand_pw 8 >"$HOME_C/.vnc/password")
  fi
  vncpasswd -f <"$HOME_C/.vnc/password" >"$HOME_C/.vnc/passwd"
  chown claude:claude "$HOME_C/.vnc/password" "$HOME_C/.vnc/passwd"
  chmod 600 "$HOME_C/.vnc/password" "$HOME_C/.vnc/passwd"

  local d="$OPT/server/desktop" conf="$HOME_C/.config" xml
  install -m 755 -o root -g root "$OPT/server/bin/desktop-init" "$LIB/desktop-init"
  install -d -m 755 /etc/chromium/policies/managed /etc/chromium-browser/policies/managed
  install -m 644 "$d/chromium-policy.json" /etc/chromium/policies/managed/claude-vps.json
  install -m 644 "$d/chromium-policy.json" /etc/chromium-browser/policies/managed/claude-vps.json

  xml="$conf/xfce4/xfconf/xfce-perchannel-xml"
  as_claude install -d -m 755 "$conf" "$conf/autostart" "$conf/xfce4" "$conf/xfce4/xfconf" "$xml"
  as_claude install -m 644 "$d/claude-vps-init.desktop" "$d/claude-vps-chromium.desktop" \
    "$d/claude-vps-terminal.desktop" "$conf/autostart/"
  as_claude install -m 644 "$d/helpers.rc" "$conf/xfce4/helpers.rc"
  as_claude install -m 644 "$d/xfce4-session.xml" "$d/xfce4-power-manager.xml" "$xml/"
  # Готовая панель: иначе первый запуск спрашивает, какую панель создать.
  if [[ ! -f "$xml/xfce4-panel.xml" && -f /etc/xdg/xfce4/panel/default.xml ]]; then
    as_claude install -m 644 /etc/xdg/xfce4/panel/default.xml "$xml/xfce4-panel.xml"
  fi
}

# ------------------------------------------------------------------ службы ---
setup_services() {
  CURRENT="службы"
  step "Настраиваю службы"
  install -d -m 700 -o ccbot -g ccbot "$BOTHOME" "$BOTHOME/alerts"
  if [[ -n "$TOKEN" ]]; then
    (umask 077 && printf '%s\n' "$TOKEN" >"$BOTHOME/token")
  fi
  chown ccbot:ccbot "$BOTHOME/token"
  chmod 600 "$BOTHOME/token"
  if [[ "$TG_API" != https://api.telegram.org ]]; then
    printf '%s\n' "$TG_API" >"$ETC/telegram-api"
  fi

  local unit changed=0
  for unit in "$OPT"/server/systemd/*; do
    if ! cmp -s "$unit" "/etc/systemd/system/$(basename "$unit")"; then
      install -m 644 -o root -g root "$unit" /etc/systemd/system/
      changed=1
    fi
  done
  [[ $changed -eq 0 ]] || systemctl daemon-reload

  # claude-tmux не перезапускаем: иначе обновление прервёт идущие задачи.
  systemctl enable --now claude-tmux.service >/dev/null
  systemctl enable --now claude-update.timer claude-watch.timer >/dev/null
  systemctl enable --now claude-vnc.service claude-desktop.service >/dev/null
  systemctl enable ccbot.service >/dev/null
  systemctl restart ccbot.service
  sleep 2
  systemctl is-active --quiet ccbot.service || {
    journalctl -u ccbot -n 20 --no-pager | cut -c1-200 >&2
    die "Бот не запустился."
  }
}

make_bind_code() {
  # Код привязки первого аккаунта. После привязки бот удаляет файл.
  BIND_CODE=""
  if [[ -f "$BOTHOME/state.json" ]] && jq -e '.accounts | length > 0' "$BOTHOME/state.json" >/dev/null 2>&1; then
    return 0
  fi
  if [[ ! -s "$BOTHOME/bind-code" ]]; then
    (umask 077 && rand_pw 10 >"$BOTHOME/bind-code")
    chown ccbot:ccbot "$BOTHOME/bind-code"
  fi
  BIND_CODE="$(cat "$BOTHOME/bind-code")"
}

# ---------------------------------------------------------------- сеть, SSH ---
geo() {
  # $1 — -4 или -6. Печатает: IP, страна (или пусто).
  curl "$1" -fsS --max-time 8 https://ipinfo.io/json 2>/dev/null |
    jq -r '[.ip // "", .country // ""] | @tsv' 2>/dev/null || true
}

setup_ipv6() {
  CURRENT="IPv6"
  local v4 v6 c4 c6 conf=/etc/sysctl.d/60-claude-vps-ipv6.conf
  v4="$(geo -4)"
  c4="$(cut -f2 <<<"$v4")"
  v6="$(geo -6)"
  c6="$(cut -f2 <<<"$v6")"
  SERVER_IP="$(cut -f1 <<<"$v4")"
  SERVER_COUNTRY="$c4"
  # Один IP — одна страна: если IPv6 определяется иначе, отключаем его.
  if [[ -n "$c6" && "$c6" != "$c4" ]]; then
    warn "IPv6 сервера определяется как $c6, а IPv4 как $c4 — отключаю IPv6."
    printf 'net.ipv6.conf.all.disable_ipv6 = 1\nnet.ipv6.conf.default.disable_ipv6 = 1\n' >"$conf"
    sysctl -q -p "$conf" || true
  fi
}

setup_firewall() {
  CURRENT="firewall"
  step "Включаю firewall: открыт только порт 22"
  ufw --force reset >/dev/null
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow 22/tcp comment 'SSH' >/dev/null
  ufw --force enable >/dev/null
}

lock_ssh() {
  CURRENT="SSH"
  step "Закрываю SSH: только ключи, только пользователь claude"
  install -d -m 755 -o root -g root /etc/ssh/authorized_keys
  [[ -f /etc/ssh/authorized_keys/claude ]] || install -m 644 -o root -g root /dev/null /etc/ssh/authorized_keys/claude
  chown root:root /etc/ssh/authorized_keys/claude
  chmod 644 /etc/ssh/authorized_keys/claude

  # 00-: sshd берёт первое значение, а облачные образы кладут свои правила в 50-.
  cat >/etc/ssh/sshd_config.d/00-claude-vps.conf <<'EOF'
# claude-vps. Ключи добавляет только бот после «Подтвердить».
Port 22
AuthorizedKeysFile /etc/ssh/authorized_keys/%u
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers claude
AllowAgentForwarding no
AllowTcpForwarding local
X11Forwarding no
PermitTunnel no
GatewayPorts no
EOF
  install -d -m 755 -o root -g root /run/sshd
  sshd -t || die "Ошибка в настройках SSH, вход оставлен как был."
  systemctl enable ssh >/dev/null 2>&1 || true
  systemctl try-reload-or-restart ssh >/dev/null 2>&1 || systemctl restart ssh
}

summary() {
  CURRENT="итог"
  local bot_name
  bot_name="$(curl -fsS --max-time 15 "$TG_API/bot$(cat "$BOTHOME/token")/getMe" 2>/dev/null |
    jq -r '.result.username // empty' 2>/dev/null || true)"
  printf '\n\033[1;32mГотово.\033[0m\n'
  printf '  Сервер:     %s, страна: %s\n' "${SERVER_IP:-не определён}" "${SERVER_COUNTRY:-не определена}"
  printf '  Версия:     %s\n' "${COMMIT:0:12}"
  if [[ -n "$BIND_CODE" ]]; then
    printf '  Бот:        открой @%s и отправь код: \033[1m%s\033[0m\n' "${bot_name:-твой_бот}" "$BIND_CODE"
  else
    printf '  Бот:        уже привязан, @%s\n' "${bot_name:-твой_бот}"
  fi
  printf '  Пароль sudo: \033[1m%s\033[0m\n' "$(cat "$ETC/sudo-password")"
  printf '\nСохрани пароль sudo в менеджере паролей.\n'
  printf 'SSH теперь открыт только ключам: root и пароли выключены. Текущее окно не закроется.\n'
  printf 'Дальше: в боте /devices → «Добавить ПК». Если бот не отвечает — консоль в панели хостера,\n'
  printf 'логин claude и этот пароль.\n'
}

main() {
  preflight
  printf '\033[1mclaude-vps: установка\033[0m\n'
  ask_token
  install_packages
  fetch_source
  setup_users
  setup_home
  install_claude_code
  setup_desktop
  setup_services
  make_bind_code
  setup_ipv6
  setup_firewall
  lock_ssh
  systemctl start claude-watch.service
  summary
}

main "$@"
