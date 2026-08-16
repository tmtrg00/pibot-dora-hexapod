"""
test_all.py — Quick smoke test of all hardware components
Run from project root: python test/test_all.py

Does NOT test full movement sequences (use test_movement.py for that).
Runs a brief functional check on every component and reports PASS/FAIL.
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results = {}


def test(name, fn):
    """Run a test function, catch exceptions, record result."""
    print(f"  {name:40s} ", end="", flush=True)
    try:
        status, detail = fn()
        results[name] = (status, detail)
        print(f"{status}  {detail}")
    except Exception as e:
        results[name] = (FAIL, str(e)[:80])
        print(f"{FAIL}  {e}")


# ── ADC ──────────────────────────────────────────────────────────────────────
def check_adc():
    from src.adc import ADC
    a = ADC()
    load, pi = a.read_battery_voltage()
    a.close_i2c()
    if load < 1.0 or pi < 1.0:
        return FAIL, f"Suspiciously low: load={load}V pi={pi}V"
    return PASS, f"load={load:.2f}V  pi={pi:.2f}V"


# ── Buzzer ────────────────────────────────────────────────────────────────────
def check_buzzer():
    from src.buzzer import Buzzer
    b = Buzzer()
    b.set_state(True)
    time.sleep(0.15)
    b.set_state(False)
    b.close()
    return PASS, "GPIO 17 — beeped 0.15s"


# ── Ultrasonic ────────────────────────────────────────────────────────────────
def check_ultrasonic():
    from src.ultrasonic import Ultrasonic
    with Ultrasonic() as u:
        readings = [u.get_distance() for _ in range(5) if not time.sleep(0.15)]
    valid = [r for r in readings if r is not None]
    if not valid:
        return FAIL, "All readings None (sensor not responding)"
    avg = sum(valid) / len(valid)
    return PASS, f"{len(valid)}/5 valid  avg={avg:.1f}cm"


# ── IMU ───────────────────────────────────────────────────────────────────────
def check_imu():
    from src.imu import IMU
    imu = IMU()
    imu.update_imu_state()
    accel = imu.sensor.get_accel_data()
    az = accel['z']
    if abs(az) < 5.0:
        return FAIL, f"Gravity not detected (az={az:.2f} m/s² — expected ~9.8)"
    return PASS, f"az={az:.2f} m/s²  roll={imu.roll_angle:.1f}°  pitch={imu.pitch_angle:.1f}°"


# ── Servo / PCA9685 ───────────────────────────────────────────────────────────
def check_servos():
    from src.servo import Servo
    s = Servo()
    # Move leg servo to 90° and camera head to centre
    s.set_servo_angle(8, 90)   # leg servo ch8
    s.set_servo_angle(0, 90)   # pan ch0
    s.set_servo_angle(1, 90)   # tilt ch1
    time.sleep(0.3)
    return PASS, "PCA9685 0x40+0x41 — ch8,0,1 set to 90°"


# ── LED strip ─────────────────────────────────────────────────────────────────
def check_leds():
    from src.led import Led
    from src.led_display import LedDisplay
    led = Led()
    if not getattr(led, "is_support_led_function", False):
        return SKIP, "LED not supported on this hardware config"
    d = LedDisplay(led=led)
    d.show_emotion("happy")
    time.sleep(0.5)
    d.show_emotion("neutral")
    time.sleep(0.3)
    d.close()
    return PASS, "7x WS2812B — happy→neutral→off"


# ── Camera ────────────────────────────────────────────────────────────────────
def check_camera():
    from src.camera_adapter import CameraAdapter
    import threading
    result = [None]
    def capture():
        try:
            cam = CameraAdapter()
            result[0] = cam.capture("/tmp/smoke_test.jpg")
            cam.close()
        except Exception as e:
            result[0] = e
    t = threading.Thread(target=capture, daemon=True)
    t.start()
    t.join(timeout=8)
    if t.is_alive():
        return FAIL, "Timed out (8s) — CSI ribbon cable likely not seated"
    if isinstance(result[0], Exception):
        return FAIL, str(result[0])[:80]
    if result[0] and os.path.exists(result[0]):
        size = os.path.getsize(result[0])
        return PASS, f"{size:,} bytes → {result[0]}"
    return FAIL, "capture() returned None"


# ── Control — stand ───────────────────────────────────────────────────────────
def check_control_stand():
    from src.control import Control
    c = Control()
    c.condition_thread.start()
    c.relax(False)
    time.sleep(1.5)
    c.relax(True)
    return PASS, "Stand + relax cycle OK"


# ── Control — gait (one cycle) ────────────────────────────────────────────────
def check_control_gait():
    from src.control import Control
    from src.command import COMMAND as cmd
    c = Control()
    c.condition_thread.start()
    c.relax(False)
    time.sleep(1.0)
    t0 = time.time()
    c.run_gait([cmd.CMD_MOVE, "1", "0", "25", "10", "0"])
    elapsed = time.time() - t0
    c.relax(True)
    return PASS, f"Tripod gait cycle in {elapsed:.2f}s"


# ── Audio (device enumeration only) ──────────────────────────────────────────
def check_audio_device():
    import pyaudio
    pa = pyaudio.PyAudio()
    count = pa.get_device_count()
    jabra = None
    for i in range(count):
        d = pa.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0:
            jabra = d["name"]
    pa.terminate()
    if jabra:
        return PASS, f"Input device: {jabra}"
    return FAIL, "No input device found"


def main():
    print("=== Smoke Test — All Hardware Components ===\n")

    test("ADC / battery voltage",      check_adc)
    test("Buzzer (GPIO 17)",           check_buzzer)
    test("Ultrasonic (GPIO 27/22)",    check_ultrasonic)
    test("IMU (MPU6050)",              check_imu)
    test("Servos (PCA9685 0x40/0x41)", check_servos)
    test("LEDs (WS2812B SPI)",         check_leds)
    test("Camera (OV5647 CSI)",        check_camera)
    test("Audio device detection",     check_audio_device)
    test("Control — stand/relax",      check_control_stand)
    test("Control — tripod gait",      check_control_gait)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "="*55)
    print("SUMMARY")
    print("="*55)
    passed = sum(1 for s, _ in results.values() if s == PASS)
    failed = sum(1 for s, _ in results.values() if s == FAIL)
    skipped = sum(1 for s, _ in results.values() if s == SKIP)
    for name, (status, detail) in results.items():
        mark = "✓" if status == PASS else ("~" if status == SKIP else "✗")
        print(f"  {mark} {name:40s} {status}")
    print(f"\n  {passed} passed  {failed} failed  {skipped} skipped")

    if failed:
        print("\nFailed tests:")
        for name, (status, detail) in results.items():
            if status == FAIL:
                print(f"  ✗ {name}: {detail}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
