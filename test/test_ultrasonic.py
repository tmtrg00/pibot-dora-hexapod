"""
test_ultrasonic.py — HC-SR04 distance sensor test
Run from project root: python test/test_ultrasonic.py

Move an object toward and away from the sensor while running.
Press Ctrl+C to stop.
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ultrasonic import Ultrasonic

READINGS = 30   # number of readings before auto-exit (set to 0 for infinite)


def bar(distance_cm, max_cm=100, width=40):
    """ASCII bar showing distance."""
    if distance_cm is None:
        return "[  out of range  ]"
    filled = int(min(distance_cm, max_cm) / max_cm * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def main():
    print("=== Ultrasonic Distance Sensor Test ===")
    print("Trigger: GPIO 27   Echo: GPIO 22   Max: 3 m\n")

    with Ultrasonic() as u:
        count = 0
        try:
            while READINGS == 0 or count < READINGS:
                d = u.get_distance()
                if d is None:
                    print("  -- cm  [out of range]")
                else:
                    print(f"  {d:5.1f} cm  {bar(d)}")
                count += 1
                time.sleep(0.3)

        except KeyboardInterrupt:
            print("\nInterrupted")

    print("\nUltrasonic test COMPLETE")


if __name__ == "__main__":
    main()
