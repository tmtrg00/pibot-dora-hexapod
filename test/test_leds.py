"""
test_leds.py — WS2812B RGB LED strip test
Run from project root: python test/test_leds.py

Tests solid colors, animations, emotions, talking ripple, and boot sequence.
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.led import Led
from src.led_display import LedDisplay, EMOTION_COLORS


def section(title):
    print(f"\n--- {title} ---")


def main():
    print("=== LED Strip Test (7x WS2812B) ===\n")

    led = Led()
    display = LedDisplay(led=led)

    try:
        # ── Boot animation ───────────────────────────────────────────────────
        section("Boot animation")
        display.animate_boot()
        time.sleep(1)

        # ── Solid primary colors ─────────────────────────────────────────────
        section("Solid primary colors (1.5s each)")
        for name, color in [
            ("Red",     [255, 0,   0  ]),
            ("Green",   [0,   255, 0  ]),
            ("Blue",    [0,   0,   255]),
            ("White",   [255, 255, 255]),
            ("Yellow",  [255, 220, 0  ]),
            ("Cyan",    [0,   255, 200]),
            ("Magenta", [255, 0,   200]),
            ("Orange",  [255, 80,  0  ]),
        ]:
            print(f"  {name}")
            display._set_all(color)
            time.sleep(1.5)

        # ── Emotions ─────────────────────────────────────────────────────────
        section("Emotions (2s each)")
        for emotion in EMOTION_COLORS:
            print(f"  {emotion:10s}  → {EMOTION_COLORS[emotion]}")
            display.show_emotion(emotion)
            time.sleep(2.0)

        # ── Talking animation ────────────────────────────────────────────────
        section("Talking animation (3s, happy emotion)")
        display.show_emotion("happy")
        display.start_talking()
        time.sleep(3)
        display.stop_talking()
        time.sleep(0.5)

        # ── Thinking animation ────────────────────────────────────────────────
        section("Talking animation (3s, thinking emotion)")
        display.show_emotion("thinking")
        display.start_talking()
        time.sleep(3)
        display.stop_talking()
        time.sleep(0.5)

        # ── Led.py raw animations ────────────────────────────────────────────
        section("Raw LED animations via Led class")

        print("  Rainbow sweep")
        led.rainbow(wait_ms=10, iterations=2)

        print("  Rainbow cycle")
        led.rainbow_cycle(wait_ms=5, iterations=2)

        print("  Theater chase (red)")
        led.theater_chase([255, 0, 0], wait_ms=60)
        time.sleep(0.5)

        print("  Color wipe: blue")
        led.color_wipe([0, 50, 255], wait_ms=60)
        time.sleep(0.5)

        print("  Color wipe: off")
        led.color_wipe([0, 0, 0], wait_ms=30)

        # ── Dim brightness levels ─────────────────────────────────────────────
        section("Brightness ramp (blue, 0→255→0)")
        for brightness in list(range(0, 255, 15)) + list(range(255, 0, -15)):
            display._set_all([0, 0, brightness])
            time.sleep(0.04)

        # ── Off ──────────────────────────────────────────────────────────────
        section("Off")
        display.close()
        time.sleep(0.5)

        print("\nLED test COMPLETE")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        display.close()


if __name__ == "__main__":
    main()
