#!/usr/bin/env python3
"""
diagnose_servos.py
==================
Tests each servo joint one at a time so you can see which ones are
attached at the wrong angle.

For each joint the script will:
  1. Move to the calibration (neutral) angle
  2. Sweep +30 deg then -30 deg from neutral
  3. Return to neutral

Watch the corresponding leg joint and note if it moves in the
EXPECTED direction described in the prompt.

Run from project root:
    python3 test/diagnose_servos.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.servo import Servo

# Servo.__init__ now owns GPIO4 (servo power rail) — no need to claim it here.
s = Servo()
time.sleep(0.1)

def prompt(msg):
    try:
        input(f"\n  {msg}\n  --> Press Enter to continue (Ctrl+C to skip)...")
    except (KeyboardInterrupt, EOFError):
        pass

def sweep(ch, neutral, delta=30, steps=30, delay=0.02):
    """Move ch: neutral -> neutral+delta -> neutral-delta -> neutral"""
    s.set_servo_angle(ch, neutral)
    time.sleep(0.3)
    for i in range(steps):
        s.set_servo_angle(ch, neutral + int(delta * i / steps))
        time.sleep(delay)
    for i in range(steps * 2):
        s.set_servo_angle(ch, neutral + delta - int(delta * 2 * i / steps))
        time.sleep(delay)
    for i in range(steps):
        s.set_servo_angle(ch, neutral - delta + int(delta * i / steps))
        time.sleep(delay)
    s.set_servo_angle(ch, neutral)

# ── Set ALL to neutral first ──────────────────────────────────────────────────
print("\n[0] Setting all servos to neutral position first ...")
for ch in range(32):
    if ch in [10, 13, 31]:
        s.set_servo_angle(ch, 10)
    elif ch in [18, 21, 27]:
        s.set_servo_angle(ch, 170)
    else:
        s.set_servo_angle(ch, 90)
time.sleep(1)

# ── Leg map ───────────────────────────────────────────────────────────────────
#  Legs viewed from above, Pi board at top:
#
#       Leg1(FR)  Leg2(MR)  Leg3(RR)
#         \         |         /
#    [PI BOARD - FRONT]
#         /         |         \
#       Leg6(FL)  Leg5(ML)  Leg4(RL)
#
#  FR=Front-Right, MR=Mid-Right, RR=Rear-Right
#  FL=Front-Left,  ML=Mid-Left,  RL=Rear-Left

legs = [
    # (name,          hip_ch, knee_ch, ankle_ch, ankle_neutral)
    ("Leg 1 - Front Right", 15, 14, 13, 10),
    ("Leg 2 - Mid Right",   12, 11, 10, 10),
    ("Leg 3 - Rear Right",   9,  8, 31, 10),
    ("Leg 6 - Front Left",  16, 17, 18, 170),
    ("Leg 5 - Mid Left",    19, 20, 21, 170),
    ("Leg 4 - Rear Left",   22, 23, 27, 170),
]

print("""
LEG JOINT GUIDE
===============
HIP   (coxa):   Sweeping +30 from 90 deg should rotate the whole leg BACKWARD.
                If it goes FORWARD instead, the hip horn is on backwards.

KNEE  (femur):  Sweeping +30 from 90 deg should lift the leg UP (raise tibia).
                Right-side legs: +30 = up.  Left-side legs: -30 = up.
                (They are mirrored - that is correct.)

ANKLE (tibia):  Right-side ankles start at 10 deg,  left-side at 170 deg.
                Sweeping toward 90 should extend/straighten the foot.
                If the foot bends the wrong way, the ankle horn is reversed.
""")

try:
    for (name, hip, knee, ankle, ankle_neutral) in legs:
        print(f"\n{'='*55}")
        print(f"  {name}   (hip={hip}, knee={knee}, ankle={ankle})")
        print(f"{'='*55}")

        # --- Hip ---
        prompt(f"WATCH: {name} HIP (ch {hip}) — should rotate whole leg backward then forward")
        sweep(hip, 90, delta=30)

        # --- Knee ---
        prompt(f"WATCH: {name} KNEE (ch {knee}) — should raise/lower the lower leg")
        sweep(knee, 90, delta=30)

        # --- Ankle ---
        prompt(f"WATCH: {name} ANKLE (ch {ankle}) — should flex/extend the foot")
        sweep(ankle, ankle_neutral, delta=25)

    print("\n\nDiagnosis complete. Note any joints that moved in wrong directions.")
    print("Those servo horns need to be re-seated at the correct calibration angle.")

except KeyboardInterrupt:
    print("\nInterrupted.")

finally:
    print("Returning all servos to neutral ...")
    for ch in range(32):
        if ch in [10, 13, 31]:
            s.set_servo_angle(ch, 10)
        elif ch in [18, 21, 27]:
            s.set_servo_angle(ch, 170)
        else:
            s.set_servo_angle(ch, 90)
    time.sleep(1)
    s.relax()
    s.servo_power.on()   # HIGH = servo power rail OFF
    print("Done. Servos relaxed.")
