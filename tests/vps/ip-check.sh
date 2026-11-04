#!/usr/bin/env bash
# Does the real address of the PC stay away from Anthropic? Uses the real claude binary when the
# host has one: a fake API records everything claude sends during a real SSH session.
# Args: container name, IP.
# shellcheck disable=SC2016,SC2317 # commands expand on the server; restore() runs from trap
set -u
NAME="$1"
IP="$2"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REAL="$(readlink -f "$(command -v claude)" 2>/dev/null || true)"
tmp="$(mktemp -d)"
fail=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=1; }
CLIENT="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' "$NAME")"
SSH=(ssh -F /dev/null -i "$tmp/key" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "claude@$IP")
ssh-keygen -q -t ed25519 -N '' -C ip-check -f "$tmp/key"
read -r t b _ <"$tmp/key.pub"
fp="$(docker exec "$NAME" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys add "$t" "$b" ip-check)"
api_pid=""
restore() {
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null
  docker exec "$NAME" sudo -u ccbot sudo -n /usr/local/lib/claude-vps/keys remove "$fp" >/dev/null 2>&1
  rm -rf "$tmp"
}
trap restore EXIT

echo "-- what a session on the server can see"
out="$("${SSH[@]}" 'env | grep -E "^SSH_(CLIENT|CONNECTION)=" || echo clean')"
if [[ "$out" == clean ]]; then ok "ssh command has no client address in env"; else bad "ssh env: $out"; fi
out="$("${SSH[@]}" -tt 'env | grep -E "^SSH_(CLIENT|CONNECTION)=" | wc -l' | tr -d '\r\n')"
if [[ "$out" == 0 ]]; then ok "interactive shell has no client address in env"; else bad "tty env: $out"; fi
out="$("${SSH[@]}" -tt 'tmux -S ~/.cc/tmux.sock new-session -d -s ipt -c ~/chat "env > /tmp/ipt.env"; sleep 1; grep -cE "^SSH_(CLIENT|CONNECTION)=" /tmp/ipt.env; grep -c "^BROWSER=/usr/local/lib/claude-vps/open-on-desktop" /tmp/ipt.env' | tr -d '\r' | tr '\n' ' ')"
if [[ "$out" == "0 1 " ]]; then ok "tmux task has no client address and uses the server browser"; else bad "tmux env: $out"; fi
if "${SSH[@]}" "who; last -n 5" 2>/dev/null | grep -q "$CLIENT"; then bad "who and last hide the address"; else ok "who and last hide the address"; fi
if "${SSH[@]}" "cat /var/log/wtmp /run/utmp /var/log/lastlog" 2>/dev/null | grep -aq "$CLIENT"; then bad "login records are closed"; else ok "login records are closed"; fi
out="$("${SSH[@]}" -tt 'true' 2>&1 | grep -c 'Last login')"
if [[ "$out" == 0 ]]; then ok "no Last login banner"; else bad "Last login banner"; fi

echo "-- browser"
out="$("${SSH[@]}" 'BROWSER=/tmp/vscode-helper bash -ic "echo \$BROWSER" 2>/dev/null')"
if [[ "$out" == /usr/local/lib/claude-vps/open-on-desktop ]]; then ok "VS Code browser helper is overridden"; else bad "BROWSER: $out"; fi
docker exec "$NAME" bash -c 'mv /usr/local/bin/chromium /usr/local/bin/chromium.real
printf "#!/bin/sh\necho \"\$DISPLAY \$*\" >> /tmp/chromium.log\n" > /usr/local/bin/chromium; chmod 755 /usr/local/bin/chromium; rm -f /tmp/chromium.log'
"${SSH[@]}" 'bash -ic "\$BROWSER https://claude.com/oauth" 2>/dev/null' >/dev/null 2>&1
sleep 1
if docker exec "$NAME" grep -q '^:1 .*https://claude.com/oauth' /tmp/chromium.log; then ok "a link opens in Chromium on the server desktop"; else bad "link opens on the server desktop"; fi

