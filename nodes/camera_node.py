#!/usr/bin/env python3
"""Camera node — owns picamera2 / the CSI camera.

On this Pi the CSI receive path is defective (pinned upstream), and testing
against it exposed two things worth defending against permanently, both of
which are properties of the *upstream driver*, not of this hardware fault:

  1. `initialize()` reports success on a camera that cannot deliver a frame.
     libcamera opens, configures and starts the sensor happily; the frontend
     timeout only surfaces later, asynchronously, on the first dequeue. So a
     successful init means nothing and cannot be trusted as a health signal.

  2. `capture()` can block indefinitely. It sits in C waiting on a frame that
     never arrives, so no amount of Python-level care inside the call helps.

Therefore captures run on a worker thread with a deadline, and the node answers
`ok: false` when the deadline passes rather than waiting. The stuck thread is
abandoned — it cannot be killed from Python — so after
`MAX_CONSECUTIVE_TIMEOUTS` the camera is marked dead and no further captures
are attempted. That bounds the damage to a few leaked threads instead of one
per request forever.

Captures are published as file paths, not pixel buffers, because the only
consumer today is the vision LLM, which uploads a JPEG. Streaming real frames
through dora's shared memory is the change that would make this node earn its
keep — see docs/DESIGN.md.
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger, owns

common.bootstrap()

from dora import Node  # noqa: E402
from src.camera_adapter import CameraAdapter  # noqa: E402

NODE = "camera"
logger = get_logger(NODE)

DEFAULT_CAPTURE_PATH = "data/observation.jpg"

# A working Pi camera returns a still well inside this. Anything slower is a
# fault, and waiting longer only delays the bad news.
CAPTURE_TIMEOUT_S = float(os.environ.get("PIBOT_CAPTURE_TIMEOUT", "8"))

# Each timeout abandons a thread stuck in libcamera. Stop after this many.
MAX_CONSECUTIVE_TIMEOUTS = int(os.environ.get("PIBOT_CAPTURE_MAX_TIMEOUTS", "3"))

POLL_TIMEOUT_S = 0.1


class CaptureJob:
    """One in-flight capture, run off the event loop so it cannot block it."""

    def __init__(self, camera: CameraAdapter, path: str, purpose: str, tool_id):
        self.path = path
        self.purpose = purpose
        self.tool_id = tool_id
        self.started = time.time()
        self.done = threading.Event()
        self.result = None

        def work() -> None:
            try:
                image = camera.capture(path)
                self.result = (
                    {"ok": True, "path": image, "error": None}
                    if image
                    else {"ok": False, "path": None, "error": "capture returned no file"}
                )
            except Exception as exc:
                self.result = {"ok": False, "path": None, "error": str(exc)}
            finally:
                self.done.set()

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


def main() -> None:
    node = Node()

    camera = CameraAdapter()
    opened = False
    try:
        opened = bool(camera.initialize())
    except Exception as exc:
        logger.warning(f"Camera init raised: {exc}")

    # Deliberately not called "available": a successful open says nothing about
    # whether a frame will ever arrive. Only a completed capture proves that.
    dead = not opened
    if opened:
        logger.info("camera opened (this does NOT mean frames will arrive)")
    else:
        logger.warning("camera unavailable — capture requests will report failure")

    consecutive_timeouts = 0
    job: CaptureJob | None = None

    def reply(payload: dict, purpose: str, tool_id=None) -> None:
        payload = dict(payload)
        payload["purpose"] = purpose
        if tool_id is not None:
            payload["tool_id"] = tool_id
        node.send_output("image", encode(payload))

    def start_capture(path: str, purpose: str, tool_id=None) -> None:
        nonlocal job
        if dead:
            reply({"ok": False, "path": None, "error": "camera unavailable"}, purpose, tool_id)
            return
        if job is not None:
            reply({"ok": False, "path": None, "error": "capture already in progress"}, purpose, tool_id)
            return
        job = CaptureJob(camera, path, purpose, tool_id)

    try:
        while True:
            # Settle any in-flight capture first, so a finished or timed-out
            # job is always answered promptly.
            if job is not None:
                if job.done.is_set():
                    consecutive_timeouts = 0
                    reply(job.result, job.purpose, job.tool_id)
                    job = None
                elif job.elapsed > CAPTURE_TIMEOUT_S:
                    consecutive_timeouts += 1
                    logger.warning(
                        f"capture timed out after {job.elapsed:.1f}s "
                        f"({consecutive_timeouts}/{MAX_CONSECUTIVE_TIMEOUTS}); "
                        f"abandoning the worker thread"
                    )
                    reply(
                        {
                            "ok": False,
                            "path": None,
                            "error": f"capture timed out after {CAPTURE_TIMEOUT_S:.0f}s",
                        },
                        job.purpose,
                        job.tool_id,
                    )
                    job = None
                    if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                        dead = True
                        logger.error(
                            f"camera marked dead after {consecutive_timeouts} consecutive "
                            f"timeouts — no further captures will be attempted. This is the "
                            f"expected result on a defective CSI receive path."
                        )

            event = node.next(timeout=POLL_TIMEOUT_S)
            if event is None:
                continue
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue

            payload = decode(event) or {}

            if event["id"] == "capture":
                start_capture(
                    str(payload.get("path") or DEFAULT_CAPTURE_PATH),
                    str(payload.get("purpose") or "observation"),
                )
            elif event["id"] == "tool_call":
                if not owns(NODE, payload.get("name", "")):
                    continue
                args = payload.get("args") or {}
                start_capture(
                    str(args.get("filepath") or "data/voice_photo.jpg"),
                    "tool",
                    payload.get("id"),
                )
    finally:
        try:
            camera.close()
        except Exception:
            pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
