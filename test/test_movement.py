"""
test_movement.py — Comprehensive hexapod movement test
Run from project root: python test/test_movement.py

Uses actions.execute() — the same code path as LLM-driven control.
This guarantees correct command queue lengths and timing.

SAFETY: Place the robot on a flat, open surface with room to move.
Each test pauses and asks for Enter before executing.
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.control import Control
from src.led import Led
from src.led_display import LedDisplay
import src.actions as actions


def pause(msg="Press Enter to continue..."):
    try:
        input(f"\n  --> {msg}")
    except EOFError:
        time.sleep(2)


def act(name, args, hw, label=None):
    """Execute an action and print the result."""
    lbl = label or f"{name}({args})"
    print(f"  {lbl}", end="  ", flush=True)
    result = actions.execute(name, args, hw)
    print(f"→  {result}")
    return result


def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


def main():
    print("=== Hexapod Movement Test ===")
    print("Place the robot on a flat, open surface.\n")

    # ── Hardware init ─────────────────────────────────────────────────────────
    print("Initializing control system...")
    ctrl = Control()
    ctrl.condition_thread.start()

    print("Initializing LEDs...")
    led = Led()
    display = LedDisplay(led=led)

    hw = {
        "control": ctrl,
        "led":     led,
        "servo":   ctrl.servo,
        "ultrasonic": None,
        "buzzer":  None,
        "adc":     None,
        "camera":  None,
    }

    print("Control + LEDs ready.\n")
    display.show_emotion("neutral")

    try:
        # ── 1. Stand up / relax ──────────────────────────────────────────────
        section("1. Stand up and relax")
        pause("Press Enter — robot will STAND UP")
        display.show_emotion("happy")
        ctrl.relax(False)
        print("  Standing up...  (servos active)")
        time.sleep(2)

        pause("Press Enter — robot will RELAX (servos off)")
        display.show_emotion("sad")
        ctrl.relax(True)
        print("  Relaxed.")
        time.sleep(1)

        pause("Press Enter — robot will STAND UP again")
        display.show_emotion("neutral")
        ctrl.relax(False)
        print("  Standing up...")
        time.sleep(2)

        # ── 2. Body position (shift body without moving feet) ─────────────────
        section("2. Body position shifts  (stand=cmd, no walking)")
        pause("Press Enter — body will shift in X/Y/Z")

        for x, y, z, label in [
            ( 30,  0,  0, "Shift right (x=+30)"),
            (-30,  0,  0, "Shift left  (x=-30)"),
            (  0,  0,  0, "Centre"),
            (  0, 30,  0, "Shift forward (y=+30)"),
            (  0,-30,  0, "Shift backward (y=-30)"),
            (  0,  0,  0, "Centre"),
            (  0,  0, 15, "Rise up (z=+15)"),
            (  0,  0,-15, "Crouch (z=-15)"),
            (  0,  0,  0, "Centre"),
        ]:
            act("set_position", {"x": x, "y": y, "z": z}, hw, label)
            time.sleep(0.8)

        # ── 3. Body attitude (tilt without moving) ────────────────────────────
        section("3. Body attitude — roll, pitch, yaw")
        pause("Press Enter — body will tilt in various directions")

        for roll, pitch, yaw, label in [
            ( 12,  0,  0, "Roll right (+12°)"),
            (-12,  0,  0, "Roll left  (-12°)"),
            (  0,  0,  0, "Neutral"),
            (  0, 12,  0, "Pitch forward (+12°)"),
            (  0,-12,  0, "Pitch backward (-12°)"),
            (  0,  0,  0, "Neutral"),
            (  0,  0, 12, "Yaw right (+12°)"),
            (  0,  0,-12, "Yaw left  (-12°)"),
            (  0,  0,  0, "Neutral"),
        ]:
            act("set_attitude", {"roll": roll, "pitch": pitch, "yaw": yaw}, hw, label)
            time.sleep(0.8)

        # ── 4. Stand neutral (reset) ──────────────────────────────────────────
        section("4. Stand neutral (reset position)")
        pause("Press Enter")
        act("stand", {}, hw, "Stand neutral")
        time.sleep(1)

        # ── 5. Walk forward ───────────────────────────────────────────────────
        section("5. Walk forward  (tripod gait, speed 7, 2 cycles)")
        pause("Press Enter — robot will walk FORWARD")
        display.show_emotion("curious")
        act("walk", {"direction": "forward", "steps": 2, "speed": 7, "gait": 1}, hw, "Walk forward")
        display.show_emotion("neutral")
        time.sleep(0.5)

        # ── 6. Walk backward ──────────────────────────────────────────────────
        section("6. Walk backward  (tripod gait, speed 7, 2 cycles)")
        pause("Press Enter — robot will walk BACKWARD")
        display.show_emotion("curious")
        act("walk", {"direction": "backward", "steps": 2, "speed": 7, "gait": 1}, hw, "Walk backward")
        display.show_emotion("neutral")
        time.sleep(0.5)

        # ── 7. Strafe left ────────────────────────────────────────────────────
        section("7. Strafe left  (tripod gait, speed 7, 2 cycles)")
        pause("Press Enter — robot will walk SIDEWAYS LEFT")
        act("walk", {"direction": "left", "steps": 2, "speed": 7, "gait": 1}, hw, "Walk left")
        time.sleep(0.5)

        # ── 8. Strafe right ───────────────────────────────────────────────────
        section("8. Strafe right  (tripod gait, speed 7, 2 cycles)")
        pause("Press Enter — robot will walk SIDEWAYS RIGHT")
        act("walk", {"direction": "right", "steps": 2, "speed": 7, "gait": 1}, hw, "Walk right")
        time.sleep(0.5)

        # ── 9. Turn left in place ─────────────────────────────────────────────
        section("9. Turn left  (tripod gait, speed 7, 3 cycles)")
        pause("Press Enter — robot will TURN LEFT in place")
        act("walk", {"direction": "turn_left", "steps": 3, "speed": 7, "gait": 1}, hw, "Turn left")
        time.sleep(0.5)

        # ── 10. Turn right in place ───────────────────────────────────────────
        section("10. Turn right  (tripod gait, speed 7, 3 cycles)")
        pause("Press Enter — robot will TURN RIGHT in place")
        act("walk", {"direction": "turn_right", "steps": 3, "speed": 7, "gait": 1}, hw, "Turn right")
        time.sleep(0.5)

        # ── 11. Wave gait (slow, 6-legged sequential) ─────────────────────────
        section("11. Wave gait forward  (slow gait 2, speed 4, 2 cycles)")
        pause("Press Enter — robot will walk in WAVE gait (all legs sequential)")
        act("walk", {"direction": "forward", "steps": 2, "speed": 4, "gait": 2}, hw, "Wave gait forward")
        time.sleep(0.5)

        # ── 12. Fast walk ─────────────────────────────────────────────────────
        section("12. Fast walk  (tripod, max speed 10, 4 cycles)")
        pause("Press Enter — robot will walk FAST forward")
        display.show_emotion("happy")
        act("walk", {"direction": "forward", "steps": 4, "speed": 10, "gait": 1}, hw, "Fast forward")
        display.show_emotion("neutral")
        time.sleep(0.5)

        # ── 13. Dance ─────────────────────────────────────────────────────────
        section("13. Dance — roll rock side-to-side")
        pause("Press Enter — robot will DANCE")
        display.show_emotion("happy")
        display.start_talking()
        act("dance", {}, hw, "Dance")
        display.stop_talking()
        display.show_emotion("neutral")
        time.sleep(0.5)

        # ── 14. Head tilt nod ─────────────────────────────────────────────────
        section("14. Nod  (pitch forward-back x3)")
        pause("Press Enter — body will NOD")
        for pitch in (10, -4, 10, -4, 0):
            act("set_attitude", {"roll": 0, "pitch": pitch, "yaw": 0}, hw, f"Pitch {pitch:+d}°")
            time.sleep(0.4)

        # ── 15. Bow ───────────────────────────────────────────────────────────
        section("15. Bow — pitch forward deep, then return")
        pause("Press Enter — robot will BOW")
        act("set_attitude", {"roll": 0, "pitch": 12, "yaw": 0}, hw, "Bow down")
        time.sleep(1.5)
        act("set_attitude", {"roll": 0, "pitch": 0,  "yaw": 0}, hw, "Return upright")
        time.sleep(0.5)

        # ── 16. Body wave (sequential tilts) ─────────────────────────────────
        section("16. Body wave animation")
        pause("Press Enter — body will do a rolling wave")
        for roll, pitch, yaw in [(10,10,0),(-10,10,0),(-10,-10,0),(10,-10,0),(0,0,0)]:
            act("set_attitude", {"roll": roll, "pitch": pitch, "yaw": yaw}, hw,
                f"roll={roll:+d} pitch={pitch:+d}")
            time.sleep(0.5)

        # ── 17. Self-balance ──────────────────────────────────────────────────
        section("17. IMU self-balance mode  (10 seconds)")
        pause("Press Enter — self-balance for 10s. Gently tilt the robot to test correction.")
        display.show_emotion("thinking")
        act("toggle_balance", {"enabled": True}, hw, "Enable self-balance")
        time.sleep(10)
        act("toggle_balance", {"enabled": False}, hw, "Disable self-balance")
        display.show_emotion("neutral")
        time.sleep(0.5)

        # ── 18. Relax ─────────────────────────────────────────────────────────
        section("18. Relax — servos off")
        pause("Press Enter — robot will relax (sit down)")
        display.show_emotion("sad")
        act("relax", {"enabled": True}, hw, "Relax")
        time.sleep(0.5)

        display.close()
        print("\n\nMovement test COMPLETE — all sequences executed.")

    except KeyboardInterrupt:
        print("\n\nInterrupted — relaxing robot")
        try:
            ctrl.relax(True)
        except Exception:
            pass
        try:
            display.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
