#!/usr/bin/env python3
"""Crab-walk test node — walks the hexapod sideways, there and back.

Crab walking is lateral locomotion: the gait engine's x parameter (±35mm
stride) drives it directly, so `walk(right)` / `walk(left)` is a true
sideways gait, not a turn. This node crabs right for N cycles then left for
N, returning roughly to its start each round, optionally in a named stance —
`wide` is the interesting one for stability, and the offline check clears it
laterally with margin (131..209mm at spread 1.12).

Every stance+direction pair is validated first with
`stances.validate_for_gait(x=±35)`, the frame-by-frame replay of the tripod
gait arithmetic; `narrow` fails it sideways (79.8mm min reach against the
90mm hard limit) just as it fails forward, and is skipped with the reason
logged rather than silently dropping frames on the robot.

LOCOMOTION: the robot travels sideways. Needs clear floor to the robot's
right and left, servo power and a charged pack.

Env:
  PIBOT_CRABWALK_STANCE    stance to crab in, default neutral
  PIBOT_CRABWALK_CYCLES    gait cycles each way, default 3
  PIBOT_CRABWALK_ROUNDS    right+left round trips, default 1
  PIBOT_CRABWALK_SPEED     gait speed 2-10, default 6
  PIBOT_CRABWALK_ABORT_V   abort below this load voltage, default 4.9
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

NODE = "crab_walk"
logger = get_logger(NODE)

STANCE = os.environ.get("PIBOT_CRABWALK_STANCE", "neutral").strip().lower()
CYCLES = max(1, min(10, int(os.environ.get("PIBOT_CRABWALK_CYCLES", "3"))))
ROUNDS = max(1, min(5, int(os.environ.get("PIBOT_CRABWALK_ROUNDS", "1"))))
SPEED = max(2, min(10, int(os.environ.get("PIBOT_CRABWALK_SPEED", "6"))))
ABORT_V = float(os.environ.get("PIBOT_CRABWALK_ABORT_V", "4.9"))
STEP_TIMEOUT_S = 40.0
SETTLE_S = 1.5

# walk() maps left/right to x=∓35; the offline check replays the same stride.
CRAB_X = 35


def build_steps() -> list:
    stance = stances.STANCES.get(STANCE)
    if stance is None:
        logger.error(
            f"unknown stance {STANCE!r}; available: {', '.join(sorted(stances.STANCES))}"
        )
        return []

    for label, x in (("right", CRAB_X), ("left", -CRAB_X)):
        ok, lo, hi, reason = stances.validate_for_gait(stance, x, 0, 0, SPEED)
        if not ok:
            logger.error(f"crabbing {label} in {STANCE!r} fails offline check — {reason}")
            return []
        logger.info(
            f"offline check ok: {STANCE} crabbing {label}, mid-gait reach {lo:.1f}..{hi:.1f}mm"
        )

    steps = [(f"stance {STANCE}", "set_stance", {"stance": STANCE})]
    for r in range(ROUNDS):
        tag = f" (round {r + 1}/{ROUNDS})" if ROUNDS > 1 else ""
        steps.append(
            (f"crab right {CYCLES}{tag}", "walk",
             {"direction": "right", "steps": CYCLES, "speed": SPEED})
        )
        steps.append(
            (f"crab left {CYCLES}{tag}", "walk",
             {"direction": "left", "steps": CYCLES, "speed": SPEED})
        )
    steps.append(("stance neutral", "set_stance", {"stance": "neutral"}))
    steps.append(("relax servos", "relax", {"enabled": True}))
    return steps


def main() -> None:
    node = Node()
    steps = build_steps()

    if not steps:
        logger.error("nothing to do; exiting")
        return

    logger.info(
        f"crab-walk test: stance {STANCE}, {ROUNDS} round(s) of {CYCLES} cycles "
        f"each way at speed {SPEED}, abort below {ABORT_V:.2f}V"
    )

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
            if refused:
                abort(f"step {steps[index][0]!r} was refused")
            pending = None
            next_at = time.time() + SETTLE_S

        elif event["id"] == "tick":
            now = time.time()
            if pending is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    logger.warning("    no result within timeout")
                    results.append((steps[index][0], "no result within timeout", True))
                    abort("a step did not complete in time")
                    pending = None
                    next_at = now + SETTLE_S
                continue
            if index >= len(steps) - 1 and index >= 0:
                break
            if now >= next_at:
                send_next()
                if index >= len(steps):
                    break

    logger.info("=" * 72)
    logger.info("crab-walk summary")
    logger.info("=" * 72)
    for label, text, failed in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label:<28} {text}")
    logger.info("=" * 72)
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
