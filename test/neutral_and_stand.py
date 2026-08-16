#!/usr/bin/env python3
"""
neutral_and_stand.py
====================
Step 1 — Move all servos to the Freenove calibration (neutral) position.
          This is the same state the "servo installation program" leaves them in
          before legs are mechanically attached.

Step 2 — Stand-up sequence (three phases):
          Phase 1: all knees push DOWN  right 90→23°,   left 90→157°
          Phase 2: all ankles extend    right 10→77°,   left 170→103°
          Phase 3: knees to walk stance + ankles inward (simultaneous)
                   knees  right 23→70°,  left 157→110°
                   ankles right 77→57°,  left 103→123°  (20° inward)

Run from project root:
    python3 test/neutral_and_stand.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.servo import Servo

# Servo.__init__ now owns GPIO4 (servo power rail) — no need to claim it here.
print("Servo power ON (GPIO4 -> LOW, via Servo.__init__)")
s = Servo()
time.sleep(0.1)

# ── Servo channel layout ─────────────────────────────────────────────────────
#  Hip joints  (rotate leg in/out):    15, 12,  9, 16, 19, 22
#  Knee joints (lift/lower leg):       14, 11,  8, 17, 20, 23
#  Ankle joints (extend foot):         13, 10, 31, 18, 21, 27
#  Camera head:                         0 (pan), 1 (tilt)

# ── STEP 1: Neutral / calibration position ───────────────────────────────────
# Identical to the servo installation program in servo.py __main__.
# Sets the reference position the stand-up sequence starts from.
#   Ankle ch 10, 13, 31  ->  10  deg  (front-side feet)
#   Ankle ch 18, 21, 27  -> 170  deg  (rear-side feet)
#   Everything else      ->  90  deg
print("\n[Step 1] Moving to neutral / calibration position ...")
for ch in range(32):
    if ch in [10, 13, 31]:
        s.set_servo_angle(ch, 10)
    elif ch in [18, 21, 27]:
        s.set_servo_angle(ch, 170)
    else:
        s.set_servo_angle(ch, 90)

print("         Done. Legs should be straight out to the sides, feet flat.")
print("         If any leg looks wrong, Ctrl+C now and check wiring/calibration.\n")

try:
    input("         Press Enter to begin stand-up sequence ...")
except EOFError:
    time.sleep(2)   # non-interactive fallback

# ── STEP 2: Stand-up sequence ────────────────────────────────────────────────
print("\n[Step 2] Stand-up sequence starting ...")

# Phase 1 — Knees push DOWN:
#   right 90→23°  (−67°; diagnose confirmed +30=UP so −67=DOWN)
#   left  90→157° (+67°; diagnose confirmed −30=UP so +67=DOWN)
# Phase 2 — Ankles extend 67° toward 90° (straighten/plant foot):
#   right 10→77°, left 170→103°
# Phase 3 — Knees adjust to walking stance:
#   right 23→70° (+47°), left 157→110° (−47°)

print("  Phase 1: pushing all knees DOWN 67° ...")
for i in range(68):
    s.set_servo_angle(14, 90 - i); s.set_servo_angle(11, 90 - i); s.set_servo_angle(8,  90 - i)
    s.set_servo_angle(17, 90 + i); s.set_servo_angle(20, 90 + i); s.set_servo_angle(23, 90 + i)
    time.sleep(0.005)

print("  Phase 2: extending all ankles 67° ...")
for i in range(68):
    s.set_servo_angle(13, 10  + i); s.set_servo_angle(10, 10  + i); s.set_servo_angle(31, 10  + i)
    s.set_servo_angle(18, 170 - i); s.set_servo_angle(21, 170 - i); s.set_servo_angle(27, 170 - i)
    time.sleep(0.005)

print("  Phase 3: knees to walk stance + ankles inward (simultaneous) ...")
# Knees: right 23→70° (+47°), left 157→110° (−47°)
# Ankles inward simultaneously: right 77→57° (−20°), left 103→123° (+20°)
KNEE_DELTA  = 47
ANKLE_INWARD = 20
for i in range(KNEE_DELTA + 1):
    ankle_step = round(i * ANKLE_INWARD / KNEE_DELTA)
    s.set_servo_angle(14, 23 + i);        s.set_servo_angle(11, 23 + i);        s.set_servo_angle(8,  23 + i)
    s.set_servo_angle(17, 157 - i);       s.set_servo_angle(20, 157 - i);       s.set_servo_angle(23, 157 - i)
    s.set_servo_angle(13, 77 - ankle_step); s.set_servo_angle(10, 77 - ankle_step); s.set_servo_angle(31, 77 - ankle_step)
    s.set_servo_angle(18, 103 + ankle_step); s.set_servo_angle(21, 103 + ankle_step); s.set_servo_angle(27, 103 + ankle_step)
    time.sleep(0.005)

print("\nDone - robot should now be standing.")
print("Servos are holding position (powered). Press Ctrl+C to relax and power off.\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nRelaxing servos ...")
    s.relax()
    s.servo_power.on()   # HIGH = servo power rail OFF
    print("Done.")
