#!/usr/bin/env python3
"""Buzzer node — owns the GPIO buzzer.

Tiny by design. It exists as its own node because the `buzz` tool sleeps for
the duration of the sound; in-process that blocked the caller, here it blocks
nothing but the buzzer.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger, owns

common.bootstrap()

from dora import Node  # noqa: E402
from src.actions import execute as run_action  # noqa: E402
from src.buzzer import Buzzer  # noqa: E402

NODE = "buzzer"
logger = get_logger(NODE)


def main() -> None:
    node = Node()

    buzzer = None
    try:
        buzzer = Buzzer()
        logger.info("buzzer ready")
    except Exception as exc:
        logger.warning(f"Buzzer unavailable: {exc}")

    hardware = {"buzzer": buzzer}

    try:
        for event in node:
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT" or event["id"] != "tool_call":
                continue

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
                        "text": text if text is not None else "Buzzer not available",
                    }
                ),
            )
    finally:
        if buzzer is not None:
            try:
                buzzer.close()
            except Exception:
                pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
