#!/usr/bin/env bash
# Runs inside the test container after install.sh.
# shellcheck disable=SC2016,SC2088,SC2317
set -u
fail=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=1; }
# check "description" command...      — command must succeed
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
# refuse "description" command...     — command must fail
refuse() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$d"; else ok "$d"; fi; }
c() { sudo -u claude -H bash -c "$1"; }

echo "-- users and services"
check "user claude exists" id claude
check "user ccbot exists" id ccbot
check "ccbot running" systemctl is-active ccbot
check "tmux server running" systemctl is-active claude-tmux
check "tmux nice is 10" bash -c '[ "$(ps -o ni= -p "$(systemctl show -p MainPID --value claude-tmux)" | tr -d " ")" = 10 ]'
check "update timer enabled" systemctl is-enabled claude-update.timer

echo "-- token isolation"
check "token mode 600 owner ccbot" bash -c '[ "$(stat -c "%a %U" /var/lib/ccbot/token)" = "600 ccbot" ]'
refuse "claude cannot read token" c 'cat /var/lib/ccbot/token'
refuse "claude cannot list bot home" c 'ls /var/lib/ccbot'
refuse "claude cannot read sudo password" c 'cat /etc/claude-vps/sudo-password'
check "sudo password mode 600" bash -c '[ "$(stat -c "%a %U" /etc/claude-vps/sudo-password)" = "600 root" ]'
refuse "token not in systemd unit" grep -rq TESTTOKEN /etc/systemd/system
refuse "token not in journal" bash -c 'journalctl -u ccbot --no-pager | grep -q TESTTOKEN'

echo "-- ssh keys"
check "keys owner root" bash -c '[ "$(stat -c "%U %a" /etc/ssh/authorized_keys/claude)" = "root 644" ]'
refuse "claude cannot write keys" c 'echo x >> /etc/ssh/authorized_keys/claude'
refuse "claude cannot replace keys dir" c 'mv /etc/ssh/authorized_keys /tmp/x'
check "sshd ignores ~/.ssh" bash -c 'sshd -T -C user=claude | grep -qi "^authorizedkeysfile /etc/ssh/authorized_keys/%u"'

echo "-- sudo"
refuse "claude has no passwordless sudo" c 'sudo -n true'
refuse "claude cannot run the keys helper" c 'sudo -n /usr/local/lib/claude-vps/keys list'
refuse "claude cannot sudo as ccbot" c 'sudo -n -u ccbot true'
check "sudo works with the password" bash -c 'install -m 644 /etc/claude-vps/sudo-password /tmp/pw; sudo -u claude -H bash -c "sudo -S -k true </tmp/pw"; r=$?; rm -f /tmp/pw; exit $r'
check "ccbot can use as-claude" sudo -u ccbot sudo -n -u claude /usr/local/lib/claude-vps/as-claude instructions-get
check "ccbot can use keys" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys list
refuse "ccbot cannot get a root shell" sudo -u ccbot sudo -n bash -c true
refuse "ccbot cannot run shell as claude" sudo -u ccbot sudo -n -u claude bash -c true

echo "-- ssh config"
d="$(sshd -T -C user=claude)"
for want in "passwordauthentication no" "permitrootlogin no" "kbdinteractiveauthentication no" \
  "allowusers claude" "allowagentforwarding no" "allowtcpforwarding local" "port 22"; do
  if grep -qi "^$want\$" <<<"$d"; then ok "sshd: $want"; else bad "sshd: $want"; fi
done

echo "-- firewall"
check "ufw active" bash -c 'ufw status | grep -q "Status: active"'
check "ufw default deny" bash -c 'ufw status verbose | grep -q "deny (incoming)"'
refuse "ufw allows nothing but 22" bash -c 'ufw status | grep ALLOW | grep -qv "^22/tcp"'

echo "-- files"
for d in inbox outbox chat projects; do
  check "~/$d owned by claude" bash -c "[ \"\$(stat -c %U /home/claude/$d)\" = claude ]"
done
check "CLAUDE.md has system part" grep -q 'claude-vps:system:start' /home/claude/.claude/CLAUDE.md
check "CLAUDE.md describes outbox" grep -q 'file:///C:/Claude/outbox' /home/claude/.claude/CLAUDE.md
check "settings has Stop hook" bash -c 'jq -e ".hooks.Stop" /home/claude/.claude/settings.json'
check "Claude Code installed" test -x /home/claude/.local/bin/claude
check "auto-upgrades on" grep -q 'Unattended-Upgrade "1"' /etc/apt/apt.conf.d/20auto-upgrades
check "second run kept sudo password" cmp /etc/claude-vps/sudo-password /tmp/pw1
check "claude-vps info works" bash -c 'claude-vps info | grep -q "Пароль sudo"'
check "cc starts a session" bash -c 'sudo -u claude -H tmux -S /home/claude/.cc/tmux.sock new-session -d -s probe sleep 30'
check "session survives under service" bash -c 'sudo -u claude -H tmux -S /home/claude/.cc/tmux.sock has-session -t probe'
exit "$fail"
