#!/usr/bin/env python3
"""Camera node — owns picamera2 / the CSI camera.

Note: on the current Pi 5 the CSI receive path is defective (see the pinned
entry in the upstream docs/PENDING.md), so `initialize()` is expected to fail
or captures to time out. This node is written to survive that: it reports the
camera as unavailable and keeps serving events, so the rest of the graph runs
normally. Nothing here needs changing when a working board arrives.

Captures are published as *file paths*, not pixel buffers, because the only
consumer today is the vision LLM, which uploads a JPEG. Streaming real frames
through dora's zero-copy shared memory is the change that would make this node
earn its keep — see docs/DESIGN.md.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger, owns

common.bootstrap()

from dora import Node  # noqa: E402
from src.camera_adapter import CameraAdapter  # noqa: E402

NODE = "camera"
logger = get_logger(NODE)

DEFAULT_CAPTURE_PATH = "data/observation.jpg"


def main() -> None:
    node = Node()

    camera = CameraAdapter()
    available = False
    try:
        available = bool(camera.initialize())
    except Exception as exc:
        logger.warning(f"Camera init raised: {exc}")

    logger.info("camera ready" if available else "camera unavailable — capture requests will report failure")

    def do_capture(path: str) -> dict:
        if not available:
            return {"ok": False, "path": None, "error": "camera unavailable"}
        try:
            image = camera.capture(path)
        except Exception as exc:
            return {"ok": False, "path": None, "error": str(exc)}
        if image:
            return {"ok": True, "path": image, "error": None}
        return {"ok": False, "path": None, "error": "capture returned no file"}

    try:
        for event in node:
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue

            payload = decode(event)
            if payload is None:
                payload = {}

            if event["id"] == "capture":
                # The brain asks for a frame; `purpose` is echoed back so it can
                # tell an autonomous observation from a user-requested photo.
                result = do_capture(str(payload.get("path") or DEFAULT_CAPTURE_PATH))
                result["purpose"] = payload.get("purpose", "observation")
                node.send_output("image", encode(result))

            elif event["id"] == "tool_call":
                if not owns(NODE, payload.get("name", "")):
                    continue
                args = payload.get("args") or {}
                result = do_capture(str(args.get("filepath") or "data/voice_photo.jpg"))
                result["purpose"] = "tool"
                result["tool_id"] = payload.get("id")
                node.send_output("image", encode(result))
    finally:
        try:
            camera.close()
        except Exception:
            pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
