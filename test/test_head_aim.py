#!/usr/bin/env python3
"""Head aim survey — which commanded tilt actually aims the ultrasonic level?

Background: the sensor was levelled MECHANICALLY on 2026-08-20 (bracket
straightened by hand), so commanded tilt=0 (servo 90 deg) is not guaranteed to
be physically level. If it is not, the code-enforced levelling added the same
day would re-tilt a sensor the owner just straightened — this script measures
that instead of guessing.

Only head channels 0/1 are driven; the legs are never commanded. At each
commanded tilt it takes a burst of ultrasonic readings and reports the echo
success rate and the median distance. A level beam against a target should
give a high success rate and the shortest consistent distance; a downward beam
mostly hits the floor and returns few or long readings.

Setup: place the robot facing a wall or box 0.5-1.5m away, run with all nodes
stopped (./stop.sh):

    ./venv/bin/python test/test_head_aim.py
"""

import os
import statistics
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
from src.ultrasonic import Ultrasonic  # noqa: E402
from src import actions  # noqa: E402

TILTS = [-20, -15, -10, -5, 0, 5, 10, 15, 20]
SAMPLES = 20

servo = Servo()
hardware = {}
results = []

with Ultrasonic() as sonar:
    for tilt in TILTS:
        actions.set_head(servo, hardware, 0, tilt)  # torque stays on: aim must hold
        time.sleep(0.5)
        readings = []
        for _ in range(SAMPLES):
            cm = sonar.get_distance()
            if cm is not None:
                readings.append(cm)
            time.sleep(0.06)
        median = statistics.median(readings) if readings else None
        results.append((tilt, len(readings), median))
        median_txt = f"median {median:6.1f}cm" if median is not None else "no echoes"
        print(f"  tilt {tilt:+3d}: {len(readings):2d}/{SAMPLES} echoes, {median_txt}")

actions.set_head(servo, hardware, 0, 0)
actions.release_head(servo, hardware)

usable = [r for r in results if r[1] >= SAMPLES // 2]
print()
if not usable:
    print("No tilt returned a majority of echoes — is there a target 0.5-1.5m ahead?")
else:
    best = max(usable, key=lambda r: (r[1], -r[0] if r[2] is None else -r[2]))
    print(f"best aim: commanded tilt {best[0]:+d} ({best[1]}/{SAMPLES} echoes, "
          f"median {best[2]:.1f}cm)")
    if best[0] != 0:
        print(f"=> commanded level is NOT physically level; the head-levelling code "
              f"should apply a tilt trim of {best[0]:+d}.")
    else:
        print("=> commanded tilt 0 is physically level; no trim needed.")
