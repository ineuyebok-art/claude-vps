#!/usr/bin/env bash
# Restarts the container like a server reboot and checks what comes back.
set -u
NAME="$1"
fail=0
as_claude() { docker exec "$NAME" sudo -n -u claude -H /usr/local/lib/claude-vps/as-claude "$@"; }
docker exec -i "$NAME" sudo -n -u claude -H /usr/local/lib/claude-vps/as-claude task-start rebooted /home/claude/chat opus <<<'долгая задача'
docker restart "$NAME" >/dev/null
for _ in $(seq 1 40); do
  s="$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)"
  [[ "$s" == running || "$s" == degraded ]] && break
  sleep 1
done
for unit in ccbot claude-tmux claude-vnc claude-desktop; do
  if docker exec "$NAME" systemctl is-active --quiet "$unit"; then echo "  ok   $unit is back"; else echo "  FAIL $unit is back"; fail=1; fi
done
for unit in claude-watch.timer claude-update.timer; do
  if docker exec "$NAME" systemctl is-active --quiet "$unit"; then echo "  ok   $unit is back"; else echo "  FAIL $unit is back"; fail=1; fi
done
if docker exec "$NAME" ufw status | grep -q 'Status: active'; then echo "  ok   firewall is back"; else echo "  FAIL firewall is back"; fail=1; fi
sleep 3
state="$(as_claude task-list | jq -r '.[] | select(.name == "rebooted") | .state')"
if [[ "$state" == interrupted ]]; then echo "  ok   task is marked interrupted"; else echo "  FAIL task state: $state"; fail=1; fi
if as_claude task-list | jq -e 'length == 1' >/dev/null && ! docker exec "$NAME" sudo -u claude -H tmux -S /home/claude/.cc/tmux.sock has-session -t rebooted 2>/dev/null; then
  echo "  ok   task does not restart by itself"
else
  echo "  FAIL task does not restart by itself"
  fail=1
fi
exit "$fail"
