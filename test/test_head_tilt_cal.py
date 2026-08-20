#!/usr/bin/env python3
"""Head tilt calibration sweep — find, by eye, which servo angle is level.

The tilt servo's 90deg is wherever the horn was pressed on, not physical
level. This sweep steps the RAW servo tilt (calibration deliberately
bypassed) through numbered positions, holding each so a watcher can note:

  1. which position number the head looked LEVEL at, and
  2. whether the head moved UP or DOWN as the positions counted up.

Report those two facts and the calibration is written to
data/head_trim.json, after which tilt 0 means level and positive tilt means
up everywhere (`set_head`, the move_head tool, the levelling code).

Head-only: channels 0/1, Control() never constructed, legs stay limp.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adc import ADC  # noqa: E402

FLOOR_V = float(os.environ.get("PIBOT_BATTERY_FLOOR", "6.0"))

load_v, pi_v = ADC().read_battery_voltage()
print(f"battery: load={load_v}V pi={pi_v}V (floor {FLOOR_V}V)")
if min(load_v, pi_v) < FLOOR_V:
    print("REFUSING to drive servos: battery below the floor. Charge the pack.")
    sys.exit(1)

from src.servo import Servo  # noqa: E402
from src import actions  # noqa: E402

# Raw frame: the sweep measures the servo, so the calibration must not apply.
actions.HEAD_TILT_SIGN, actions.HEAD_TILT_TRIM = 1, 0.0

servo = Servo()
hardware = {}

POSITIONS = list(range(-45, 46, 15))  # -45 .. +45 in 15deg steps
HOLD_S = 2.5

print(f"{len(POSITIONS)} positions, {HOLD_S}s each. Watch the head; note the "
      f"LEVEL position number and whether counting up moves it UP or DOWN.\n")
for i, raw in enumerate(POSITIONS, 1):
    print(f"  position {i}/{len(POSITIONS)}  (raw tilt {raw:+d})")
    actions.set_head(servo, hardware, 0, raw)
    time.sleep(HOLD_S)

print("\nsweep done — returning to raw 0 and releasing torque")
actions.set_head(servo, hardware, 0, 0)
actions.release_head(servo, hardware)
print("Now report: which position looked level, and did counting up move the "
      "head up or down?")
