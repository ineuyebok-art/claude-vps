#!/usr/bin/env bash
# Real SSH logins from outside the container. Args: container name, IP.
set -u
NAME="$1"
IP="$2"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fail=0
SSH=(ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR)
ssh-keygen -q -t ed25519 -N '' -C test-pc -f "$tmp/k1"
ssh-keygen -q -t ed25519 -N '' -C planted -f "$tmp/k2"
expect() {
  local want="$1" desc="$2"
  shift 2
  if "$@" true >/dev/null 2>&1; then got=yes; else got=no; fi
  if [[ "$got" == "$want" ]]; then echo "  ok   $desc"; else echo "  FAIL $desc"; fail=1; fi
}
expect no "no login before a key is confirmed" "${SSH[@]}" -i "$tmp/k1" "claude@$IP"
read -r t b _ <"$tmp/k1.pub"
docker exec "$NAME" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys add "$t" "$b" test-pc >/dev/null
expect yes "login with a confirmed key" "${SSH[@]}" -i "$tmp/k1" "claude@$IP"
expect no "root is refused" "${SSH[@]}" -i "$tmp/k1" "root@$IP"
docker exec "$NAME" bash -c "install -d -o claude -m 700 /home/claude/.ssh; cat >/home/claude/.ssh/authorized_keys" <"$tmp/k2.pub"
docker exec "$NAME" chown claude /home/claude/.ssh/authorized_keys
expect no "key planted in ~/.ssh is ignored" "${SSH[@]}" -i "$tmp/k2" "claude@$IP"
docker exec "$NAME" rm -rf /home/claude/.ssh
expect no "password auth is refused" "${SSH[@]/BatchMode=yes/BatchMode=no}" -o PreferredAuthentications=password -o NumberOfPasswordPrompts=0 "claude@$IP"
out="$("${SSH[@]}" -i "$tmp/k1" -R 5999:127.0.0.1:22 -N -o ExitOnForwardFailure=yes "claude@$IP" 2>&1 & p=$!; sleep 3; kill "$p" 2>/dev/null; wait "$p" 2>/dev/null)"
if grep -qi 'forward\|denied\|failed' <<<"$out"; then echo "  ok   sshd refuses -R forwarding"; else echo "  FAIL sshd refuses -R forwarding: $out"; fail=1; fi
fp="$(ssh-keygen -lf "$tmp/k1.pub" | awk '{print $2}')"
docker exec "$NAME" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys remove "$fp"
expect no "removed key no longer logs in" "${SSH[@]}" -i "$tmp/k1" "claude@$IP"
exit "$fail"
