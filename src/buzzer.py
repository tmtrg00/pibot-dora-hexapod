import time
import lgpio

class Buzzer:
    def __init__(self):
        """Initialize the Buzzer class."""
        self.PIN = 17                                       # Set the GPIO pin for the buzzer
        self._handle = lgpio.gpiochip_open(0)              # Open gpiochip0 (Pi 5 + Ubuntu 25.10)
        lgpio.gpio_claim_output(self._handle, self.PIN)    # Claim pin as output
        lgpio.gpio_write(self._handle, self.PIN, 0)        # Start low (off)

    def set_state(self, state: bool) -> None:
        """Set the state of the buzzer."""
        lgpio.gpio_write(self._handle, self.PIN, 1 if state else 0)

    def close(self) -> None:
        """Close the buzzer pin."""
        lgpio.gpio_write(self._handle, self.PIN, 0)
        lgpio.gpiochip_close(self._handle)

if __name__ == '__main__':
    try:
        buzzer = Buzzer()                 # Create an instance of the Buzzer class
        buzzer.set_state(True)            # Turn on the buzzer
        time.sleep(3)                     # Wait for 3 second
        buzzer.set_state(False)           # Turn off the buzzer
    finally:
        buzzer.close()                    # Ensure the buzzer pin is closed when the program is interrupted



