#!/usr/bin/env python3
"""
servo_recover.py — find a dead or mis-seated leg servo, then stand up cleanly.

Written for the situation it is named after: a servo lead pulled out mid-run,
some legs are out of sync, and the robot needs checking over before it is
trusted to walk again.

It differs from test/diagnose_servos.py in three ways that matter when the
robot is in an unknown state:

  * it reads the battery first and refuses below the 6.0V floor, with the servo
    rail ON, because the unloaded reading is about a volt optimistic and that
    gap is the whole risk (AGENTS.md pre-flight);
  * it relaxes first and waits, so connectors can be re-seated and legs put
    roughly in place by hand before anything is energised;
  * it drives ONE JOINT AT A TIME and never commands a whole-body pose while
    the robot is standing on the floor.

There is no position feedback on these servos, so "is this one working" can
only be answered by moving it and looking. That is what the joint test does:
each joint gets a small sweep and you say whether it moved.

Usage, from the project root:

    ./bin/py test/servo_recover.py             # check every joint, then reference pose
    ./bin/py test/servo_recover.py --rest      # skip the check, go to reference pose
    ./bin/py test/servo_recover.py --stand     # reference pose -> standing on its feet
    ./bin/py test/servo_recover.py --leg 3     # check one leg only
    ./bin/py test/servo_recover.py --relax     # torque off and exit

Two different poses, and the difference matters:

  REFERENCE POSE  every leg straight out sideways — the "starfish". Coxa and
                  femur at 90deg, right tibias at 10deg, left at 170deg. This
                  is the pose servo horns are installed against, so it is the
                  one to check alignment in: every leg should mirror its
                  opposite number exactly. The robot CANNOT stand in it.

  STANDING POSE   what the robot actually stands in, computed by the inverse
                  kinematics from the resting footprint. `--stand` eases from
                  the reference pose into it, so put the robot down on the
                  floor first.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adc import ADC
from src.servo import Servo

BATTERY_FLOOR_V = float(os.environ.get("PIBOT_BATTERY_FLOOR", "6.0"))

# (leg name, coxa ch, femur ch, tibia ch, tibia reference angle)
# Mirrors the channel map in Control.set_leg_angles(). Right-side tibias rest
# near 10deg and left-side near 170deg — they are mirrored, which is correct.
LEGS = [
    ("Leg 1  Front Right", 15, 14, 13, 10),
    ("Leg 2  Mid Right",   12, 11, 10, 10),
    ("Leg 3  Rear Right",   9,  8, 31, 10),
    ("Leg 4  Rear Left",   22, 23, 27, 170),
    ("Leg 5  Mid Left",    19, 20, 21, 170),
    ("Leg 6  Front Left",  16, 17, 18, 170),
]
JOINTS = ("coxa (hip)", "femur (thigh)", "tibia (foot)")


def reference_angle(channel):
    if channel in (10, 13, 31):
        return 10
    if channel in (18, 21, 27):
        return 170
    return 90


def check_battery(adc, servo_on_hint):
    load, pi = adc.read_battery_voltage()
    worst = min(load, pi)
    print(f"  battery {load:.2f}V load / {pi:.2f}V pi  (floor {BATTERY_FLOOR_V:.1f}V)"
          f"  [{servo_on_hint}]")
    return worst


def ease_to(servo, channel, start, end, seconds=0.8, steps=25):
    """Move one joint gradually rather than letting it slam to the target."""
    for i in range(1, steps + 1):
        servo.set_servo_angle(channel, start + (end - start) * i / steps)
        time.sleep(seconds / steps)


def ask(question):
    try:
        return input(f"    {question} [y/N] ").strip().lower().startswith("y")
    except (KeyboardInterrupt, EOFError):
        print()
        raise


def standing_angles(servo):
    """Servo angles for the standing pose, WITHOUT moving anything.

    Runs the robot's own inverse kinematics rather than a second copy of it:
    build a Control shell with the fields set_leg_angles() reads, point it at
    the resting footprint, and capture the angles it would write by swapping
    the servo out for a recorder. Anything else would be a reimplementation of
    the IK, which is the one piece of this codebase that must not be duplicated.
    """
    from src.control import Control

    c = Control.__new__(Control)          # no __init__: __init__ drives the legs
    c.servo = servo
    c.body_height = -30
    c.body_points = [[137.1, 189.4, -30], [225, 0, -30], [137.1, -189.4, -30],
                     [-137.1, -189.4, -30], [-225, 0, -30], [-137.1, 189.4, -30]]
    c.calibration_leg_positions = c.read_from_txt("point")
    c.leg_positions = [[140, 0, 0] for _ in range(6)]
    c.calibration_angles = [[0, 0, 0] for _ in range(6)]
    c.current_angles = [[90, 0, 0] for _ in range(6)]
    c.last_foot_z = [-30] * 6
    c.calibrate()

    # move_position(0, 0, 0): feet on the resting footprint at ride height.
    points = [list(p) for p in c.body_points]
    for i in range(6):
        points[i][2] = -30
    c.transform_coordinates(points)

    if not c.check_point_validity():
        return None

    captured = {}

    class Recorder:
        def set_servo_angle(self, channel, angle):
            captured[channel] = angle

    real, c.servo = c.servo, Recorder()
    try:
        c.set_leg_angles()
    finally:
        c.servo = real
    return captured


def go_standing(servo, seconds=2.5, steps=60):
    """Ease from the reference pose into the standing pose."""
    targets = standing_angles(servo)
    if targets is None:
        print("  REFUSED: the standing pose reports its legs out of reach.")
        return False
    start = {ch: reference_angle(ch) for ch in targets}
    print(f"  easing {len(targets)} joints from the reference pose into standing "
          f"over {seconds:.1f}s")
    for i in range(1, steps + 1):
        f = i / steps
        for ch, target in targets.items():
            servo.set_servo_angle(ch, start[ch] + (target - start[ch]) * f)
        time.sleep(seconds / steps)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rest", action="store_true",
                    help="skip the joint test, go to the reference pose")
    ap.add_argument("--stand", action="store_true",
                    help="ease from the reference pose into the standing pose")
    ap.add_argument("--relax", action="store_true", help="torque off and exit")
    ap.add_argument("--leg", type=int, default=0, help="check one leg (1-6) only")
    ap.add_argument("--sweep", type=int, default=25, help="sweep size in degrees")
    args = ap.parse_args()

    adc = ADC()
    print("\n=== 1. battery ===")
    check_battery(adc, "rail off, reads high")

    servo = Servo()          # enables the servo power rail; moves nothing
    time.sleep(0.5)
    worst = check_battery(adc, "rail ON, this is the one that counts")
    if worst < BATTERY_FLOOR_V:
        print(f"\n  REFUSING: {worst:.2f}V is below the {BATTERY_FLOOR_V:.1f}V floor. "
              f"Charge the pack.")
        servo.servo_power.on()
        adc.close_i2c()
        return 1
    print(f"  OK — {worst:.2f}V is above the floor.\n")

    # Torque off immediately. Nothing should be holding a position while
    # connectors are being checked.
    servo.relax()
    if args.relax:
        print("=== servos relaxed, rail off ===")
        servo.servo_power.on()
        adc.close_i2c()
        return 0

    print("=== 2. before anything moves ===")
    print("  * Check every servo lead is seated, especially the one that pulled out.")
    print("  * STAND THE ROBOT ON A BOX so its legs hang free, or hold it.")
    print("    The legs are out of sync, so the first move could tip it over.")
    print("  * Servos are relaxed now — you can move the legs by hand.")
    try:
        input("\n  Press Enter when the robot is supported and legs are free...")
    except (KeyboardInterrupt, EOFError):
        print("\n  aborted")
        servo.relax()
        servo.servo_power.on()
        adc.close_i2c()
        return 1

    legs = LEGS if not args.leg else [LEGS[args.leg - 1]]
    dead = []

    if not args.rest:
        print("\n=== 3. joint-by-joint check ===")
        print("  Each joint moves on its own. Watch that ONE joint and answer.")
        print("  A joint that does not move at all is the unplugged or failed one.\n")
        try:
            for name, coxa, femur, tibia, _ in legs:
                print(f"  {'-' * 52}")
                print(f"  {name}   channels: coxa {coxa}, femur {femur}, tibia {tibia}")
                for label, ch in zip(JOINTS, (coxa, femur, tibia)):
                    ref = reference_angle(ch)
                    lo = max(0, ref - args.sweep)
                    hi = min(180, ref + args.sweep)
                    print(f"    {label:<15} ch {ch:>2}: sweeping {lo}..{hi}deg")
                    ease_to(servo, ch, ref, hi, 0.5)
                    ease_to(servo, ch, hi, lo, 0.9)
                    ease_to(servo, ch, lo, ref, 0.5)
                    if not ask(f"did the {label.split()[0]} joint move?"):
                        dead.append((name, label, ch))
                    # Leave it relaxed again so nothing is held under load.
                    servo.invalidate(ch)
        except (KeyboardInterrupt, EOFError):
            print("\n  joint check interrupted")

    if args.stand:
        print("\n=== 4. standing pose ===")
        print("  Put the robot on the FLOOR now — it is about to take its own weight.")
        try:
            input("  Press Enter when it is down and clear...")
        except (KeyboardInterrupt, EOFError):
            print()
        # Reach the reference pose first, so the ease starts from a known place.
        for name, coxa, femur, tibia, _ in LEGS:
            for ch in (coxa, femur, tibia):
                servo.set_servo_angle(ch, reference_angle(ch))
        time.sleep(1.0)
        if go_standing(servo):
            print("  Standing. It should be on its feet, body level, legs even.")
        print("\n  Leaving the servos holding the pose.")
        try:
            input("  Press Enter to relax and finish...")
        except (KeyboardInterrupt, EOFError):
            print()
        servo.relax()
        servo.servo_power.on()
        adc.close_i2c()
        print("  Servos relaxed, rail off.\n")
        return 0

    print("\n=== 4. reference pose ===")
    print("  Driving every joint to its reference angle, gradually.")
    for name, coxa, femur, tibia, _ in LEGS:
        for ch in (coxa, femur, tibia):
            servo.set_servo_angle(ch, reference_angle(ch))
        time.sleep(0.25)          # one leg at a time
    for ch in (0, 1):             # head pan/tilt
        servo.set_servo_angle(ch, 90)
    time.sleep(1.0)
    print("  All joints are at the reference pose.")
    print("  Every leg should now look the SAME as its mirror on the other side.")
    print("  Any leg that looks wrong here has a horn seated at the wrong angle —")
    print("  unscrew that horn, re-seat it so the leg matches, and screw it back.")

    print("\n=== summary ===")
    if dead:
        print("  Joints that did NOT move:")
        for name, label, ch in dead:
            print(f"    {name}  {label}  channel {ch}")
        print("\n  Check that lead at both ends: the servo and the driver board.")
        print("  Channels 0-15 are on the 0x41 board, 16-31 on 0x40.")
    elif args.rest:
        print("  Joint check skipped.")
    else:
        print("  Every joint tested moved. No dead servo.")

    print("\n  Leaving the servos HOLDING the reference pose so you can inspect it.")
    print("  Run with --relax to release them, or Ctrl+C now.")
    try:
        input("  Press Enter to relax and finish...")
    except (KeyboardInterrupt, EOFError):
        print()
    servo.relax()
    servo.servo_power.on()
    adc.close_i2c()
    print("  Servos relaxed, rail off.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
