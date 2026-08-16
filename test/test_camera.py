"""
test_camera.py — Camera adapter test
Run from project root: python test/test_camera.py

IMPORTANT: If this hangs/times out, the camera ribbon cable is not fully seated.
Reseat the CSI ribbon cable and retry.
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.camera_adapter import CameraAdapter


def main():
    print("=== Camera Adapter Test ===")
    print("Sensor: OV5647 via CSI   Resolution: 640x480\n")
    print("NOTE: If this hangs for >10s, the CSI ribbon cable is not fully seated.\n")

    cam = CameraAdapter()
    print("Camera initialized\n")

    try:
        for i in range(3):
            out = f"/tmp/test_frame_{i+1}.jpg"
            print(f"[{i+1}] Capturing to {out} ...")
            t0 = time.time()
            result = cam.capture(out)
            elapsed = time.time() - t0
            if result and os.path.exists(result):
                size = os.path.getsize(result)
                print(f"     OK — {size:,} bytes in {elapsed:.2f}s")
            else:
                print(f"     FAILED — no file produced in {elapsed:.2f}s")
            time.sleep(1.0)

        print("\nCamera test COMPLETE")
        print(f"Images saved to /tmp/test_frame_1.jpg .. /tmp/test_frame_3.jpg")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        cam.close()


if __name__ == "__main__":
    main()
