#!/usr/bin/env python3
"""Attitude test node — do roll and pitch tilt the axes they are named after?

`calculate_posture_balance` built its X rotation from the pitch argument and
its Y rotation from the roll argument, so the two were swapped: asking for roll
tilted the robot nose-down, and the tilted stances leaned sideways instead of
forward. It was found by computing the commanded foot heights — `roll` moved
the nose and tail legs, `pitch` moved the two side legs — and fixed on
2026-08-19.

The IMU balance loop was accidentally unharmed, because `imu6050` also unpacked
`update_imu_state()` in the wrong order and the two errors cancelled. Both are
now correct, so they no longer need to.

This graph is the hardware check on that, and it is one you settle by looking
at the robot rather than by reading a log:

  * ROLL should tip the robot LEFT and RIGHT — one long side down, the nose
    and tail staying level.
  * PITCH should tip it NOSE-DOWN and NOSE-UP — the ends moving, the sides
    staying level.

If those are swapped, the fix went in backwards. Before 2026-08-19 they WERE
swapped, so if this graph looks wrong on an old checkout, that is why.

It then runs the two tilted stances, whose descriptions assert a direction:
`lean_forward` is nose-down and `alert` is slightly nose-up.

MOTION: tilts the body on the spot. Does not travel and needs no floor space
beyond the robot's own footprint. Needs servo power and a charged pack.

Env:
  PIBOT_ATTITUDE_ANGLE   degrees to tilt, default 12 (max 15)
  PIBOT_ATTITUDE_HOLD    seconds to hold each tilt, default 3
  PIBOT_ATTITUDE_ABORT_V abort below this load voltage, default 4.9
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

NODE = "attitude_test"
logger = get_logger(NODE)

ANGLE = max(3, min(15, int(os.environ.get("PIBOT_ATTITUDE_ANGLE", "12"))))
HOLD_S = max(1.0, float(os.environ.get("PIBOT_ATTITUDE_HOLD", "3")))
ABORT_V = float(os.environ.get("PIBOT_ATTITUDE_ABORT_V", "4.9"))
STEP_TIMEOUT_S = 60.0


def build_steps():
    a = ANGLE
    return [
        ("stance neutral", "set_stance", {"stance": "neutral"}, 1.0, None),
        (f"ROLL +{a}", "set_attitude", {"roll": a, "pitch": 0, "yaw": 0}, HOLD_S,
         "the robot should tip SIDEWAYS — one long side down, nose and tail level"),
        (f"ROLL -{a}", "set_attitude", {"roll": -a, "pitch": 0, "yaw": 0}, HOLD_S,
         "sideways the other way"),
        ("level", "set_attitude", {"roll": 0, "pitch": 0, "yaw": 0}, 1.0, None),
        (f"PITCH +{a}", "set_attitude", {"roll": 0, "pitch": a, "yaw": 0}, HOLD_S,
         "the robot should tip NOSE-DOWN — the ends move, the sides stay level"),
        (f"PITCH -{a}", "set_attitude", {"roll": 0, "pitch": -a, "yaw": 0}, HOLD_S,
         "nose-up"),
        ("level", "set_attitude", {"roll": 0, "pitch": 0, "yaw": 0}, 1.0, None),
        ("stance lean_forward", "set_stance", {"stance": "lean_forward"}, HOLD_S,
         "described as nose-down: it should lean FORWARD, not sideways"),
        ("stance alert", "set_stance", {"stance": "alert"}, HOLD_S,
         "described as raised with a slight nose-up tilt"),
        ("stance neutral", "set_stance", {"stance": "neutral"}, 1.0, None),
        ("relax servos", "relax", {"enabled": True}, 0.0, None),
    ]


def main() -> None:
    node = Node()
    steps = build_steps()

    logger.info(
        f"attitude test: +/-{ANGLE}deg, holding {HOLD_S:.0f}s each, "
        f"abort below {ABORT_V:.2f}V"
    )
    logger.info("WATCH THE ROBOT. Roll = side to side. Pitch = nose up and down.")
    logger.info("If those are the other way round, the axis fix went in backwards.")

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
        label, name, args, _, watch = steps[index]
        pending = f"c{index}"
        sent_at = time.time()
        logger.info(f"[{index + 1}/{len(steps)}] {label}")
        if watch:
            logger.info(f"    WATCH: {watch}")
        node.send_output("tool_call", encode({"id": pending, "name": name, "args": args}))

    def abort(reason):
        nonlocal aborted, steps, index
        if aborted:
            return
        aborted = True
        logger.error(f"ABORT: {reason}")
        steps = steps[: index + 1] + [
            ("level (abort)", "set_attitude", {"roll": 0, "pitch": 0, "yaw": 0}, 1.0, None),
            ("relax (abort)", "relax", {"enabled": True}, 0.0, None),
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
            refused = bool(payload.get("refused")) or "FAILED" in text or "refused" in text
            logger.info(f"    {'FAILED' if refused else 'ok'}: {text}")
            results.append((steps[index][0], text, refused))
            if refused:
                abort(f"step {steps[index][0]!r} was refused")
            pending = None
            next_at = time.time() + steps[index][3]

        elif event["id"] == "tick":
            now = time.time()
            if pending is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    logger.warning("    no result within timeout")
                    results.append((steps[index][0], "no result within timeout", True))
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

    logger.info("=" * 78)
    logger.info("attitude summary")
    logger.info("=" * 78)
    for label, text, failed in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label:<24} {text}")
    logger.info("=" * 78)
    logger.info("  The verdict here is yours, not the log's:")
    logger.info("    did ROLL tip the robot side to side?")
    logger.info("    did PITCH tip it nose up and down?")
    logger.info("    did lean_forward lean FORWARD rather than sideways?")
    good = sum(1 for r in results if not r[2])
    logger.info(
        f"{good}/{len(results)} steps ok"
        + (f", lowest battery {battery_min:.2f}V" if battery_min else "")
    )
    if aborted:
        logger.warning("Run was ABORTED.")

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
