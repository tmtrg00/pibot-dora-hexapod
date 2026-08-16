#!/usr/bin/env python3
"""LED node — owns the WS2812B strip (SPI on the Pi 5).

Serves three streams:
  * `emotion`      — set the emotion colour
  * `speech_state` — start/stop the talking animation
  * `tool_call`    — the `set_led` LLM tool

The talking animation and the chase/rainbow patterns run in threads *inside*
this node, exactly as upstream. The difference is that they are now the only
threads in this process, so a stuck animation can no longer steal time from
the gait loop or the audio pipeline, and if this node dies dora reports it
instead of the strip silently freezing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger, load_config, owns

common.bootstrap()

from dora import Node  # noqa: E402
from src.actions import execute as run_action  # noqa: E402
from src.led import Led  # noqa: E402
from src.led_display import LedDisplay  # noqa: E402

NODE = "led"
logger = get_logger(NODE)


def main() -> None:
    node = Node()
    config = load_config()

    led = None
    display = None
    try:
        led = Led()
        display = LedDisplay(config, led)
        display.animate_boot()
        logger.info("LED strip ready")
    except Exception as exc:
        logger.warning(f"LEDs unavailable: {exc}")

    # `hardware` carries the `_led_thread` handle that src/actions.py uses to
    # cancel a running chase/rainbow before starting the next pattern.
    hardware = {"led": led}
    talking = False

    try:
        for event in node:
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue

            payload = decode(event)
            if payload is None:
                continue

            if event["id"] == "emotion":
                if display is None:
                    continue
                try:
                    display.show_emotion(str(payload.get("emotion", "neutral")))
                except Exception as exc:
                    logger.warning(f"show_emotion failed: {exc}")

            elif event["id"] == "speech_state":
                if display is None:
                    continue
                want_talking = bool(payload.get("talking"))
                if want_talking == talking:
                    continue
                talking = want_talking
                try:
                    display.start_talking() if talking else display.stop_talking()
                except Exception as exc:
                    logger.warning(f"talking animation failed: {exc}")

            elif event["id"] == "tool_call":
                if not owns(NODE, payload.get("name", "")):
                    continue
                text = run_action(payload["name"], payload.get("args") or {}, hardware)
                node.send_output(
                    "tool_result",
                    encode(
                        {
                            "id": payload.get("id"),
                            "name": payload["name"],
                            "text": text if text is not None else "LEDs not available",
                        }
                    ),
                )
    finally:
        if display is not None:
            try:
                display.close()
            except Exception:
                pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