if [[ -z "$REAL" ]]; then
  echo "  skip real claude: binary not installed"
else
  docker cp "$REAL" "$NAME:/home/claude/.local/bin/claude"
  docker exec "$NAME" chown claude:claude /home/claude/.local/bin/claude
  python3 - "$ROOT" "$tmp" "$CLIENT" <<'PY' &
import json, sys
sys.path.insert(0, sys.argv[1])
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tests.fake_anthropic import FakeAnthropic
fake = FakeAnthropic(tool_command="env; who; last -n 3")
log = open(sys.argv[2] + "/api.jsonl", "a", buffering=1)

class Recorder(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args):
        pass
    def forward(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
        log.write(json.dumps({"headers": headers, "body": body.decode("utf-8", "replace")}) + "\n")
        conn = HTTPConnection("127.0.0.1", fake.server.server_port)
        conn.request(self.command, self.path, body, {"content-type": "application/json"})
        resp = conn.getresponse()
        self.send_response(resp.status)
        self.send_header("content-type", resp.getheader("content-type", "application/json"))
        self.send_header("connection", "close")
        self.end_headers()
        while chunk := resp.read(4096):
            self.wfile.write(chunk)
            self.wfile.flush()
        self.close_connection = True
    do_GET = do_POST = forward

ThreadingHTTPServer(("0.0.0.0", 8799), Recorder).serve_forever()
PY
  api_pid=$!
  sleep 1
  docker exec "$NAME" sudo -u claude -H bash -c "cp ~/.claude/settings.json /tmp/settings.bak; jq '.env.ANTHROPIC_BASE_URL=\"http://$CLIENT:8799\" | .env.DISABLE_AUTOUPDATER=\"1\" | .env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=\"1\" | .apiKeyHelper=\"echo sk-ant-fake\"' /tmp/settings.bak > ~/.claude/settings.json"
  "${SSH[@]}" "cd ~/chat && claude -p 'RUNTOOL покажи окружение' --allowedTools Bash </dev/null" >"$tmp/answer" 2>&1
  if grep -q 'Настоящий ответ' "$tmp/answer"; then ok "real claude answers through ssh"; else bad "real claude: $(tail -2 "$tmp/answer")"; fi
  count="$(wc -l <"$tmp/api.jsonl")"
  # In this test the PC and the fake API share an address: the API setting itself does not count.
  hits="$(sed "s/$CLIENT:8799//g" "$tmp/api.jsonl" | grep -c "$CLIENT")"
  if [[ "$count" -ge 2 && "$hits" == 0 ]]; then
    ok "$count requests to the API, client address in none, even after env, who and last"
  else
    bad "client address in $hits of $count API requests"
  fi
  docker exec "$NAME" sudo -u claude -H bash -c "jq 'del(.apiKeyHelper)' /tmp/settings.bak > ~/.claude/settings.json"
  # timeout on the host: on the server it would leave the login stopped in the background.
  timeout 8 "${SSH[@]}" -tt 'BROWSER=/tmp/vscode-helper bash -ic "claude auth login" 2>/dev/null' >/dev/null 2>&1 </dev/null
  docker exec "$NAME" pkill -u claude -f 'auth login' 2>/dev/null
  sleep 1
  if docker exec "$NAME" grep -q '^:1 .*https://claude' /tmp/chromium.log; then ok "login from a PC terminal opens on the server desktop"; else bad "login opens on the server desktop"; fi
  docker exec "$NAME" sudo -u claude -H cp /tmp/settings.bak /home/claude/.claude/settings.json
  docker cp "$ROOT/tests/stubs/claude-install.sh" "$NAME:/tmp/ci.sh"
  docker exec "$NAME" sudo -u claude -H bash -c 'rm -f ~/.local/bin/claude; bash /tmp/ci.sh'
fi
docker exec "$NAME" bash -c 'mv -f /usr/local/bin/chromium.real /usr/local/bin/chromium'
exit "$fail"
