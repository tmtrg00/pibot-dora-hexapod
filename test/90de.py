#!/usr/bin/env python3
import sys
import os
import time

# Path setup for Freenove libraries
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.servo import Servo

s = Servo()

# --- CHANNEL DEFINITIONS ---
# Knees (Femur): 14, 11, 8 (Right) | 17, 20, 23 (Left)
# Ankles (Tibia): 13, 10, 31 (Right) | 18, 21, 27 (Left)
# Hips (Coxa): 15, 12, 9 (Right) | 16, 19, 22 (Left)

KNEE_CHANNELS  = [14, 11, 8, 17, 20, 23]
ANKLE_CHANNELS = [13, 10, 31, 18, 21, 27]
HIP_CHANNELS   = [15, 12, 9, 16, 19, 22]

def set_90_90_stance():
    print("Moving to 90/90 Stance...")
    
    # 1. Set Hips to 90 first (straight out)
    for ch in HIP_CHANNELS:
        s.set_servo_angle(ch, 90)
    
    # 2. Set Knees and Ankles to 90
    # We do them in a single loop for speed
    for i in range(len(KNEE_CHANNELS)):
        s.set_servo_angle(KNEE_CHANNELS[i], 90)
        s.set_servo_angle(ANKLE_CHANNELS[i], 90)
    
    print("Done. All Tibia and Knee joints are at 90 degrees.")

if __name__ == "__main__":
    try:
        set_90_90_stance()
        # Keep powered so it holds the weight
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nRelaxing servos...")
        s.relax()
