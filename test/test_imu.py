"""
test_imu.py — MPU6050 IMU sensor test
Run from project root: python test/test_imu.py

Tilt and rotate the robot while running to verify live angle tracking.
Press Ctrl+C to stop.
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.imu import IMU

READINGS = 50   # number of readings before auto-exit (0 = infinite)


def bar(value, min_val=-90, max_val=90, width=40):
    """Centred ASCII bar showing angle."""
    mid = width // 2
    normalised = (value - min_val) / (max_val - min_val)
    pos = int(normalised * width)
    pos = max(0, min(width - 1, pos))
    chars = ["-"] * width
    chars[mid] = "|"
    chars[pos] = "#"
    return "".join(chars)


def main():
    print("=== IMU (MPU6050) Test ===")
    print("I2C address: 0x68\n")

    imu = IMU()
    print("IMU initialized — calibrating zero offset (hold still)...")
    time.sleep(1.0)
    print("Calibration done\n")
    print(f"{'Pitch':>8}  {'Roll':>8}  {'Yaw':>8}   Pitch bar")
    print("-" * 70)

    count = 0
    try:
        while READINGS == 0 or count < READINGS:
            imu.update_imu_state()
            pitch = imu.pitch_angle
            roll  = imu.roll_angle
            yaw   = imu.yaw_angle

            # Also grab raw sensor data
            accel = imu.sensor.get_accel_data()
            ax, ay, az = accel['x'], accel['y'], accel['z']

            print(f"  {pitch:+7.2f}°  {roll:+7.2f}°  {yaw:+7.2f}°   {bar(pitch)}  "
                  f"ax={ax:+5.2f} ay={ay:+5.2f} az={az:+5.2f}")

            count += 1
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nInterrupted")

    print("\nIMU test COMPLETE")


if __name__ == "__main__":
    main()
