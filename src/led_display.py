"""LED display adapter: PiBot Display-like interface on Freenove WS2812 LEDs."""

import threading
import time

from src.led import Led


EMOTION_COLORS = {
    "neutral": [50, 50, 50],
    "happy": [0, 220, 40],
    "thinking": [255, 140, 0],
    "curious": [0, 120, 255],
    "surprised": [255, 255, 255],
    "sad": [30, 60, 200],
    "angry": [255, 0, 0],
}


class LedDisplay:
    def __init__(self, _config: dict | None = None, led: Led | None = None):
        self.led = led or Led()
        self.current_emotion = "neutral"
        self._talking = False
        self._talk_stop = threading.Event()
        self._talk_thread = None
        self._lock = threading.Lock()

    def _supported(self) -> bool:
        return bool(getattr(self.led, "is_support_led_function", True)) and hasattr(self.led, "strip")

    def _set_all(self, color: list[int]) -> None:
        if not self._supported():
            return
        count = self.led.strip.get_led_count()
        for i in range(count):
            self.led.strip.set_led_rgb_data(i, color)
        self.led.strip.show()

    def _set_one(self, index: int, color: list[int]) -> None:
        if not self._supported():
            return
        count = self.led.strip.get_led_count()
        if count <= 0:
            return
        self.led.strip.set_led_rgb_data(index % count, color)

    def show_emotion(self, emotion: str) -> None:
        with self._lock:
            self.current_emotion = emotion if emotion in EMOTION_COLORS else "neutral"
            if not self._talking:
                self._set_all(EMOTION_COLORS[self.current_emotion])

    def _talk_loop(self) -> None:
        idx = 0
        while not self._talk_stop.is_set():
            base = EMOTION_COLORS.get(self.current_emotion, EMOTION_COLORS["neutral"])
            dim = [max(0, int(c * 0.15)) for c in base]
            self._set_all(dim)

            if self._supported():
                count = self.led.strip.get_led_count()
                if count > 0:
                    self._set_one(idx, base)
                    self.led.strip.show()
                    idx = (idx + 1) % count
            time.sleep(0.08)

        self._set_all(EMOTION_COLORS.get(self.current_emotion, EMOTION_COLORS["neutral"]))

    def start_talking(self) -> None:
        with self._lock:
            if self._talking:
                return
            self._talking = True
            self._talk_stop.clear()
            self._talk_thread = threading.Thread(target=self._talk_loop, daemon=True)
            self._talk_thread.start()

    def stop_talking(self) -> None:
        with self._lock:
            if not self._talking:
                return
            self._talking = False
            self._talk_stop.set()
            thread = self._talk_thread
            self._talk_thread = None
        if thread:
            thread.join(timeout=1.0)

    def animate_boot(self) -> None:
        if not self._supported():
            return
        self._set_all([0, 0, 0])
        count = self.led.strip.get_led_count()
        for i in range(count):
            self.led.strip.set_led_rgb_data(i, [0, 100, 255])
            self.led.strip.show()
            time.sleep(0.08)
        self._set_all(EMOTION_COLORS["neutral"])

    def close(self) -> None:
        self.stop_talking()
        self._set_all([0, 0, 0])
