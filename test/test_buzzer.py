"""
test_buzzer.py — Buzzer hardware test
Run from project root: python test/test_buzzer.py
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.buzzer import Buzzer


def beep(buzzer, duration=0.1, pause=0.1):
    buzzer.set_state(True)
    time.sleep(duration)
    buzzer.set_state(False)
    time.sleep(pause)


def main():
    print("=== Buzzer Test ===")
    b = Buzzer()
    print("Buzzer initialized on GPIO 17\n")

    try:
        print("[1] Single beep (0.3s)")
        beep(b, 0.3, 0.5)

        print("[2] Three short beeps")
        for _ in range(3):
            beep(b, 0.1, 0.15)
        time.sleep(0.5)

        print("[3] Double beep")
        beep(b, 0.15, 0.1)
        beep(b, 0.15, 0.5)

        print("[4] SOS pattern  (... --- ...)")
        # dots
        for _ in range(3):
            beep(b, 0.1, 0.1)
        time.sleep(0.2)
        # dashes
        for _ in range(3):
            beep(b, 0.3, 0.1)
        time.sleep(0.2)
        # dots
        for _ in range(3):
            beep(b, 0.1, 0.1)
        time.sleep(0.5)

        print("[5] Rising chirps (6 ascending durations)")
        for dur in [0.05, 0.07, 0.09, 0.11, 0.13, 0.15]:
            beep(b, dur, 0.07)
        time.sleep(0.3)

        print("[6] Long tone (1s)")
        beep(b, 1.0, 0.5)

        print("\nBuzzer test COMPLETE")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        b.close()


if __name__ == "__main__":
    main()
