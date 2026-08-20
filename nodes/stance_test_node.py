#!/usr/bin/env python3
"""Stance test node — cycles the hexapod through every named stance.

Each stance is applied, held briefly so it can be observed, and reported with
the leg reach the robot actually ended up at. Returns to neutral and relaxes at
the end, and aborts to that same safe tail if the pack sags.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
import stances
from common import decode, encode, get_logger

common.bootstrap()

from dora import Node  # noqa: E402

NODE = "stance_test"
logger = get_logger(NODE)

HOLD_S = float(os.environ.get("PIBOT_STANCE_HOLD", "2.5"))
ABORT_V = float(os.environ.get("PIBOT_STANCE_ABORT_V", "4.9"))
ONLY = os.environ.get("PIBOT_STANCE_ONLY")
STEP_TIMEOUT_S = 30.0


def main() -> None:
    node = Node()

    names = [ONLY] if ONLY else list(stances.STANCES)
    steps = [(n, "set_stance", {"stance": n}) for n in names]
    steps.append(("neutral", "set_stance", {"stance": "neutral"}))
    steps.append(("relax", "relax", {"enabled": True}))

    logger.info(f"stance test: {len(names)} stance(s), holding {HOLD_S:.1f}s each")

    index = -1
    pending = None
    sent_at = 0.0
    next_at = time.time() + 2.0
    results = []
    battery_min = None
    aborted = False

    def send_next():
        nonlocal index, pending, sent_at
        index += 1
        if index >= len(steps):
            pending = None
            return
        label, name, args = steps[index]
        pending = f"s{index}"
        sent_at = time.time()
        logger.info(f"[{index + 1}/{len(steps)}] {label}")
        node.send_output("tool_call", encode({"id": pending, "name": name, "args": args}))

    def abort(reason):
        nonlocal aborted, steps, index
        if aborted:
            return
        aborted = True
        logger.error(f"ABORT: {reason}")
        steps = steps[: index + 1] + [
            ("neutral (abort)", "set_stance", {"stance": "neutral"}),
            ("relax (abort)", "relax", {"enabled": True}),
        ]

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
                abort(f"battery {load_v:.2f}V below {ABORT_V:.2f}V")

        elif event["id"] == "tool_result":
            payload = decode(event) or {}
            if payload.get("id") != pending:
                continue
            text = str(payload.get("text") or "")
            refused = (
                bool(payload.get("refused"))
                or "FAILED" in text
                or "rejected" in text
                or "refused" in text
            )
            logger.info(f"    {'FAILED' if refused else 'ok'}: {text}")
            results.append((steps[index][0], text, refused))
            pending = None
            next_at = time.time() + HOLD_S

        elif event["id"] == "tick":
            now = time.time()
            if pending is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    logger.warning("    no result within timeout")
                    results.append((steps[index][0], "no result within timeout", True))
                    abort("a stance did not complete in time")
                    pending = None
                    next_at = now + HOLD_S
                continue
            if index >= len(steps) - 1 and index >= 0:
                break
            if now >= next_at:
                send_next()
                if index >= len(steps):
                    break

    logger.info("=" * 72)
    logger.info("stance test summary")
    logger.info("=" * 72)
    for label, text, failed in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label:<16} {text}")
    logger.info("=" * 72)
    good = sum(1 for r in results if not r[2])
    logger.info(f"{good}/{len(results)} steps ok" + (f", lowest battery {battery_min:.2f}V" if battery_min else ""))
    if aborted:
        logger.warning("Run was ABORTED - robot returned to neutral and relaxed.")

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
