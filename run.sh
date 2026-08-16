#!/usr/bin/env bash
#
# Launch a PiBot-Hexapod dora dataflow.
#
#   ./run.sh            full autonomous graph (dataflow.yml)
#   ./run.sh sensors    sensors + LED only, no motion, no audio, no API spend
#   ./run.sh <file>     any other dataflow yaml
#
# Ctrl+C stops the graph. The trap below matters: if the dora CLI is killed
# rather than exiting cleanly, it orphans its node processes, which keep
# holding the I2C bus, gpiochip0, the mic and any energised servos — and the
# next run then fails with "GPIO busy". We clean up on the way in and out.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

export PIBOT_HOME="${PIBOT_HOME:-/opt/pibot-hexapod}"
export PATH="$HERE/venv/bin:$PATH"

case "${1:-full}" in
  full|"")  DATAFLOW="dataflow.yml" ;;
  sensors)  DATAFLOW="dataflow-sensors.yml" ;;
  *)        DATAFLOW="$1" ;;
esac

if [[ ! -f "$DATAFLOW" ]]; then
  echo "No such dataflow: $DATAFLOW" >&2
  exit 1
fi

if [[ ! -d "$PIBOT_HOME/src" ]]; then
  echo "PIBOT_HOME=$PIBOT_HOME does not contain the upstream project (no src/)." >&2
  exit 1
fi

# Refuse to start on top of a previous run's orphans — they own the hardware
# this graph is about to ask for.
if pgrep -f "$HERE/venv/bin/python nodes/" >/dev/null; then
  echo "Nodes from a previous run are still alive; cleaning up first."
  ./stop.sh
fi

cleanup() { ./stop.sh >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

echo "dataflow : $DATAFLOW"
echo "upstream : $PIBOT_HOME"
echo

"$HERE/venv/bin/dora" run "$DATAFLOW"
