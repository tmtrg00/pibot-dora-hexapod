"""
test_head.py — Camera pan/tilt servo test (channels 0 and 1 on PCA9685 0x40)
Run from project root: python test/test_head.py

Pan:  servo ch0 — left (50°) to right (180°), center = 90°
Tilt: servo ch1 — up (0°)   to down (180°), center = 90°

The move_head action maps:
    pan  = -90..+90  →  servo angle  50..180  (90 + pan, clamped)
    tilt = -90..+90  →  servo angle   0..180  (90 + tilt, clamped)
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.servo import Servo

# Channel assignments
CH_PAN  = 0   # PCA9685 0x40, ch0
CH_TILT = 1   # PCA9685 0x40, ch1

# Safe limits (degrees)
PAN_LEFT   = 50
PAN_CENTER = 90
PAN_RIGHT  = 130
TILT_UP    = 60
TILT_CENTER = 90
TILT_DOWN  = 120


def move(servo, pan_deg, tilt_deg, label="", delay=1.0):
    """Set pan and tilt to given angles and wait."""
    print(f"  {label:30s}  pan={pan_deg:3d}°  tilt={tilt_deg:3d}°")
    servo.set_servo_angle(CH_PAN,  pan_deg)
    servo.set_servo_angle(CH_TILT, tilt_deg)
    time.sleep(delay)


def sweep(servo, ch, start, end, steps=20, delay=0.05, other_ch=None, other_val=90):
    """Smoothly sweep one servo channel from start to end."""
    for angle in range(start, end + (1 if end >= start else -1), (1 if end >= start else -1)):
        servo.set_servo_angle(ch, angle)
        if other_ch is not None:
            servo.set_servo_angle(other_ch, other_val)
        time.sleep(delay)


def main():
    print("=== Camera Head Pan/Tilt Test ===")
    print(f"Pan  ch={CH_PAN}  (left={PAN_LEFT}° center={PAN_CENTER}° right={PAN_RIGHT}°)")
    print(f"Tilt ch={CH_TILT} (up={TILT_UP}°   center={TILT_CENTER}°  down={TILT_DOWN}°)\n")

    s = Servo()

    try:
        # ── Center ──────────────────────────────────────────────────────────
        move(s, PAN_CENTER, TILT_CENTER, "Center (home position)", delay=1.5)

        # ── Discrete pan positions ───────────────────────────────────────────
        print("\n[1] Discrete pan positions")
        move(s, PAN_LEFT,   TILT_CENTER, "Look left")
        move(s, PAN_CENTER, TILT_CENTER, "Center")
        move(s, PAN_RIGHT,  TILT_CENTER, "Look right")
        move(s, PAN_CENTER, TILT_CENTER, "Center")

        # ── Discrete tilt positions ──────────────────────────────────────────
        print("\n[2] Discrete tilt positions")
        move(s, PAN_CENTER, TILT_UP,     "Look up")
        move(s, PAN_CENTER, TILT_CENTER, "Center")
        move(s, PAN_CENTER, TILT_DOWN,   "Look down")
        move(s, PAN_CENTER, TILT_CENTER, "Center")

        # ── Diagonal corners ─────────────────────────────────────────────────
        print("\n[3] Diagonal corners")
        move(s, PAN_LEFT,  TILT_UP,     "Upper left")
        move(s, PAN_RIGHT, TILT_UP,     "Upper right")
        move(s, PAN_RIGHT, TILT_DOWN,   "Lower right")
        move(s, PAN_LEFT,  TILT_DOWN,   "Lower left")
        move(s, PAN_CENTER, TILT_CENTER, "Center")

        # ── Smooth pan sweep ─────────────────────────────────────────────────
        print("\n[4] Smooth pan sweep left→right→center")
        sweep(s, CH_PAN, PAN_CENTER, PAN_LEFT,   other_ch=CH_TILT, other_val=TILT_CENTER)
        sweep(s, CH_PAN, PAN_LEFT,   PAN_RIGHT,  other_ch=CH_TILT, other_val=TILT_CENTER)
        sweep(s, CH_PAN, PAN_RIGHT,  PAN_CENTER, other_ch=CH_TILT, other_val=TILT_CENTER)
        time.sleep(0.5)

        # ── Smooth tilt sweep ─────────────────────────────────────────────────
        print("\n[5] Smooth tilt sweep up→down→center")
        sweep(s, CH_TILT, TILT_CENTER, TILT_UP,   other_ch=CH_PAN, other_val=PAN_CENTER)
        sweep(s, CH_TILT, TILT_UP,     TILT_DOWN,  other_ch=CH_PAN, other_val=PAN_CENTER)
        sweep(s, CH_TILT, TILT_DOWN,   TILT_CENTER, other_ch=CH_PAN, other_val=PAN_CENTER)
        time.sleep(0.5)

        # ── Look-around routine ───────────────────────────────────────────────
        print("\n[6] Look-around routine (scanning pattern)")
        positions = [
            (PAN_LEFT,   TILT_UP,      "Up-left"),
            (PAN_CENTER, TILT_UP,      "Up-center"),
            (PAN_RIGHT,  TILT_UP,      "Up-right"),
            (PAN_RIGHT,  TILT_CENTER,  "Right"),
            (PAN_RIGHT,  TILT_DOWN,    "Down-right"),
            (PAN_CENTER, TILT_DOWN,    "Down-center"),
            (PAN_LEFT,   TILT_DOWN,    "Down-left"),
            (PAN_LEFT,   TILT_CENTER,  "Left"),
            (PAN_CENTER, TILT_CENTER,  "Center"),
        ]
        for pan, tilt, label in positions:
            move(s, pan, tilt, label, delay=0.8)

        # ── Nod (tilt up-down-up) ─────────────────────────────────────────────
        print("\n[7] Nod  (tilt up-down x3)")
        for _ in range(3):
            move(s, PAN_CENTER, TILT_UP,     "Nod up",   delay=0.4)
            move(s, PAN_CENTER, TILT_DOWN,   "Nod down", delay=0.4)
        move(s, PAN_CENTER, TILT_CENTER, "Center", delay=0.8)

        # ── Shake (pan left-right x3) ─────────────────────────────────────────
        print("\n[8] Head shake  (pan left-right x3)")
        for _ in range(3):
            move(s, PAN_LEFT,  TILT_CENTER, "Shake left",  delay=0.35)
            move(s, PAN_RIGHT, TILT_CENTER, "Shake right", delay=0.35)
        move(s, PAN_CENTER, TILT_CENTER, "Center", delay=1.0)

        print("\nHead test COMPLETE — returning to center")

    except KeyboardInterrupt:
        print("\nInterrupted — returning to center")
        move(s, PAN_CENTER, TILT_CENTER, "Center")
    finally:
        # Leave head centered
        s.set_servo_angle(CH_PAN,  PAN_CENTER)
        s.set_servo_angle(CH_TILT, TILT_CENTER)


if __name__ == "__main__":
    main()
