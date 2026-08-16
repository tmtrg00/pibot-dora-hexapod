import lgpio
import time

class Ultrasonic:
    def __init__(self, trigger_pin: int = 27, echo_pin: int = 22, max_distance: float = 3.0):
        self.trigger_pin = trigger_pin
        self.echo_pin = echo_pin
        self.max_distance = max_distance
        self._timeout = max_distance * 2 / 34300 + 0.01  # seconds for round trip + margin
        self._handle = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self._handle, self.trigger_pin)
        lgpio.gpio_claim_input(self._handle, self.echo_pin)
        lgpio.gpio_write(self._handle, self.trigger_pin, 0)
        time.sleep(0.05)  # settle

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def get_distance(self) -> float:
        """
        Get the distance measurement from the ultrasonic sensor.

        Returns:
        float: The distance measurement in centimeters, rounded to one decimal place,
               or None if no echo was received within max_distance.
        """
        # Send 10µs trigger pulse
        lgpio.gpio_write(self._handle, self.trigger_pin, 1)
        time.sleep(0.00001)
        lgpio.gpio_write(self._handle, self.trigger_pin, 0)

        # Wait for echo to go high
        start = time.time()
        while lgpio.gpio_read(self._handle, self.echo_pin) == 0:
            if time.time() - start > self._timeout:
                return None

        pulse_start = time.time()

        # Wait for echo to go low
        while lgpio.gpio_read(self._handle, self.echo_pin) == 1:
            if time.time() - pulse_start > self._timeout:
                return None

        pulse_end = time.time()

        # Distance = (pulse_duration * speed_of_sound) / 2
        distance = (pulse_end - pulse_start) * 17150  # cm
        if distance > self.max_distance * 100:
            return None
        return round(distance, 1)

    def close(self):
        lgpio.gpiochip_close(self._handle)

if __name__ == '__main__':
    with Ultrasonic() as ultrasonic:
        try:
            while True:
                distance = ultrasonic.get_distance()
                if distance is not None:
                    print(f"Ultrasonic distance: {distance}cm")
                else:
                    print("No echo (out of range)")
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nEnd of program")
