#!/usr/bin/env bash
# Real Mutagen between a Linux folder (standing in for C:\Claude) and the container over SSH.
set -u
NAME="$1"
IP="$2"
MUTAGEN=/opt/mutagen/mutagen
tmp="$(mktemp -d)"
export HOME="$tmp/home" MUTAGEN_DATA_DIRECTORY="$tmp/data"
mkdir -p "$HOME/.ssh" "$tmp/pc/inbox"
trap '"$MUTAGEN" daemon stop >/dev/null 2>&1; rm -rf "$tmp"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=1; }
on_server() { docker exec "$NAME" sudo -u claude -H bash -c "$1"; }

ssh-keygen -q -t ed25519 -N '' -C sync-pc -f "$HOME/.ssh/claude-vps"
cat >"$HOME/.ssh/config" <<CONF
Host claude-vps
    HostName $IP
    User claude
    IdentityFile $HOME/.ssh/claude-vps
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
CONF
# OpenSSH takes the home folder from the passwd entry, so the config goes in through wrappers.
mkdir "$tmp/bin"
for tool in ssh scp; do
  printf '#!/bin/sh\nexec /usr/bin/%s -F "%s" "$@"\n' "$tool" "$HOME/.ssh/config" >"$tmp/bin/$tool"
  chmod +x "$tmp/bin/$tool"
done
export MUTAGEN_SSH_PATH="$tmp/bin"
read -r t b _ <"$HOME/.ssh/claude-vps.pub"
fp="$(docker exec "$NAME" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys add "$t" "$b" sync-pc)"
"$MUTAGEN" daemon start
if timeout 90 "$MUTAGEN" sync create --name=claude-inbox --mode=two-way-safe --symlink-mode=ignore \
  '--ignore=~$*' --ignore=desktop.ini "$tmp/pc/inbox" claude-vps:/home/claude/inbox </dev/null; then
  ok "session created, agent installed over the same key"
else
  bad "session created"
fi
flush() { timeout 30 "$MUTAGEN" sync flush claude-inbox </dev/null >/dev/null 2>&1; }

echo 'с компьютера' >"$tmp/pc/inbox/отчёт май.txt"
touch "$tmp/pc/inbox/~\$lock.docx" "$tmp/pc/inbox/desktop.ini"
flush
if on_server 'grep -q "с компьютера" ~/inbox/"отчёт май.txt"'; then ok "PC to server, cyrillic name"; else bad "PC to server"; fi
if on_server 'ls -A ~/inbox | grep -qE "lock|desktop"'; then bad "ignored files stay on PC"; else ok "ignored files stay on PC"; fi

on_server 'echo "с сервера" > ~/inbox/server.txt; ln -s /etc/passwd ~/inbox/link'
flush
if grep -q 'с сервера' "$tmp/pc/inbox/server.txt"; then ok "server to PC"; else bad "server to PC"; fi
if [[ -e "$tmp/pc/inbox/link" ]]; then bad "symlink is not synced"; else ok "symlink is not synced"; fi
on_server 'rm ~/inbox/link'
flush

# two-way-safe: the same file changed on both sides stays intact on both.
# The session is paused so that both edits really happen before either side syncs.
"$MUTAGEN" sync pause claude-inbox
echo 'версия ПК' >"$tmp/pc/inbox/server.txt"
on_server 'echo "версия сервера" > ~/inbox/server.txt'
"$MUTAGEN" sync resume claude-inbox
flush
pc="$(cat "$tmp/pc/inbox/server.txt")"
srv="$(on_server 'cat ~/inbox/server.txt')"
if [[ "$pc" == 'версия ПК' && "$srv" == 'версия сервера' ]]; then ok "conflict loses nothing"; else bad "conflict: pc=$pc srv=$srv"; fi
if "$MUTAGEN" sync list claude-inbox | grep -qi conflict; then ok "conflict is reported"; else bad "conflict is reported"; fi
rm -f "$tmp/pc/inbox/server.txt"
on_server 'rm -f ~/inbox/server.txt'
flush

rm "$tmp/pc/inbox/отчёт май.txt"
flush
if on_server 'test -e ~/inbox/"отчёт май.txt"'; then bad "delete on PC deletes on server"; else ok "delete on PC deletes on server"; fi

docker exec "$NAME" bash -c 'systemctl stop ccbot; rm -f /var/lib/ccbot/alerts/*.txt; systemctl start claude-watch.service; sleep 1'
if docker exec "$NAME" pgrep -u claude -f mutagen-agent >/dev/null; then ok "mutagen agent is running"; else bad "mutagen agent is running"; fi
if docker exec "$NAME" bash -c 'ls /var/lib/ccbot/alerts/*.txt' >/dev/null 2>&1; then bad "mutagen agent raises no alert"; else ok "mutagen agent raises no alert"; fi
docker exec "$NAME" systemctl start ccbot

docker exec "$NAME" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys remove "$fp"
sleep 2
echo 'после удаления' >"$tmp/pc/inbox/late.txt"
timeout 12 "$MUTAGEN" sync flush claude-inbox </dev/null >/dev/null 2>&1
if on_server 'test -e ~/inbox/late.txt'; then bad "removed device can no longer sync"; else ok "removed device can no longer sync"; fi
exit "$fail"
