#!/usr/bin/env python3
"""Hypno test node — runs the belly-sat leg ripple once and stands down.

WATCH: the robot should sink smoothly onto its belly, lift all six legs clear
of the ground, wave them in a slow ripple that travels around the body — each
leg a beat behind its neighbour, feet drawing little ellipses — then set the
feet down and push back up to standing. No foot should touch the ground
mid-wave and the body should not rock. About fifteen seconds in total.

The routine validates every frame offline before moving (nodes/hypno.py), and
the hardware node refuses it below the battery floor.
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

NODE = "hypno_test"
logger = get_logger(NODE)

ABORT_V = float(os.environ.get("PIBOT_HYPNO_ABORT_V", "4.9"))
STEP_TIMEOUT_S = 90.0

# hypno_wave already ends standing in neutral; relax is the safe tail.
STEPS = [
    ("hypno_wave", "hypno_wave", {}),
    ("relax", "relax", {"enabled": True}),
]


def main() -> None:
    node = Node()

    index = -1
    pending = None
    sent_at = 0.0
    next_at = time.time() + 2.0
    results = []
    battery_min = None

    def send_next():
        nonlocal index, pending, sent_at
        index += 1
        if index >= len(STEPS):
            pending = None
            return
        label, name, args = STEPS[index]
        pending = f"h{index}"
        sent_at = time.time()
        logger.info(f"[{index + 1}/{len(STEPS)}] {label}")
        node.send_output("tool_call", encode({"id": pending, "name": name, "args": args}))

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "battery":
            payload = decode(event) or {}
            load_v = float(payload.get("load_v", 0.0))
            if battery_min is None or load_v < battery_min:
                battery_min = load_v
            if load_v < ABORT_V:
                logger.error(f"battery {load_v:.2f}V below {ABORT_V:.2f}V — standing down")

        elif event["id"] == "tool_result":
            payload = decode(event) or {}
            if payload.get("id") != pending:
                continue
            text = str(payload.get("text") or "")
            refused = bool(payload.get("refused")) or "refused" in text
            logger.info(f"    {'FAILED' if refused else 'ok'}: {text}")
            results.append((STEPS[index][0], text, refused))
            pending = None
            next_at = time.time() + 1.0

        elif event["id"] == "tick":
            now = time.time()
            if pending is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    logger.warning("    no result within timeout")
                    results.append((STEPS[index][0], "no result within timeout", True))
                    pending = None
                    next_at = now + 1.0
                continue
            if index >= len(STEPS) - 1 and index >= 0:
                break
            if now >= next_at:
                send_next()
                if index >= len(STEPS):
                    break

    logger.info("=" * 72)
    for label, text, failed in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label:<12} {text}")
    good = sum(1 for r in results if not r[2])
    logger.info(f"{good}/{len(results)} steps ok"
                + (f", lowest battery {battery_min:.2f}V" if battery_min else ""))

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
