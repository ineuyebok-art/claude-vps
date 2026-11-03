#!/usr/bin/env bash
# End-to-end installer test in a clean Ubuntu 24.04 container with systemd and its own network.
# Needs Docker and debootstrap, so it is run by hand, not in CI.
set -euo pipefail
cd "$(dirname "$0")/../.."
IMAGE=ccvps-noble
NAME=ccvps-test
bash tests/vps/image.sh "$IMAGE"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --privileged --cgroupns=host --tmpfs /run:mode=755 --tmpfs /run/lock \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw -v "$PWD:/src:ro" "$IMAGE" /lib/systemd/systemd >/dev/null
for _ in $(seq 1 30); do
  s="$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)"
  [[ "$s" == running || "$s" == degraded ]] && break
  sleep 1
done
IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$NAME")"
docker exec "$NAME" bash -c 'apt-get update -qq && apt-get install -y -qq curl ca-certificates python3 >/dev/null 2>&1'
docker exec -d "$NAME" python3 /src/tests/stubs/telegram.py 18080
sleep 1
ENVS=(-e CCVPS_SRC=/src -e CCVPS_TELEGRAM_API=http://127.0.0.1:18080 -e CCVPS_TOKEN=123:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN
  -e CCVPS_CLAUDE_INSTALLER=/src/tests/stubs/claude-install.sh -e CCVPS_CHROMIUM=/src/tests/stubs/chromium)
echo "=== first run"
docker exec "${ENVS[@]}" "$NAME" bash /src/install.sh | tail -14
docker exec "$NAME" cp /etc/claude-vps/sudo-password /tmp/pw1
docker exec "$NAME" sudo -u claude -H tmux -S /home/claude/.cc/tmux.sock new-session -d -s keepme sleep 600
echo "=== second run"
docker exec "${ENVS[@]}" "$NAME" bash /src/install.sh | tail -8
docker exec "$NAME" sudo -u claude -H tmux -S /home/claude/.cc/tmux.sock has-session -t keepme
echo "  ok   running task survives an update"
docker exec "$NAME" sudo -u claude -H tmux -S /home/claude/.cc/tmux.sock kill-session -t keepme
echo "=== install as curl | bash"
bash tests/vps/pipe-check.sh "$NAME"
echo "=== checks"
docker cp tests/vps/checks.sh "$NAME:/tmp/checks.sh"
docker exec "$NAME" bash /tmp/checks.sh
docker cp "$NAME:/tmp/desktop.png" /tmp/claude-0/desktop.png 2>/dev/null || true
echo "=== external port scan of $IP"
python3 tests/vps/portscan.py "$IP"
echo "=== real ssh logins"
bash tests/vps/ssh-check.sh "$NAME" "$IP"
echo "=== mutagen over ssh"
bash tests/vps/mutagen-check.sh "$NAME" "$IP"
echo "=== real bot behind sudo"
docker exec "$NAME" python3 /src/tests/vps/bot-e2e.py
echo "=== reboot"
bash tests/vps/reboot-check.sh "$NAME"
