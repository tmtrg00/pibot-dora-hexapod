#!/usr/bin/env python3
"""Ultrasonic node — owns the HC-SR04 on GPIO 27/22.

Publishes a distance reading on every tick, and answers the `get_distance`
tool call. In the single-process runtime a distance read blocked whatever
thread asked for it; here the echo-timing loop has its own process, so a slow
or timed-out read cannot stall the autonomy loop or the audio pipeline.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger, owns

common.bootstrap()

from dora import Node  # noqa: E402
from src.actions import execute as run_action  # noqa: E402
from src.ultrasonic import Ultrasonic  # noqa: E402

NODE = "ultrasonic"
logger = get_logger(NODE)


def main() -> None:
    node = Node()

    sensor = None
    try:
        sensor = Ultrasonic()
        logger.info("HC-SR04 ready on GPIO 27/22")
    except Exception as exc:
        # Degrade rather than die: the rest of the robot stays useful without
        # a distance sensor, and dora would otherwise restart us in a loop.
        logger.warning(f"Ultrasonic unavailable: {exc}")

    hardware = {"ultrasonic": sensor}

    try:
        for event in node:
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue

            if event["id"] == "tick":
                if sensor is None:
                    continue
                try:
                    distance = sensor.get_distance()
                except Exception as exc:
                    logger.warning(f"Distance read failed: {exc}")
                    continue
                if distance is not None:
                    node.send_output("distance", encode({"cm": float(distance)}))

            elif event["id"] == "tool_call":
                call = decode(event)
                if not call or not owns(NODE, call.get("name", "")):
                    continue
                text = run_action(call["name"], call.get("args") or {}, hardware)
                node.send_output(
                    "tool_result",
                    encode(
                        {
                            "id": call.get("id"),
                            "name": call["name"],
                            "text": text if text is not None else "Distance sensor not available",
                        }
                    ),
                )
    finally:
        if sensor is not None:
            try:
                sensor.close()
            except Exception:
                pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
