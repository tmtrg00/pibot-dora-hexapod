#!/usr/bin/env python3
"""Head-only movement test — nothing but the head moves.

Drives servo channels 0 (pan) and 1 (tilt) through a slow, watchable sequence
using the same ramped `set_head()` the nodes use. `Control()` is never
constructed, so the legs are never commanded; powering the servo rail with no
PWM on the leg channels leaves them limp.

WATCH: each move should be a smooth sweep, not a snap. The last step releases
torque — the head should go limp without twitching.

Run with everything else stopped (./stop.sh) so nothing owns the I2C bus:

    ./venv/bin/python test/test_head.py

Env: PIBOT_HEAD_SPEED_DEG_S tunes the sweep speed (default 80 deg/s; 0 = jump),
PIBOT_HEAD_RAMP_PAUSE_S the frame time, PIBOT_BATTERY_FLOOR the refusal
voltage (default 6.0).
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

servo = Servo()  # powers the rail; writes nothing until asked
hardware = {}

STEPS = [
    ("level", 0, 0),
    ("pan left", -40, 0),
    ("pan right", 40, 0),
    ("centre", 0, 0),
    ("tilt +20", 0, 20),
    ("tilt -20", 0, -20),
    ("level again", 0, 0),
]

print(f"sweep speed: {actions.HEAD_SPEED_DEG_S} deg/s, frame {actions.HEAD_RAMP_PAUSE_S}s")
for label, pan, tilt in STEPS:
    print(f"  {label:12s} -> pan={pan:+d} tilt={tilt:+d}  (watch: smooth sweep, no snap)")
    actions.set_head(servo, hardware, pan, tilt)
    time.sleep(1.2)

print("releasing head torque — the head should go limp, nothing else moves")
actions.release_head(servo, hardware)
print("done")
