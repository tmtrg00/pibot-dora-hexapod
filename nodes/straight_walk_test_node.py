#!/usr/bin/env python3
"""Straight-walk test node — walks open-loop, then closed-loop, and compares.

The point of this graph is not "does it walk" — `stancewalk` already proved
that. It is "does it walk *straight*", and the only honest way to answer that
is to measure the drift with and without correction and put the numbers side
by side. So each round walks the same distance twice:

  1. **Uncorrected** — `walk_straight` with `gain: 0`. That runs the identical
     code path with the identical gyro instrumentation and simply never acts
     on the error, which is a fairer baseline than calling the old open-loop
     `walk` would be: `walk` reports no heading at all, so comparing against it
     would mean comparing a measurement to an anecdote.

  2. **Corrected** — `walk_straight` at the configured gain.

Both report their final heading error, so the summary is a direct before/after
on the same floor, the same battery and the same stance, with exactly one
variable changed.

The run opens with a small `turn_to`, which is not decoration: it is what
teaches the robot which way its gyro counts (see nodes/heading.py) and it
proves the gyro responds at all before any number from it is trusted. Once
learned the sign is remembered in data/gyro_sense.json, but the step is cheap
and repeating it keeps the test self-contained.

Run the closed-loop leg only (the normal case) with PIBOT_STRAIGHT_COMPARE=0.

LOCOMOTION: the robot travels forward and back. Needs a couple of metres of
clear floor ahead, servo power and a charged pack. Put a strip of tape on the
floor and eyeball the robot against it — the gyro number and your eye should
agree, and if they disagree the gyro is the thing to doubt.

Env:
  PIBOT_STRAIGHT_STANCE    stance to walk in, default neutral
  PIBOT_STRAIGHT_CYCLES    gait cycles per leg, default 6
  PIBOT_STRAIGHT_SPEED     gait speed 2-10, default 6
  PIBOT_STRAIGHT_ROUNDS    out-and-back round trips, default 1
  PIBOT_STRAIGHT_COMPARE   1 = also run an uncorrected leg, default 1
  PIBOT_STRAIGHT_ABORT_V   abort below this load voltage, default 4.9
  PIBOT_HEADING_GAIN       steering gain, default 0.12 (read by hardware node)
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

NODE = "straight_walk"
logger = get_logger(NODE)

STANCE = os.environ.get("PIBOT_STRAIGHT_STANCE", "neutral").strip().lower()
CYCLES = max(1, min(20, int(os.environ.get("PIBOT_STRAIGHT_CYCLES", "6"))))
SPEED = max(2, min(10, int(os.environ.get("PIBOT_STRAIGHT_SPEED", "6"))))
ROUNDS = max(1, min(5, int(os.environ.get("PIBOT_STRAIGHT_ROUNDS", "1"))))
COMPARE = os.environ.get("PIBOT_STRAIGHT_COMPARE", "1").lower() in {"1", "true", "yes"}
ABORT_V = float(os.environ.get("PIBOT_STRAIGHT_ABORT_V", "4.9"))

# A closed-loop walk stands, calibrates the gyro for a second, walks, then
# stands again; the cycles themselves are ~0.8s at speed 6.
STEP_TIMEOUT_S = 120.0
SETTLE_S = 2.0

WALK_STRIDE_MM = 35


def build_steps() -> list:
    stance = stances.STANCES.get(STANCE)
    if stance is None:
        logger.error(
            f"unknown stance {STANCE!r}; available: {', '.join(sorted(stances.STANCES))}"
        )
        return []

    # Same offline gate the other walking graphs use: a stance that drops
    # frames mid-gait would show up as heading noise and be blamed on the
    # controller.
    for label, x, y in (("forward", 0, WALK_STRIDE_MM), ("backward", 0, -WALK_STRIDE_MM)):
        ok, lo, hi, reason = stances.validate_for_gait(stance, x, y, 0, SPEED)
        if not ok:
            logger.error(f"walking {label} in {STANCE!r} fails offline check — {reason}")
            return []
        logger.info(
            f"offline check ok: {STANCE} walking {label}, mid-gait reach {lo:.1f}..{hi:.1f}mm"
        )

    steps = [
        (f"stance {STANCE}", "set_stance", {"stance": STANCE}),
        # Establishes the gyro sign before any walk relies on it, and fails
        # loudly here rather than silently producing a meaningless heading
        # number later if the IMU is not responding.
        ("gyro check: turn right 20deg", "turn_to", {"degrees": 20, "tolerance": 8}),
        ("gyro check: turn back", "turn_to", {"degrees": -20, "tolerance": 8}),
    ]
    for r in range(ROUNDS):
        tag = f" (round {r + 1}/{ROUNDS})" if ROUNDS > 1 else ""
        if COMPARE:
            steps.append(
                (f"UNCORRECTED forward {CYCLES}{tag}", "walk_straight",
                 {"direction": "forward", "cycles": CYCLES, "speed": SPEED,
                  "_uncorrected": True})
            )
            steps.append(
                (f"UNCORRECTED back {CYCLES}{tag}", "walk_straight",
                 {"direction": "backward", "cycles": CYCLES, "speed": SPEED,
                  "_uncorrected": True})
            )
        steps.append(
            (f"CORRECTED forward {CYCLES}{tag}", "walk_straight",
             {"direction": "forward", "cycles": CYCLES, "speed": SPEED})
        )
        steps.append(
            (f"CORRECTED back {CYCLES}{tag}", "walk_straight",
             {"direction": "backward", "cycles": CYCLES, "speed": SPEED})
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
        f"straight-walk test: stance {STANCE}, {ROUNDS} round(s) of {CYCLES} cycles "
        f"at speed {SPEED}, "
        + ("uncorrected vs corrected" if COMPARE else "corrected only")
        + f", abort below {ABORT_V:.2f}V"
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
        args = dict(args)
        # The uncorrected leg is the same code path with the steering gain
        # zeroed, so the comparison isolates exactly one variable.
        if args.pop("_uncorrected", False):
            args["gain"] = 0.0
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
            # Guard the experiment itself. The baseline leg is only a baseline
            # if the steering gain really reached the hardware node; the first
            # time this ran it did not — `gain` was dropped in tool dispatch,
            # so both legs were corrected and the comparison silently measured
            # nothing (2026-08-19). The hardware node says "measured
            # uncorrected" only when the gain actually arrived as zero, so
            # check for it rather than trusting that the argument got through.
            if steps[index][0].startswith("UNCORRECTED") and "measured uncorrected" not in text:
                refused = True
                text = (
                    "BASELINE INVALID: this leg was supposed to run with the "
                    "steering gain at zero, but the hardware node did not report "
                    "it as uncorrected. The comparison would be meaningless. "
                    + text
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

    logger.info("=" * 78)
    logger.info("straight-walk summary")
    logger.info("=" * 78)
    for label, text, failed in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label:<32} {text}")

    # Pull the heading numbers back out of the result text so the verdict does
    # not depend on the operator reading eight log lines.
    def errors_for(prefix):
        out = []
        for label, text, failed in results:
            if failed or not label.startswith(prefix):
                continue
            marker = "final heading error "
            if marker in text:
                try:
                    out.append(abs(float(text.split(marker, 1)[1].split("deg", 1)[0])))
                except ValueError:
                    pass
        return out

    uncorrected = errors_for("UNCORRECTED")
    corrected = errors_for("CORRECTED")
    logger.info("=" * 78)
    if uncorrected:
        logger.info(
            f"  uncorrected drift: {', '.join(f'{e:.1f}deg' for e in uncorrected)} "
            f"(mean {sum(uncorrected) / len(uncorrected):.1f}deg)"
        )
    if corrected:
        logger.info(
            f"  corrected drift:   {', '.join(f'{e:.1f}deg' for e in corrected)} "
            f"(mean {sum(corrected) / len(corrected):.1f}deg)"
        )
    if uncorrected and corrected:
        before = sum(uncorrected) / len(uncorrected)
        after = sum(corrected) / len(corrected)
        verdict = "BETTER" if after < before else "NO BETTER"
        logger.info(f"  verdict: heading hold is {verdict} ({before:.1f}deg -> {after:.1f}deg)")
    logger.info("=" * 78)
    good = sum(1 for r in results if not r[2])
    logger.info(
        f"{good}/{len(results)} steps ok"
        + (f", lowest battery {battery_min:.2f}V" if battery_min else "")
    )
    if aborted:
        logger.warning("Run was ABORTED - robot returned to neutral and relaxed.")


if __name__ == "__main__":
    main()
