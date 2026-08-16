#!/usr/bin/env bash
#
# Stop every PiBot dora node and release the hardware.
#
# `dora run` does not always reap its children: if the CLI is killed with
# SIGTERM (a `timeout`, a closed terminal, a dropped ssh session) the daemon
# exits but the node processes are orphaned and keep holding the I2C bus,
# gpiochip0, the microphone and — in the full graph — energised servos. The
# next run then fails with "GPIO busy" or an I2C error.
#
# Run this after any unclean exit, and before reporting a hardware fault.

set -uo pipefail

PATTERN="/opt/pibot-dora/venv/bin/python nodes/"

pids="$(pgrep -f "$PATTERN" || true)"

if [[ -z "$pids" ]]; then
  echo "No PiBot dora nodes running."
else
  echo "Stopping nodes:"
  ps -o pid=,args= -p $pids
  # SIGTERM first so nodes run their cleanup (servos relaxed, I2C closed).
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 20); do
    sleep 0.5
    pgrep -f "$PATTERN" >/dev/null || break
  done
  remaining="$(pgrep -f "$PATTERN" || true)"
  if [[ -n "$remaining" ]]; then
    echo "Forcing: $remaining"
    kill -9 $remaining 2>/dev/null || true
    sleep 1
  fi
fi

# Tear down any coordinator/daemon left behind by `dora up`.
if pgrep -f "dora-cli (daemon|coordinator)" >/dev/null 2>&1; then
  /opt/pibot-dora/venv/bin/dora destroy >/dev/null 2>&1 || true
fi

if pgrep -f "$PATTERN" >/dev/null; then
  echo "WARNING: nodes still running." >&2
  exit 1
fi
echo "All nodes stopped."
