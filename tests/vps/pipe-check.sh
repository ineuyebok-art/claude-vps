#!/usr/bin/env bash
# The installer as a user runs it: piped into bash, token typed at a terminal.
set -u
NAME="$1"
fail=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=1; }
docker exec "$NAME" bash -c 'git config --global --add safe.directory /src; git config --global --add safe.directory /src/.git'
base=(-e CCVPS_TELEGRAM_API=http://127.0.0.1:18080 -e CCVPS_CLAUDE_INSTALLER=/src/tests/stubs/claude-install.sh
  -e CCVPS_CHROMIUM=/src/tests/stubs/chromium -e CCVPS_REPO=file:///src)

# Token is asked at the terminal although stdin is the script itself; a wrong one is asked again.
docker exec "$NAME" systemctl stop ccbot
docker exec "$NAME" rm -f /var/lib/ccbot/token
out="$(docker exec "${base[@]}" "$NAME" bash -c '
  (sleep 4; echo 123:BADTOKENBADTOKENBADTOKENBADTOKENBADTOKEN; sleep 2; echo 123:GOODTOKENGOODTOKENGOODTOKENGOODTOKEN) |
    script -qec "cat /src/install.sh | bash" /dev/null' 2>&1)"
if grep -q 'не принял этот токен' <<<"$out"; then ok "wrong token is asked again"; else bad "wrong token is asked again"; fi
if grep -q 'Готово' <<<"$out"; then ok "install completes through curl | bash"; else bad "install completes: $(tail -3 <<<"$out")"; fi
if docker exec "$NAME" grep -q GOODTOKEN /var/lib/ccbot/token; then ok "token typed at the terminal is saved"; else bad "token is saved"; fi
if grep -q GOODTOKEN <<<"$out"; then bad "token is not echoed"; else ok "token is not echoed"; fi
head="$(git rev-parse HEAD)"
if [[ "$(docker exec "$NAME" cat /etc/claude-vps/commit)" == "$head" ]]; then ok "installed commit is recorded for the add-PC line"; else bad "commit recorded"; fi
docker exec "$NAME" bash -c 'printf "123:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN\n" > /var/lib/ccbot/token; chown ccbot /var/lib/ccbot/token'
docker exec "$NAME" systemctl restart ccbot

# Clear refusals.
out="$(docker exec "$NAME" sudo -u claude bash /src/install.sh 2>&1)"
if grep -q 'Нужны права root' <<<"$out"; then ok "non-root run explains itself"; else bad "non-root: $out"; fi
docker exec "$NAME" cp /etc/os-release /tmp/os-release.bak
docker exec "$NAME" bash -c 'rm -f /etc/os-release; sed s/24.04/22.04/g /tmp/os-release.bak > /etc/os-release'
out="$(docker exec "$NAME" bash /src/install.sh 2>&1)"
docker exec "$NAME" bash -c 'rm -f /etc/os-release; cp /tmp/os-release.bak /etc/os-release'
if grep -q 'Нужна Ubuntu 24.04' <<<"$out"; then ok "wrong system explains itself"; else bad "wrong system: $out"; fi
exit "$fail"
