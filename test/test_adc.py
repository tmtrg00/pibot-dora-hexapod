"""
test_adc.py — Battery voltage ADC test
Run from project root: python test/test_adc.py
"""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adc import ADC

WARN_LOW = 6.0   # warn if load battery below this (V)
WARN_PI  = 6.5   # warn if Pi battery below this (V)


def main():
    print("=== Battery ADC Test ===")
    a = ADC()
    print("ADC initialized (ADS7830 at I2C 0x48)\n")

    try:
        print("Taking 10 readings (1s apart)...\n")
        for i in range(10):
            load, pi = a.read_battery_voltage()
            load_warn = " *** LOW ***" if load < WARN_LOW else ""
            pi_warn   = " *** LOW ***" if pi   < WARN_PI  else ""
            print(f"  [{i+1:2d}]  Load battery: {load:.2f} V{load_warn}   Pi battery: {pi:.2f} V{pi_warn}")
            time.sleep(1.0)

        print("\nADC test COMPLETE")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        a.close_i2c()


if __name__ == "__main__":
    main()
