#!/usr/bin/env python3
"""Probe node — test harness for the sensors-only graph.

Stands in for the brain in `dataflow-sensors.yml`. It sends nothing that can
move the robot: it only prints the telemetry it receives and cycles the LED
emotions, which together prove that dora is running, the I2C bus is readable,
the ultrasonic sensor is answering, the LED strip works, and messages are
flowing in both directions.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger

common.bootstrap()

from dora import Node  # noqa: E402

NODE = "probe"
logger = get_logger(NODE)

EMOTIONS = ["neutral", "happy", "curious", "thinking", "surprised"]


def main() -> None:
    node = Node()
    logger.info("probe running — watching telemetry, cycling LED emotions")

    battery = None
    distance = None
    ticks = 0
    started = time.time()

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        payload = decode(event) or {}

        if event["id"] == "battery":
            battery = payload
        elif event["id"] == "distance":
            distance = payload.get("cm")
        elif event["id"] == "health":
            logger.warning(f"health: {payload}")
        elif event["id"] == "tick":
            emotion = EMOTIONS[ticks % len(EMOTIONS)]
            node.send_output("emotion", encode({"emotion": emotion}))

            battery_text = (
                f"load={battery['load_v']:.2f}V pi={battery['pi_v']:.2f}V"
                if battery
                else "no reading yet"
            )
            distance_text = f"{distance:.1f}cm" if distance is not None else "no reading yet"
            logger.info(
                f"[{time.time() - started:5.1f}s] battery {battery_text} | "
                f"distance {distance_text} | led -> {emotion}"
            )
            ticks += 1

    logger.info("stopped")


if __name__ == "__main__":
    main()
