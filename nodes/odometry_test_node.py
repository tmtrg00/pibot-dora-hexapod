#!/usr/bin/env python3
"""Odometry test node — does a walk travel the distance it was asked for?

Two questions, and the second is the sharper one.

**Does a walk run the cycles it was told to?** It used to not. `walk` queued a
gait command, slept for `steps` x an *estimated* cycle duration, then queued a
stop. The estimate counts `run_gait`'s 10ms-per-frame sleep and ignores the 18
servo writes each frame also spends on the I2C bus, so it always undershoots
reality — and the walk stopped early by however much the estimate was wrong.
Now the gait engine counts completed cycles (`Control.gait_cycles`) and the
walk waits on that count instead, so "6 cycles" means six.

**Is the distance now independent of speed?** This is the part you can verify
with a ruler and no trust in any number the robot reports. Distance per cycle
is fixed by the stride, so six cycles should cover the same ground whether the
gait runs fast or slow. Under the old timing that was false: the estimate's
error varies sharply with speed (at speed 9 it predicts 0.40s per cycle, at
speed 3 it predicts 1.18s), so the same command travelled different distances
at different speeds. This graph walks the same 6 cycles at three speeds,
stopping between each, so you can mark the floor and compare three gaps that
should now be equal.

Part B checks turns, where the old behaviour was worst rather than merely
inaccurate: a turn has x=0,y=0, which is the *single-shot* branch in
`condition_monitor` — one cycle, then the queue is cleared. Nothing re-queued
it, so `steps` was silently ignored for turns entirely and every turn command
ran exactly one cycle no matter what was asked (this is what made 23 commanded
cycles produce about 5 real ones, CHANGELOG 2026-08-18). So `turn_right x3`
should now rotate visibly about three times as far as `turn_right x1`.

LOCOMOTION: travels roughly 60-70cm forward, then returns, then turns in
place. Needs about a metre of clear floor ahead and room to rotate.

MEASURE THIS: put a mark on the floor at the robot's starting point. It pauses
PIBOT_ODO_PAUSE seconds after each leg — mark the floor each time. The three
gaps should be equal to within a centimetre or two.

Env:
  PIBOT_ODO_STANCE   stance to walk in, default neutral
  PIBOT_ODO_CYCLES   cycles per leg, default 6
  PIBOT_ODO_SPEEDS   comma-separated speeds to compare, default 3,6,9
  PIBOT_ODO_PAUSE    seconds to pause for marking, default 6
  PIBOT_ODO_TURNS    1 = also run the turn comparison, default 1
  PIBOT_ODO_ABORT_V  abort below this load voltage, default 4.9
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

NODE = "odometry"
logger = get_logger(NODE)

STANCE = os.environ.get("PIBOT_ODO_STANCE", "neutral").strip().lower()
CYCLES = max(1, min(20, int(os.environ.get("PIBOT_ODO_CYCLES", "6"))))
SPEEDS = [
    max(2, min(10, int(s)))
    for s in os.environ.get("PIBOT_ODO_SPEEDS", "3,6,9").split(",")
    if s.strip()
] or [3, 6, 9]
PAUSE_S = max(0.0, float(os.environ.get("PIBOT_ODO_PAUSE", "6")))
TURNS = os.environ.get("PIBOT_ODO_TURNS", "1").lower() in {"1", "true", "yes"}
ABORT_V = float(os.environ.get("PIBOT_ODO_ABORT_V", "4.9"))

STEP_TIMEOUT_S = 180.0
WALK_STRIDE_MM = 35


def estimated_cycle_s(speed: int) -> float:
    """What the old timing-based walk believed a cycle cost, for comparison."""
    frames = round((22 - 126) * (speed - 2) / (10 - 2) + 126)
    return max(0.2, frames * 0.01 + 0.05)


def build_steps() -> list:
    stance = stances.STANCES.get(STANCE)
    if stance is None:
        logger.error(
            f"unknown stance {STANCE!r}; available: {', '.join(sorted(stances.STANCES))}"
        )
        return []

    for speed in SPEEDS:
        ok, lo, hi, reason = stances.validate_for_gait(stance, 0, WALK_STRIDE_MM, 0, speed)
        if not ok:
            logger.error(f"walking in {STANCE!r} at speed {speed} fails offline check — {reason}")
            return []
    logger.info(f"offline check ok: {STANCE} walks at speeds {SPEEDS}")

    steps = [(f"stance {STANCE}", "set_stance", {"stance": STANCE}, 1.5)]

    # Part A. Heading-hold walking, so the distance measured is along a
    # straight line rather than an arc — otherwise drift, not cycle counting,
    # would dominate what the ruler sees.
    for speed in SPEEDS:
        steps.append((
            f"A: forward {CYCLES} cycles @ speed {speed} "
            f"(old estimate {estimated_cycle_s(speed):.2f}s/cycle) — MARK THE FLOOR",
            "walk_straight",
            {"direction": "forward", "cycles": CYCLES, "speed": speed},
            PAUSE_S,
        ))

    steps.append((
        f"A: return home ({CYCLES * len(SPEEDS)} cycles back)",
        "walk_straight",
        {"direction": "backward", "cycles": CYCLES * len(SPEEDS), "speed": 6},
        2.0,
    ))

    # Part B. `steps` used to be ignored entirely for turns.
    if TURNS:
        steps.append((
            "B: walk turn_right x1 — note the angle",
            "walk", {"direction": "turn_right", "steps": 1, "speed": 6}, PAUSE_S,
        ))
        steps.append((
            "B: walk turn_right x3 — should be about 3x the last one",
            "walk", {"direction": "turn_right", "steps": 3, "speed": 6}, PAUSE_S,
        ))
        steps.append((
            "B: walk turn_left x4 — back to roughly the start heading",
            "walk", {"direction": "turn_left", "steps": 4, "speed": 6}, 2.0,
        ))

    steps.append(("stance neutral", "set_stance", {"stance": "neutral"}, 1.0))
    steps.append(("relax servos", "relax", {"enabled": True}, 0.0))
    return steps


def main() -> None:
    node = Node()
    steps = build_steps()
    if not steps:
        logger.error("nothing to do; exiting")
        return

    logger.info(
        f"odometry test: stance {STANCE}, {CYCLES} cycles per leg at speeds "
        f"{SPEEDS}, pausing {PAUSE_S:.0f}s to mark the floor, "
        + ("turn comparison on" if TURNS else "turn comparison off")
        + f", abort below {ABORT_V:.2f}V"
    )
    logger.info("MARK THE FLOOR at the robot's start point now, and after each leg.")

    index = -1
    pending = None
    sent_at = 0.0
    next_at = time.time() + 3.0
    results = []
    battery_min = None
    aborted = False

    def send_next():
        nonlocal index, pending, sent_at
        index += 1
        if index >= len(steps):
            pending = None
            return
        label, name, args, _ = steps[index]
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
            ("stance neutral (abort)", "set_stance", {"stance": "neutral"}, 1.0),
            ("relax servos (abort)", "relax", {"enabled": True}, 0.0),
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
                or "short of the" in text
            )
            logger.info(f"    {'FAILED' if refused else 'ok'}: {text}")
            results.append((steps[index][0], text, refused))
            if refused:
                abort(f"step {steps[index][0]!r} did not do what it was asked")
            pause = steps[index][3]
            if pause:
                logger.info(f"    pausing {pause:.0f}s — MARK THE FLOOR")
            pending = None
            next_at = time.time() + max(1.0, pause)

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

    logger.info("=" * 84)
    logger.info("odometry summary")
    logger.info("=" * 84)
    for label, text, failed in results:
        logger.info(f"  {'FAILED' if failed else 'ok    '}  {label}")
        logger.info(f"          {text}")
    logger.info("=" * 84)
    logger.info("Cycle timing, estimated (the old basis for stopping) vs measured:")
    for speed in SPEEDS:
        for label, text, _ in results:
            if f"speed {speed}" in label and "per cycle measured" in text:
                measured = text.split("per cycle measured")[0].split(",")[-1].strip()
                logger.info(
                    f"  speed {speed}: estimate {estimated_cycle_s(speed):.2f}s, "
                    f"measured {measured}"
                )
                break
    logger.info("=" * 84)
    logger.info(
        f"NOW MEASURE: the {len(SPEEDS)} forward legs were all {CYCLES} cycles, so the "
        f"gaps you marked should be EQUAL regardless of speed."
    )
    good = sum(1 for r in results if not r[2])
    logger.info(
        f"{good}/{len(results)} steps ok"
        + (f", lowest battery {battery_min:.2f}V" if battery_min else "")
    )
    if aborted:
        logger.warning("Run was ABORTED - robot returned to neutral and relaxed.")


if __name__ == "__main__":
    main()
