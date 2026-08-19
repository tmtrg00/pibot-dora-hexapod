# coding:utf-8
import lgpio
import os
import time
from src.pca9685 import PCA9685

# Skip an I2C write when the channel is already holding the value being asked
# for. Set PIBOT_SERVO_WRITE_CACHE=0 to write unconditionally, as before.
WRITE_CACHE = os.environ.get("PIBOT_SERVO_WRITE_CACHE", "1").lower() not in {"0", "false", "no"}

def map_value(value, from_low, from_high, to_low, to_high):
    """Map a value from one range to another."""
    return (to_high - to_low) * (value - from_low) / (from_high - from_low) + to_low


class _ServoPowerAdapter:
    """
    Thin adapter so callers that expect a gpiozero-style .on()/.off() interface
    (actions.py) can toggle servo power without knowing about lgpio.

    on()  → GPIO4 HIGH → servo power rail disabled (motors de-energised)
    off() → GPIO4 LOW  → servo power rail enabled  (motors can run)
    """
    def __init__(self, handle, pin, servo=None):
        self._handle = handle
        self._pin = pin
        self._servo = servo

    def on(self):
        lgpio.gpio_write(self._handle, self._pin, 1)
        # Power off: the servos are no longer holding anything, whatever the
        # write cache thinks.
        if self._servo is not None:
            self._servo.invalidate()

    def off(self):
        lgpio.gpio_write(self._handle, self._pin, 0)
        if self._servo is not None:
            self._servo.invalidate()


class Servo:
    # GPIO pin that enables the servo power rail (active-LOW: 0 = on, 1 = off)
    _SERVO_POWER_PIN = 4

    def __init__(self):
        # Enable servo power: GPIO4 LOW = servos on.
        # Servo owns this pin so any script that creates Servo() gets power
        # automatically — no need for a separate Control instance.
        self._power_handle = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self._power_handle, self._SERVO_POWER_PIN)
        lgpio.gpio_write(self._power_handle, self._SERVO_POWER_PIN, 0)

        # Adapter object for callers that use .on() / .off() (actions.py)
        self.servo_power = _ServoPowerAdapter(self._power_handle, self._SERVO_POWER_PIN, self)

        # Last duty cycle written to each of the 32 channels, or None where the
        # channel's state is unknown. The gait writes all 18 leg servos every
        # frame at ~100 frames a second, and most frames do not actually change
        # most joints: leg angles are whole degrees, and a frame often moves a
        # joint by less than one. Those writes cost real time on a bus the gyro
        # sampler is also trying to use, and achieve nothing. Skipping them is
        # safe precisely because a PCA9685 channel HOLDS its value — not
        # writing is what leaves the servo where it already is.
        self._last_duty = [None] * 32
        self.writes_sent = 0
        self.writes_skipped = 0

        self.pwm_40 = PCA9685(0x40, debug=True)
        self.pwm_41 = PCA9685(0x41, debug=True)
        # Set the cycle frequency of PWM to 50 Hz
        self.pwm_40.set_pwm_freq(50)
        time.sleep(0.01)
        self.pwm_41.set_pwm_freq(50)
        time.sleep(0.01)

    def set_servo_angle(self, channel, angle):
        """
        Convert the input angle to the value of PCA9685 and set the servo angle.

        :param channel: Servo channel (0-31)
        :param angle: Angle in degrees (0-180)
        """
        if not 0 <= channel < 32:
            return
        duty_cycle = map_value(angle, 0, 180, 500, 2500)
        duty_cycle = int(map_value(duty_cycle, 0, 20000, 0, 4095))

        if WRITE_CACHE and self._last_duty[channel] == duty_cycle:
            self.writes_skipped += 1
            return
        self._last_duty[channel] = duty_cycle
        self.writes_sent += 1

        if channel < 16:
            self.pwm_41.set_pwm(channel, 0, duty_cycle)
        else:
            self.pwm_40.set_pwm(channel - 16, 0, duty_cycle)

    def invalidate(self, channel=None):
        """Forget what a channel is holding, so the next write always goes out.

        Anything that drives a channel without going through set_servo_angle —
        relaxing one head servo, cutting the power rail — leaves the cache
        describing a state the hardware is no longer in. Say so, or the next
        command to that channel is silently skipped as a no-op.
        """
        if channel is None:
            self._last_duty = [None] * 32
        elif 0 <= channel < 32:
            self._last_duty[channel] = None

    def cache_stats(self):
        total = self.writes_sent + self.writes_skipped
        share = (100.0 * self.writes_skipped / total) if total else 0.0
        return (
            f"{self.writes_sent} servo writes sent, {self.writes_skipped} skipped as "
            f"already-held ({share:.0f}% saved)"
        )

    def relax(self):
        """Relax all servos by setting their PWM values to 4096."""
        # Torque is about to come off every channel, so nothing is holding the
        # value the cache believes it is.
        self.invalidate()
        for i in range(8):
            self.pwm_41.set_pwm(i, 4096, 4096)      # ch 0-7  on 0x41 (head pan/tilt + spare)
            self.pwm_41.set_pwm(i + 8, 4096, 4096)  # ch 8-15 on 0x41 (leg servos)
            self.pwm_40.set_pwm(i, 4096, 4096)       # ch 0-7  on 0x40 (leg servos)
            self.pwm_40.set_pwm(i + 8, 4096, 4096)  # ch 8-15 on 0x40 (leg servos)


# Main program logic follows:
if __name__ == '__main__':
    print("Now servos will rotate to certain angles.")
    print("Please keep the program running when installing the servos.")
    print("After that, you can press ctrl-C to end the program.")
    servo = Servo()
    while True:
        try:
            for i in range(32):
                if i in [10, 13, 31]:
                    servo.set_servo_angle(i, 10)
                elif i in [18, 21, 27]:
                    servo.set_servo_angle(i, 170)
                else:
                    servo.set_servo_angle(i, 90)
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nEnd of program")
            servo.relax()
            break
