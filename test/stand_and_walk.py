#!/usr/bin/env python3
"""
stand_and_walk.py — Animated stand-up then walk.

Uses the original Freenove test_Servo() 3-phase stand-up sequence to
physically lift the robot from flat/relaxed, then hands off to the IK
control system for walking.

Stand-up phases (from Freenove test.py test_Servo):
  Phase A: Hips rotate 90 → 140° (all 6 legs)
  Phase B: Knees bend   right 90→150°,  left 90→30°
  Phase C: Ankles extend right  0→120°,  left 180→60°
  Phase D: Hips return  140 → 90°  (IK expects hips at 90° for walking)

After Phase D the robot is upright with servos within a few degrees of
the IK standing position.  ctrl.relax(False) corrects the small residual
difference and syncs internal IK state before walking begins.

Run from project root:
    python test/stand_and_walk.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.control import Control
import src.actions as actions

# ── Config ────────────────────────────────────────────────────────────────────
WALK_DIRECTION = "forward"   # forward | backward | left | right | turn_left | turn_right
WALK_STEPS     = 4           # gait cycles (1–10)
WALK_SPEED     = 7           # 2 (slow) – 10 (fast)
WALK_GAIT      = 1           # 1 = tripod, 2 = wave
# ─────────────────────────────────────────────────────────────────────────────

# Servo channel layout (for reference)
HIPS   = [15, 12, 9, 16, 19, 22]   # all 6 hip channels
KNEES_R = [14, 11,  8]              # right-side knees (ch on pwm_41)
KNEES_L = [17, 20, 23]              # left-side knees  (ch on pwm_40)
ANKLES_R = [13, 10, 31]             # right-side ankles
ANKLES_L = [18, 21, 27]             # left-side ankles


def main():
    print("=== Stand and Walk ===\n")

    # ── Init: create Control (and its Servo) but do NOT start condition_thread yet.
    # Control.__init__ calls set_leg_angles() which immediately snaps servos to
    # the IK standing angles.  We then override that with the animated stand-up.
    print("Initialising control system (IK engine, servos)...")
    ctrl = Control()
    s = ctrl.servo          # reuse the single Servo instance — avoids double GPIO4 claim

    hw = {
        "control":    ctrl,
        "led":        None,
        "servo":      s,
        "ultrasonic": None,
        "buzzer":     None,
        "adc":        None,
        "camera":     None,
    }

    try:
        # ── Step 1: Neutral / calibration position ──────────────────────────
        # Identical to the servo installation calibration — a safe starting
        # point from which the stand-up animation can run correctly.
        print("\n[Step 1] Moving to neutral / calibration position...")
        for ch in range(32):
            if ch in [10, 13, 31]:
                s.set_servo_angle(ch, 10)
            elif ch in [18, 21, 27]:
                s.set_servo_angle(ch, 170)
            else:
                s.set_servo_angle(ch, 90)
        print("         Legs should be flat and horizontal.")

        try:
            input("\n         Press Enter to begin stand-up, or Ctrl+C to abort: ")
        except EOFError:
            time.sleep(2)

        # ── Step 2: Phase A — Hips 90 → 140° ────────────────────────────────
        print("\n[Phase A] Rotating hips 90 → 140° ...")
        for i in range(50):
            for ch in HIPS:
                s.set_servo_angle(ch, 90 + i)
            time.sleep(0.005)

        # ── Step 3: Phase B — Knees bend ─────────────────────────────────────
        # Right: 90 → 150°   Left: 90 → 30°
        print("[Phase B] Bending knees (right +60°, left −60°) ...")
        for i in range(60):
            for ch in KNEES_R:
                s.set_servo_angle(ch, 90 + i)
            for ch in KNEES_L:
                s.set_servo_angle(ch, 90 - i)
            time.sleep(0.005)

        # ── Step 4: Phase C — Ankles extend (body lifts) ─────────────────────
        # Right: 0 → 120°   Left: 180 → 60°
        print("[Phase C] Extending ankles — robot lifting up ...")
        for i in range(120):
            for ch in ANKLES_R:
                s.set_servo_angle(ch, i)
            for ch in ANKLES_L:
                s.set_servo_angle(ch, 180 - i)
            time.sleep(0.005)

        print("         Standing on all six legs.")
        time.sleep(0.3)

        # ── Step 5: Phase D — Return hips to 90° (IK walk stance) ────────────
        # The IK engine keeps hips at 90° during walking.  Sweep back slowly.
        print("[Phase D] Returning hips to 90° (walk stance) ...")
        for i in range(50):
            for ch in HIPS:
                s.set_servo_angle(ch, 140 - i)
            time.sleep(0.010)

        time.sleep(0.5)

        # ── Step 6: Sync IK state with current servo positions ───────────────
        # relax(False) → set_leg_angles() snaps knees/ankles by ≤7° to align
        # internal IK state.  Hips are already at 90° so no hip movement.
        print("[Sync]    Aligning IK state (small correction) ...")
        ctrl.relax(False)
        time.sleep(0.5)

        # ── Step 7: Start condition_thread and walk ───────────────────────────
        ctrl.condition_thread.start()
        print("\nRobot is standing and ready.")

        try:
            input(f"\nPress Enter to walk {WALK_DIRECTION} ({WALK_STEPS} cycles, "
                  f"speed {WALK_SPEED}), or Ctrl+C to abort: ")
        except EOFError:
            time.sleep(1)

        print(f"\nWalking {WALK_DIRECTION}...")
        result = actions.execute(
            "walk",
            {"direction": WALK_DIRECTION, "steps": WALK_STEPS,
             "speed": WALK_SPEED, "gait": WALK_GAIT},
            hw,
        )
        print(f"Result: {result}")

        print("\nDone walking. Press Ctrl+C to relax and power down servos.")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nRelaxing servos...")
        try:
            ctrl.relax(True)
        except Exception:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
