#!/usr/bin/env python3
"""Motion test node — drives a scripted motion sequence through the graph.

Stands in for the brain in `dataflow-motion.yml` so the motion path can be
exercised without the audio and llm nodes: no microphone, no API spend, no
autonomy loop. It sends one tool call at a time and waits for its result
before sending the next, so the robot is never given overlapping commands.

Two levels, because walking is a different class of risk from posing:

  pose  (default) stand, body attitude, head pan/tilt, relax. The robot moves
        in place and does not travel.
  walk  (PIBOT_MOTION_WALK=1) adds a short forward gait. Needs clear floor
        space — the robot travels, and can walk off an edge.

Every step reports what the hardware node actually said, including refusals,
so a blocked battery gate is visible rather than looking like a hang.
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

NODE = "motion_test"
logger = get_logger(NODE)

INCLUDE_WALK = os.environ.get("PIBOT_MOTION_WALK", "").lower() in {"1", "true", "yes"}

# Seconds to wait for a single step before giving up and moving on. Generous:
# `stand` runs two queued commands and each waits on the gait thread.
STEP_TIMEOUT_S = 25.0

# Settle time after a step completes, so successive poses are distinguishable
# to someone watching the robot.
SETTLE_S = 1.0


def build_steps() -> list:
    steps = [
        ("read battery", "get_battery", {}),
        ("stand neutral", "stand", {}),
        ("lean right", "set_attitude", {"roll": 8, "pitch": 0, "yaw": 0}),
        ("level", "set_attitude", {"roll": 0, "pitch": 0, "yaw": 0}),
        ("lean forward", "set_attitude", {"roll": 0, "pitch": 8, "yaw": 0}),
        ("level", "set_attitude", {"roll": 0, "pitch": 0, "yaw": 0}),
        ("head left", "move_head", {"pan": -25, "tilt": 5}),
        ("head right", "move_head", {"pan": 25, "tilt": 5}),
        ("head centre", "move_head", {"pan": 0, "tilt": 0}),
    ]
    if INCLUDE_WALK:
        steps.append(("walk forward 2 cycles", "walk", {"direction": "forward", "steps": 2, "speed": 6}))
        steps.append(("stand neutral", "stand", {}))
    steps.append(("relax servos", "relax", {"enabled": True}))
    return steps


def main() -> None:
    node = Node()
    steps = build_steps()

    logger.info(
        f"motion test starting — {len(steps)} steps, "
        f"{'INCLUDING WALK (robot will travel)' if INCLUDE_WALK else 'pose only, robot stays in place'}"
    )

    index = -1
    pending_id = None
    sent_at = 0.0
    next_at = time.time() + 2.0  # let the hardware node finish coming up
    results: list = []

    # Track the pack across the run. On a marginal battery the interesting
    # number is not the resting voltage but how far it sags while the legs are
    # actually lifting, so keep the minimum seen.
    battery_min = None
    battery_last = None

    def send_next() -> None:
        nonlocal index, pending_id, sent_at
        index += 1
        if index >= len(steps):
            pending_id = None
            return
        label, name, args = steps[index]
        pending_id = f"m{index}"
        sent_at = time.time()
        logger.info(f"[{index + 1}/{len(steps)}] {label} -> {name}({args})")
        node.send_output("tool_call", encode({"id": pending_id, "name": name, "args": args}))

    def record(text: str, refused: bool) -> None:
        label = steps[index][0]
        mark = "REFUSED" if refused else "ok"
        logger.info(f"    {mark}: {text}")
        results.append((label, steps[index][1], text, refused))

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "battery":
            payload = decode(event) or {}
            load_v = float(payload.get("load_v", 0.0))
            battery_last = (load_v, float(payload.get("pi_v", 0.0)))
            if battery_min is None or load_v < battery_min[0]:
                battery_min = battery_last
            logger.info(f"    battery load={battery_last[0]:.2f}V pi={battery_last[1]:.2f}V")

        elif event["id"] == "tool_result":
            payload = decode(event) or {}
            if payload.get("id") != pending_id:
                continue
            record(str(payload.get("text") or ""), bool(payload.get("refused")))
            pending_id = None
            next_at = time.time() + SETTLE_S

        elif event["id"] == "tick":
            now = time.time()

            if pending_id is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    record("no result within timeout", False)
                    pending_id = None
                    next_at = now + SETTLE_S
                continue

            if index >= len(steps) - 1 and index >= 0:
                break
            if now >= next_at:
                send_next()
                if index >= len(steps):
                    break

    logger.info("=" * 62)
    logger.info("motion test summary")
    logger.info("=" * 62)
    refused = 0
    for label, name, text, was_refused in results:
        if was_refused:
            refused += 1
        logger.info(f"  {'REFUSED' if was_refused else 'ok     '}  {label:<24} {name:<14} {text}")
    logger.info("=" * 62)
    logger.info(f"{len(results)} steps, {refused} refused")
    if battery_min is not None:
        logger.info(
            f"battery: lowest seen load={battery_min[0]:.2f}V pi={battery_min[1]:.2f}V, "
            f"final load={battery_last[0]:.2f}V pi={battery_last[1]:.2f}V"
        )
    if refused:
        logger.warning(
            "Steps were refused by the hardware node's safety gate — this is the "
            "gate working, not a port failure. Check the battery."
        )

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
