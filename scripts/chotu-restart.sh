#!/usr/bin/env bash
# chotu-restart — restart the hey-claude wake daemon.
#
# Use after reconnecting the mic: on a Bluetooth drop chotu stops cleanly (exit 0)
# and the LaunchAgent's KeepAlive={SuccessfulExit: false} leaves it down, so recovery
# is manual. This kicks it back up and reports which input device it will bind to
# (so you can confirm it grabbed the headset, not the silent HDMI/Teams fallback).
set -u

LABEL="com.hey-claude.chotu"
DOMAIN="gui/$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="/Volumes/GeofastStorage/GitHub/hey-claude/.venv/bin/python"

# Job stays *loaded* (just not running) after a clean KeepAlive-suppressed exit, so
# kickstart -k restarts it. If it was fully booted out, fall back to bootstrap.
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl kickstart -k "$DOMAIN/$LABEL"
else
  launchctl bootstrap "$DOMAIN" "$PLIST"
fi

sleep 2
PID="$(pgrep -f '[-]m chotu' || true)"
if [ -n "$PID" ]; then
  echo "chotu restarted (PID $PID)."
else
  echo "chotu did not come up — check ~/Library/Logs/hey-claude/daemon.err.log" >&2
fi

if [ -x "$PY" ]; then
  MIC="$("$PY" -c "import sounddevice as sd; print(sd.query_devices(kind='input')['name'])" 2>/dev/null)"
  [ -n "$MIC" ] && echo "mic bound to: $MIC"
fi
