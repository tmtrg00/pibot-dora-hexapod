#!/usr/bin/env python3
"""Stance-walk test node — walks the hexapod in different footprints.

The claim under test is the one stances.py makes: `run_gait` deep-copies
`Control.body_points`, so a stance's widened or narrowed footprint carries
into the walking gait. Nothing has ever exercised that on hardware.

For each stance the sequence is: adopt the stance, walk forward, walk backward
(returning roughly to the start), so the robot ends each round where it began
and the operator can compare stability between footprints. Every
stance+direction pair is validated offline first with
`stances.validate_for_gait`, which replays the tripod-gait arithmetic frame by
frame — a combination that would push a leg outside the 90..248mm reach window
mid-stride is skipped with a log line instead of silently stuttering on the
robot (set_leg_angles drops out-of-range frames without raising).

LOCOMOTION: the robot travels ~forward then back. Needs floor space ahead of
the robot, servo power and a charged pack.

Env:
  PIBOT_STANCEWALK_STANCES   comma list, default narrow,neutral,wide
  PIBOT_STANCEWALK_CYCLES    gait cycles each way, default 3
  PIBOT_STANCEWALK_SPEED     gait speed 2-10, default 6
  PIBOT_STANCEWALK_ABORT_V   abort below this load voltage, default 4.9
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

NODE = "stance_walk"
logger = get_logger(NODE)

STANCE_NAMES = [
    s.strip().lower()
    for s in os.environ.get("PIBOT_STANCEWALK_STANCES", "narrow,neutral,wide").split(",")
    if s.strip()
]
CYCLES = max(1, min(10, int(os.environ.get("PIBOT_STANCEWALK_CYCLES", "3"))))
SPEED = max(2, min(10, int(os.environ.get("PIBOT_STANCEWALK_SPEED", "6"))))
ABORT_V = float(os.environ.get("PIBOT_STANCEWALK_ABORT_V", "4.9"))
STEP_TIMEOUT_S = 40.0
SETTLE_S = 1.5

# walk() maps forward/backward to y=±35; the offline check must replay the
# same stride the robot will be asked for.
WALK_Y = 35


def build_steps() -> list:
    steps = []
    for name in STANCE_NAMES:
        stance = stances.STANCES.get(name)
        if stance is None:
            logger.warning(f"skipping unknown stance {name!r}")
            continue
        rejected = False
        for label, y in (("forward", WALK_Y), ("backward", -WALK_Y)):
            ok, lo, hi, reason = stances.validate_for_gait(stance, 0, y, 0, SPEED)
            if not ok:
                logger.warning(f"skipping {name}: walking {label} fails offline check — {reason}")
                rejected = True
                break
            logger.info(
                f"offline check ok: {name} walking {label}, "
                f"mid-gait reach {lo:.1f}..{hi:.1f}mm"
            )
        if rejected:
            continue
        steps.append((f"stance {name}", "set_stance", {"stance": name}))
        steps.append(
            (f"{name}: walk forward {CYCLES}", "walk",
             {"direction": "forward", "steps": CYCLES, "speed": SPEED})
        )
        steps.append(
            (f"{name}: walk backward {CYCLES}", "walk",
             {"direction": "backward", "steps": CYCLES, "speed": SPEED})
        )
    steps.append(("stance neutral", "set_stance", {"stance": "neutral"}))
    steps.append(("relax servos", "relax", {"enabled": True}))
    return steps


def main() -> None:
    node = Node()
    steps = build_steps()
    walkable = sum(1 for s in steps if s[1] == "walk") // 2

    if walkable == 0:
        logger.error("no stance passed the offline gait check; nothing to do")

    logger.info(
        f"stance-walk test: {walkable} stance(s), {CYCLES} cycles each way at "
        f"speed {SPEED}, abort below {ABORT_V:.2f}V"
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
        pending = f"w{index}"
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
            refused = bool(payload.get("refused")) or "FAILED" in text or "rejected" in text
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
    logger.info("stance-walk summary")
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


if __name__ == "__main__":
    main()
