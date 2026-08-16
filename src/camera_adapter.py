"""Camera adapter: Freenove Camera -> PiBot capture() interface."""

import os
import time

from src.camera import Camera


class CameraAdapter:
    def __init__(self, camera: Camera | None = None):
        self.camera = camera or Camera()
        self.initialized = False

    def initialize(self) -> bool:
        if self.camera is None:
            return False
        picam = getattr(self.camera, "camera", None)
        if picam is None:
            return False
        if not getattr(picam, "started", False):
            try:
                picam.start()
                time.sleep(1.5)
            except Exception:
                return False
        self.initialized = True
        return True

    def capture(self, filepath: str = "data/capture.jpg") -> str | None:
        if not self.initialized and not self.initialize():
            return None
        try:
            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            metadata = self.camera.save_image(filepath)
            if metadata is None:
                return None
            return filepath
        except Exception:
            return None

    def close(self) -> None:
        try:
            self.camera.close()
        except Exception:
            pass
