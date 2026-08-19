# coding:utf-8
import lgpio
import time
from src.pca9685 import PCA9685

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
    def __init__(self, handle, pin):
        self._handle = handle
        self._pin = pin

    def on(self):
        lgpio.gpio_write(self._handle, self._pin, 1)

    def off(self):
        lgpio.gpio_write(self._handle, self._pin, 0)


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
        self.servo_power = _ServoPowerAdapter(self._power_handle, self._SERVO_POWER_PIN)

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
        if channel < 16:
            duty_cycle = map_value(angle, 0, 180, 500, 2500)
            duty_cycle = map_value(duty_cycle, 0, 20000, 0, 4095)
            self.pwm_41.set_pwm(channel, 0, int(duty_cycle))
        elif channel >= 16 and channel < 32:
            channel -= 16
            duty_cycle = map_value(angle, 0, 180, 500, 2500)
            duty_cycle = map_value(duty_cycle, 0, 20000, 0, 4095)
            self.pwm_40.set_pwm(channel, 0, int(duty_cycle))

    def relax(self):
        """Relax all servos by setting their PWM values to 4096."""
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
