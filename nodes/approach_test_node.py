#!/usr/bin/env python3
"""Approach test node — walk up to an obstacle and stop at a set distance.

This is the first graph in which two device nodes close a control loop
together. The ultrasonic node owns the HC-SR04 and publishes a distance
reading; the hardware node owns the legs, subscribes to that stream, and
decides when to stop. Neither knows anything about the other beyond the
message, which is the architecture's central claim being used for something
rather than merely asserted.

It also depends on a property worth stating: the gait runs on its own thread
inside the hardware node, so once a walk is queued the robot keeps walking
without the node's attention. The node's event loop therefore stays free to
receive distance readings *while* the robot walks — which is what allows the
approach to be both continuous and closed-loop. A blocking implementation
would have had to stop between readings.

What to watch for:

  * it should stop by itself at about the requested distance, and the reported
    figure should agree with a tape measure;
  * it should walk straight at the obstacle, not arc off (heading hold runs
    throughout);
  * the retreat leg should open the gap back up again.

Two failure modes the log will catch rather than the floor: stopping short
because the sensor is noisy, and overshooting because a gait cycle cannot be
interrupted — the hardware node predicts the travel still to come in the cycle
in flight and stops that much early, and reports how much lead it allowed.

LOCOMOTION: travels forward and back. Put a wall, box or upright book about
1-1.5m ahead with clear floor between. Needs servo power and a charged pack.

Env:
  PIBOT_APPROACH_STOP_CM   distance to stop at, default 25
  PIBOT_APPROACH_SPEED     gait speed 2-10, default 5
  PIBOT_APPROACH_RETREAT   1 = also back off afterwards, default 1
  PIBOT_APPROACH_RETREAT_CM  distance to back off to, default 50
  PIBOT_APPROACH_MAX_CYCLES  safety cap, default 25
  PIBOT_APPROACH_ABORT_V   abort below this load voltage, default 4.9
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

NODE = "approach_test"
logger = get_logger(NODE)

STOP_CM = max(5.0, min(200.0, float(os.environ.get("PIBOT_APPROACH_STOP_CM", "25"))))
SPEED = max(2, min(10, int(os.environ.get("PIBOT_APPROACH_SPEED", "5"))))
RETREAT = os.environ.get("PIBOT_APPROACH_RETREAT", "1").lower() in {"1", "true", "yes"}
RETREAT_CM = max(10.0, min(300.0, float(os.environ.get("PIBOT_APPROACH_RETREAT_CM", "50"))))
MAX_CYCLES = max(1, min(40, int(os.environ.get("PIBOT_APPROACH_MAX_CYCLES", "25"))))
ABORT_V = float(os.environ.get("PIBOT_APPROACH_ABORT_V", "4.9"))

# An approach runs until it arrives; it is not a fixed number of cycles, so the
# timeout has to cover the cap at the slowest speed with room to spare.
STEP_TIMEOUT_S = 240.0
SETTLE_S = 3.0


def main() -> None:
    node = Node()

    steps = [
        ("stance neutral", "set_stance", {"stance": "neutral"}),
        (
            f"approach the obstacle, stop at {STOP_CM:.0f}cm",
            "approach",
            {"stop_cm": STOP_CM, "direction": "forward", "speed": SPEED,
             "max_cycles": MAX_CYCLES},
        ),
    ]
    if RETREAT:
        steps.append((
            f"back off to {RETREAT_CM:.0f}cm",
            "approach",
            {"stop_cm": RETREAT_CM, "direction": "backward", "speed": SPEED,
             "max_cycles": MAX_CYCLES},
        ))
    steps.append(("stance neutral", "set_stance", {"stance": "neutral"}))
    steps.append(("relax servos", "relax", {"enabled": True}))

    logger.info(
        f"approach test: stop at {STOP_CM:.0f}cm at speed {SPEED}, cap {MAX_CYCLES} cycles, "
        + (f"then retreat to {RETREAT_CM:.0f}cm, " if RETREAT else "")
        + f"abort below {ABORT_V:.2f}V"
    )
    logger.info("Place an obstacle about 1-1.5m ahead with clear floor between.")

    index = -1
    pending = None
    sent_at = 0.0
    next_at = time.time() + 3.0
    results = []
    battery_min = None
    aborted = False
    last_distance = None
    distance_seen = 0
    last_report = 0.0

    def send_next():
        nonlocal index, pending, sent_at
        index += 1
        if index >= len(steps):
            pending = None
            return
        label, name, args = steps[index]
        pending = f"c{index}"
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
            ("stance neutral (abort)", "set_stance", {"stance": "neutral"}),
            ("relax servos (abort)", "relax", {"enabled": True}),
        ]

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "distance":
            payload = decode(event) or {}
            try:
                last_distance = float(payload.get("cm"))
            except (TypeError, ValueError):
                continue
            distance_seen += 1
            now = time.time()
            if pending is not None and now - last_report > 1.0:
                last_report = now
                logger.info(f"        distance {last_distance:.1f}cm")

        elif event["id"] == "battery":
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
                or "refused" in text
                or "ABORTED" in text
            )
            logger.info(f"    {'FAILED' if refused else 'ok'}: {text}")
            results.append((steps[index][0], text, refused, last_distance))
            if refused:
                abort(f"step {steps[index][0]!r} did not complete")
            pending = None
            next_at = time.time() + SETTLE_S

        elif event["id"] == "tick":
            now = time.time()
            if pending is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    logger.warning("    no result within timeout")
                    results.append((steps[index][0], "no result within timeout", True, last_distance))
                    abort("a step did not complete in time")
                    pending = None
                    next_at = now + 2.0
                continue
            if index >= len(steps) - 1 and index >= 0:
                break
            if now >= next_at:
                send_next()
                if index >= len(steps):
                    break

    logger.info("=" * 80)
    logger.info("approach summary")
    logger.info("=" * 80)
    for label, text, failed, dist in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label}")
        logger.info(f"          {text}")
        if dist is not None:
            logger.info(f"          sensor read {dist:.1f}cm just after this step")
    logger.info("=" * 80)
    if distance_seen == 0:
        logger.error(
            "  NO distance readings arrived at all — the ultrasonic node is not "
            "publishing. Nothing here tested the closed loop."
        )
    else:
        logger.info(f"  {distance_seen} distance readings received during the run")
    logger.info("  NOW MEASURE: is the robot really the requested distance from the obstacle?")
    good = sum(1 for r in results if not r[2])
    logger.info(
        f"{good}/{len(results)} steps ok"
        + (f", lowest battery {battery_min:.2f}V" if battery_min else "")
    )
    if aborted:
        logger.warning("Run was ABORTED - robot returned to neutral and relaxed.")

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
