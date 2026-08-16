#!/usr/bin/env python3
import sys
import os
import time

# Ensure we can find the Freenove servo library
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.servo import Servo

s = Servo()
time.sleep(0.1)

# --- CONFIGURATION / TARGETS ---
# Final Standing Angles
KNEE_L = 115    # Left Knee (Higher = taller)
KNEE_R = 65     # Right Knee (Lower = taller)
ANKLE_L = 125   # Left Ankle (Inward)
ANKLE_R = 55    # Right Ankle (Inward)

# Walking Stance Hips (The "X" Shape)
HIP_FRONT_L, HIP_MID_L, HIP_REAR_L = 120, 90, 60
HIP_FRONT_R, HIP_MID_R, HIP_REAR_R = 60, 90, 120

# Channel Groups
RIGHT_KNEES  = [14, 11, 8]
LEFT_KNEES   = [17, 20, 23]
RIGHT_ANKLES = [13, 10, 31]
LEFT_ANKLES  = [18, 21, 27]
HIPS         = [15, 12, 9, 16, 19, 22]

# --- STEP 1: Neutral Position ---
print("\n[Step 1] Moving to Flat Neutral...")
for ch in range(32):
    if ch in RIGHT_ANKLES: s.set_servo_angle(ch, 10)
    elif ch in LEFT_ANKLES: s.set_servo_angle(ch, 170)
    else: s.set_servo_angle(ch, 90)
time.sleep(1.0)

input("Press Enter to begin the STABLE stand-up sequence...")

# --- STEP 2: The Lift (Phase 1 & 2 combined) ---
print("[Step 2] Pushing knees down and planting feet...")
for i in range(65):
    # Move Right Knees Down, Left Knees Down
    for ch in RIGHT_KNEES: s.set_servo_angle(ch, 90 - i)
    for ch in LEFT_KNEES:  s.set_servo_angle(ch, 90 + i)
    # Simultaneously bring ankles in slightly to plant them
    for ch in RIGHT_ANKLES: s.set_servo_angle(ch, 10 + i)
    for ch in LEFT_ANKLES:  s.set_servo_angle(ch, 170 - i)
    time.sleep(0.01)

# --- STEP 3: Tripod Stabilization (Phase 3) ---
# We split the legs so the robot doesn't "split" on the floor
print("[Step 3] Shifting to Walk Stance via Tripod...")
TRIPOD_A = [14, 20, 8]  # FR, ML, RR
TRIPOD_B = [11, 17, 23] # MR, FL, RL

# Gently nudge legs to final standing angles to reduce stress
for i in range(20):
    # Adjust Knees to Final
    for ch in RIGHT_KNEES: s.set_servo_angle(ch, 25 + (i*2))
    for ch in LEFT_KNEES:  s.set_servo_angle(ch, 155 - (i*2))
    time.sleep(0.01)

# --- STEP 4: Set Hip "X" Stance (Phase 4) ---
print("[Step 4] Setting Hip 'X' Stance for walking...")
# This prevents the legs from bumping into each other during gait
s.set_servo_angle(15, HIP_FRONT_R); s.set_servo_angle(16, HIP_FRONT_L)
s.set_servo_angle(12, HIP_MID_R);   s.set_servo_angle(19, HIP_MID_L)
s.set_servo_angle(9,  HIP_REAR_R);  s.set_servo_angle(22, HIP_REAR_L)

print("\nRobot is now STANDING and READY to walk.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nPowering off...")
    s.relax()
