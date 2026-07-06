#!/usr/bin/env bash
# hey-claude setup: venv + deps + LaunchAgent. Idempotent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
LA="$HOME/Library/LaunchAgents/com.hey-claude.plist"
LAUNCHER_DIR="$HOME/Library/Application Support/hey-claude"
LAUNCHER="$LAUNCHER_DIR/launch-hey-claude.sh"

echo "==> repo: $REPO"

if [ ! -d "$REPO/.venv" ]; then
  echo "==> creating venv"
  python3 -m venv "$REPO/.venv"
fi

echo "==> installing (this pulls openwakeword + pyobjc; may take a few minutes)"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e "$REPO[dev]"

echo "==> log dir"
mkdir -p "$HOME/Library/Logs/hey-claude"

echo "==> generating internal launcher -> $LAUNCHER"
# Lives on the internal disk so launchd can always start it at login; it waits for
# the repo's venv (which may be on an external volume) before starting hey-claude.
mkdir -p "$LAUNCHER_DIR"
sed -e "s#__PYTHON__#$PY#g" -e "s#__REPO__#$REPO#g" \
    "$REPO/launch-hey-claude.sh.template" > "$LAUNCHER"
chmod +x "$LAUNCHER"

echo "==> generating LaunchAgent -> $LA"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__LAUNCHER__#$LAUNCHER#g" -e "s#__HOME__#$HOME#g" \
    "$REPO/com.hey-claude.plist.template" > "$LA"

cat <<EOF

==> done.

Next:
  1. Grant permissions to  $PY  in System Settings → Privacy & Security:
       • Accessibility   (keystrokes + reading the Claude box)
       • Microphone      (wake-word listening)
  2. Smoke test (no wake word):   "$PY" -m hey_claude --once
  3. Train "hey-claude":               see scripts/train-wake-word.md, then set wake.model in config.toml
  4. Start at login:              launchctl load $LA
     Stop:                        launchctl unload $LA

Run unit tests:                   "$PY" -m pytest
EOF
