#!/usr/bin/env python3
"""
Freenove Big Hexapod - Final Walking Stance (based on official product photo)
=============================================================================

TARGET GEOMETRY (from official Freenove product image):
  ┌─────────────────────────────────────────────────────────┐
  │  COXA:  points outward at ~45° forward (front legs)     │
  │         straight out (middle legs)                      │
  │         ~45° backward (rear legs)                       │
  │                                                         │
  │  FEMUR: angles ~45° DOWNWARD from body (steep drop)     │
  │         This is the main body-lifting joint             │
  │                                                         │
  │  TIBIA: nearly VERTICAL — pointing straight to ground   │
  │         Forms the lower strut of each leg               │
  │                                                         │
  │  Body clearance: ~1 tibia-length off the ground         │
  │  Knee (femur-tibia joint) is the widest point           │
  └─────────────────────────────────────────────────────────┘

SERVO ANGLE GEOMETRY:
  At 90° all joints are in mechanical neutral (horizontal/centered).

  RIGHT SIDE (direct angles):
    FEMUR: 90° neutral → push to ~35° (femur drops steeply = body rises)
    TIBIA: 90° neutral → push to ~150° (tibia swings toward vertical/ground)

  LEFT SIDE (mirrored — servos face opposite direction):
    FEMUR: 180 - 35 = 145°
    TIBIA: 180 - 150 = 30°

  COXA (horizontal sweep):
    Right-Front:  65°  (forward)   Left-Front:  115° (mirror)
    Right-Middle: 90°  (out)       Left-Middle:  90° (out)
    Right-Rear:  115°  (backward)  Left-Rear:    65° (mirror)

SEQUENCE (strictly ordered — DO NOT reorder phases):
  Phase 0 → Reset all to 90°
  Phase 1 → COXAS to walking spread
  Phase 2 → FEMURS lift body (steep angle)
  Phase 3 → TIBIAS extend vertically to ground
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.servo import Servo

s = Servo()

# ═══════════════════════════════════════════════════════════════════════
# GEOMETRY — tuned to match official product photo
# ═══════════════════════════════════════════════════════════════════════

# FEMUR: how far below 90° the right femur goes
# 35° = steep drop → high body clearance (matches product photo)
# Increase toward 55° if robot tips or servos strain
FEMUR_R = 35
FEMUR_L = 180 - FEMUR_R   # = 145°

# TIBIA: right tibia target — close to 150° makes it near-vertical (pointing down)
# This is the key angle that makes the stance look like the product photo
# Decrease toward 130° if feet slip outward
TIBIA_R = 150
TIBIA_L = 180 - TIBIA_R   # = 30°

# COXA: walking spread angles (right side direct, left auto-mirrored)
COXA_R_FRONT  = 65    # right front sweeps forward
COXA_R_MID    = 90    # right middle straight out
COXA_R_REAR   = 115   # right rear sweeps backward
COXA_L_FRONT  = 180 - COXA_R_REAR   # = 65  (left front mirrors right rear)
COXA_L_MID    = 180 - COXA_R_MID    # = 90
COXA_L_REAR   = 180 - COXA_R_FRONT  # = 115 (left rear mirrors right front)

# Motion smoothness
STEPS      = 120
STEP_DELAY = 0.009   # seconds between steps (~1.1s per phase)

# ═══════════════════════════════════════════════════════════════════════
# CHANNEL MAP  [COXA, FEMUR, TIBIA]
# ═══════════════════════════════════════════════════════════════════════
R_FRONT  = [15, 14, 13]
R_MIDDLE = [12, 11, 10]
R_REAR   = [ 9,  8, 31]   # ch31 = second PCA9685

L_FRONT  = [16, 17, 18]
L_MIDDLE = [19, 20, 21]
L_REAR   = [22, 23, 27]

ALL_LEGS = [
    # (channels,  coxa_target, femur_target, tibia_target)
    (R_FRONT,  COXA_R_FRONT, FEMUR_R, TIBIA_R),
    (R_MIDDLE, COXA_R_MID,   FEMUR_R, TIBIA_R),
    (R_REAR,   COXA_R_REAR,  FEMUR_R, TIBIA_R),
    (L_FRONT,  COXA_L_FRONT, FEMUR_L, TIBIA_L),
    (L_MIDDLE, COXA_L_MID,   FEMUR_L, TIBIA_L),
    (L_REAR,   COXA_L_REAR,  FEMUR_L, TIBIA_L),
]
# ═══════════════════════════════════════════════════════════════════════


def move_phase(joint_index, start_angle_map, steps=STEPS, delay=STEP_DELAY):
    """
    Smoothly move one joint type (0=coxa,1=femur,2=tibia) on all legs.
    start_angle_map: dict of channel → start_angle (usually 90)
    """
    for step in range(steps + 1):
        t = step / steps
        for channels, coxa_t, femur_t, tibia_t in ALL_LEGS:
            ch = channels[joint_index]
            targets = [coxa_t, femur_t, tibia_t]
            start  = start_angle_map.get(ch, 90)
            angle  = start + (targets[joint_index] - start) * t
            s.set_servo_angle(ch, angle)
        time.sleep(delay)


def stance():
    print("=" * 60)
    print("FREENOVE HEXAPOD — OFFICIAL STANCE SEQUENCE")
    print("=" * 60)
    print(f"\nTarget angles:")
    print(f"  Femur  → R: {FEMUR_R}°  L: {FEMUR_L}°")
    print(f"  Tibia  → R: {TIBIA_R}°  L: {TIBIA_L}°")
    print(f"  Coxa   → R: Front={COXA_R_FRONT}° Mid={COXA_R_MID}° Rear={COXA_R_REAR}°")
    print(f"           L: Front={COXA_L_FRONT}° Mid={COXA_L_MID}° Rear={COXA_L_REAR}°")

    # ── Phase 0: Full reset to 90° ─────────────────────────────────
    print("\n[Phase 0/3]  Resetting all joints to 90° (neutral)...")
    all_ch = [ch for leg in ALL_LEGS for ch in leg[0]]
    for ch in all_ch:
        s.set_servo_angle(ch, 90)
    time.sleep(1.5)
    print("             ✓ All joints at 90°")

    # ── Phase 1: COXAS — horizontal walking spread ─────────────────
    print("\n[Phase 1/3]  Spreading COXAS into walking A-stance...")
    move_phase(joint_index=0, start_angle_map={ch: 90 for ch in all_ch})
    time.sleep(0.5)
    print("             ✓ Legs spread. Body still low.")

    # ── Phase 2: FEMURS — steep lift ───────────────────────────────
    print("\n[Phase 2/3]  FEMURS lifting body (steep angle)...")
    move_phase(joint_index=1, start_angle_map={ch: 90 for ch in all_ch})
    time.sleep(0.5)
    print(f"             ✓ Femurs at {FEMUR_R}°/{FEMUR_L}°. Body should be visibly raised.")

    # ── Phase 3: TIBIAS — near-vertical extension ──────────────────
    print("\n[Phase 3/3]  TIBIAS extending to near-vertical (pushing feet down)...")
    move_phase(joint_index=2, start_angle_map={ch: 90 for ch in all_ch})
    time.sleep(0.5)
    print(f"             ✓ Tibias at {TIBIA_R}°/{TIBIA_L}°. Legs should be near-vertical.")

    print("\n" + "=" * 60)
    print("✓  STANCE COMPLETE")
    print("=" * 60)
    print()
    print("FINE-TUNE IF NEEDED:")
    print(f"  Body too low?       → Decrease FEMUR_R below {FEMUR_R} (e.g. 28°)")
    print(f"  Feet slip outward?  → Decrease TIBIA_R below {TIBIA_R} (e.g. 140°)")
    print(f"  Tibias not vertical?→ Increase TIBIA_R above {TIBIA_R} (e.g. 158°)")
    print(f"  Left side wrong?    → One or more left channels may be swapped;")
    print(f"                        run hexapod_stance.py with DIAGNOSTIC=True")
    print(f"  Legs uneven spread? → Adjust COXA_R_FRONT / COXA_R_REAR")


if __name__ == "__main__":
    try:
        stance()
        print("\nHolding position. Press Ctrl+C to relax.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nRelaxing servos...")
        s.relax()
