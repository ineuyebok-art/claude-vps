#!/usr/bin/env bash
# Stub of the official Claude Code installer for container tests.
set -eu
mkdir -p "$HOME/.local/bin"
cat >"$HOME/.local/bin/claude" <<'STUB'
#!/usr/bin/env bash
echo "claude stub $*"
STUB
chmod +x "$HOME/.local/bin/claude"
