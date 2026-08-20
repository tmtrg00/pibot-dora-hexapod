#!/usr/bin/env python3
"""Turn node — rotates the hexapod in place through the dora graph.

Turning is locomotion: unlike the pose test, it runs full gait cycles, so the
servos draw sustained current rather than one brief lift. On a marginal pack
that is the difference between a dip and a collapse. So the turn is broken into
short segments with a battery check between each, and the run aborts rather
than pressing on into a brownout mid-stride.

Rotation per gait cycle is *not* assumed. `walk(turn_*)` passes angle=8 to the
gait engine, but how many degrees of body rotation that produces per cycle
depends on how the stance phase accumulates, so it is measured instead:

  PIBOT_TURN_CYCLES=5 ./run.sh turn      run 5 cycles, observe how far it went
  PIBOT_TURN_DEG_PER_CYCLE=16 ./run.sh turn
                                          having measured, ask for a full 360

Closed-loop mode (PIBOT_TURN_CLOSED_LOOP=1) hands the whole rotation to the
hardware node's turn_to tool instead: the hardware node integrates the gyro,
segments the turn itself and re-plans from measured rotation, so no
DEG_PER_CYCLE figure is needed and the result reports the residual error.
This node then just sends one tool call and waits.

  PIBOT_TURN_CLOSED_LOOP=1 PIBOT_TURN_DEGREES=90 ./run.sh turn

Env:
  PIBOT_TURN_CYCLES          total gait cycles to run (calibration mode)
  PIBOT_TURN_DEG_PER_CYCLE   measured degrees per cycle; sets cycles for 360
  PIBOT_TURN_DEGREES         target rotation, default 360 (needs DEG_PER_CYCLE)
  PIBOT_TURN_DIRECTION       turn_right (default) or turn_left
  PIBOT_TURN_SEGMENT         cycles per walk call, default 5, max 10
  PIBOT_TURN_ABORT_V         abort below this load voltage, default 4.9
  PIBOT_TURN_CLOSED_LOOP     1 = one turn_to call closed on the gyro
  PIBOT_TURN_TOLERANCE       closed-loop stop tolerance in degrees, default 5
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger

common.bootstrap()

from dora import Node  # noqa: E402

NODE = "turn"
logger = get_logger(NODE)

DIRECTION = os.environ.get("PIBOT_TURN_DIRECTION", "turn_right")
SPEED = int(os.environ.get("PIBOT_TURN_SPEED", "6"))
SEGMENT = max(1, min(10, int(os.environ.get("PIBOT_TURN_SEGMENT", "5"))))
ABORT_V = float(os.environ.get("PIBOT_TURN_ABORT_V", "4.9"))
TARGET_DEGREES = float(os.environ.get("PIBOT_TURN_DEGREES", "360"))

# Measured on hardware 2026-08-16: 23 gait cycles of walk(turn_right) produced
# roughly 180 degrees of body rotation, so about 7.8 deg/cycle. That means the
# `angle` argument the gait engine receives (8 for a turn_* direction) maps
# about 1:1 to degrees of rotation per cycle -- the stance phase does not double
# it, which reading run_gait alone had left ambiguous.
MEASURED_DEG_PER_CYCLE = 7.8
DEG_PER_CYCLE = os.environ.get("PIBOT_TURN_DEG_PER_CYCLE")

# An explicit cycle count forces calibration mode; otherwise we solve for the
# target rotation using the measured figure.
CALIBRATION_CYCLES = os.environ.get("PIBOT_TURN_CYCLES")

CLOSED_LOOP = os.environ.get("PIBOT_TURN_CLOSED_LOOP", "").lower() in {"1", "true", "yes"}
TOLERANCE = float(os.environ.get("PIBOT_TURN_TOLERANCE", "5"))

STEP_TIMEOUT_S = 40.0
# A single turn_to call covers the whole rotation plus a stand at each end;
# budget generously rather than aborting a healthy turn.
TURN_TO_TIMEOUT_S = 300.0
SETTLE_S = 1.2


def planned_cycles() -> tuple:
    """Return (total_cycles, description)."""
    if CALIBRATION_CYCLES:
        cycles = int(CALIBRATION_CYCLES)
        return cycles, f"{cycles} cycles (calibration - measure the angle turned)"
    per = float(DEG_PER_CYCLE) if DEG_PER_CYCLE else MEASURED_DEG_PER_CYCLE
    if per <= 0:
        raise SystemExit("PIBOT_TURN_DEG_PER_CYCLE must be positive")
    cycles = max(1, int(math.ceil(TARGET_DEGREES / per)))
    return cycles, f"{TARGET_DEGREES:.0f}deg at {per:.1f}deg/cycle"


def main() -> None:
    node = Node()

    if CLOSED_LOOP:
        # The hardware node owns the whole rotation: gyro integration,
        # segmenting, re-planning and the battery checks between segments.
        signed = TARGET_DEGREES if DIRECTION == "turn_right" else -TARGET_DEGREES
        total_cycles = 0
        logger.info(
            f"turn test (closed loop): turn_to {signed:+.0f}deg, "
            f"tolerance {TOLERANCE:.1f}deg, abort below {ABORT_V:.2f}V"
        )
        steps = [
            (f"turn_to {signed:+.0f}deg", "turn_to", {"degrees": signed, "tolerance": TOLERANCE}),
            ("relax servos", "relax", {"enabled": True}),
        ]
    else:
        total_cycles, description = planned_cycles()

        segments = []
        remaining = total_cycles
        while remaining > 0:
            take = min(SEGMENT, remaining)
            segments.append(take)
            remaining -= take

        logger.info(
            f"turn test: {DIRECTION}, {description}, "
            f"{len(segments)} segment(s) of up to {SEGMENT} cycles, abort below {ABORT_V:.2f}V"
        )

        # stand first, then the segments, then stand and relax.
        steps = [("stand neutral", "stand", {})]
        for i, count in enumerate(segments):
            steps.append(
                (
                    f"turn segment {i + 1}/{len(segments)} ({count} cycles)",
                    "walk",
                    {"direction": DIRECTION, "steps": count, "speed": SPEED},
                )
            )
        steps.append(("stand neutral", "stand", {}))
        steps.append(("relax servos", "relax", {"enabled": True}))

    index = -1
    pending_id = None
    sent_at = 0.0
    next_at = time.time() + 2.0
    results: list = []
    battery_min = None
    battery_last = None
    aborted = False
    cycles_done = 0

    def send_next() -> None:
        nonlocal index, pending_id, sent_at
        index += 1
        if index >= len(steps):
            pending_id = None
            return
        label, name, args = steps[index]
        pending_id = f"t{index}"
        sent_at = time.time()
        logger.info(f"[{index + 1}/{len(steps)}] {label}")
        node.send_output("tool_call", encode({"id": pending_id, "name": name, "args": args}))

    def abort(reason: str) -> None:
        """Stop turning and put the robot down safely."""
        nonlocal aborted, steps, index
        if aborted:
            return
        aborted = True
        logger.error(f"ABORT: {reason}")
        # Drop every remaining turn segment, keep the stand + relax tail.
        steps = steps[: index + 1] + [
            ("stand neutral (abort)", "stand", {}),
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
            battery_last = (load_v, float(payload.get("pi_v", 0.0)))
            if battery_min is None or load_v < battery_min[0]:
                battery_min = battery_last
            logger.info(f"    battery load={load_v:.2f}V pi={battery_last[1]:.2f}V")
            if load_v < ABORT_V:
                abort(f"battery load {load_v:.2f}V fell below {ABORT_V:.2f}V")

        elif event["id"] == "tool_result":
            payload = decode(event) or {}
            if payload.get("id") != pending_id:
                continue
            text = str(payload.get("text") or "")
            refused = bool(payload.get("refused"))
            label, name, args = steps[index]
            if name == "walk" and not refused:
                cycles_done += int(args.get("steps", 0))
            logger.info(f"    {'REFUSED' if refused else 'ok'}: {text}")
            results.append((label, text, refused))
            if refused and name == "walk":
                abort("hardware node refused a turn segment")
            pending_id = None
            next_at = time.time() + SETTLE_S

        elif event["id"] == "tick":
            now = time.time()
            if pending_id is not None:
                step_timeout = TURN_TO_TIMEOUT_S if steps[index][1] == "turn_to" else STEP_TIMEOUT_S
                if now - sent_at > step_timeout:
                    logger.warning("    no result within timeout")
                    results.append((steps[index][0], "no result within timeout", False))
                    abort("a step did not complete in time")
                    pending_id = None
                    next_at = now + SETTLE_S
                continue
            if index >= len(steps) - 1 and index >= 0:
                break
            if now >= next_at:
                send_next()
                if index >= len(steps):
                    break

    logger.info("=" * 66)
    logger.info("turn summary")
    logger.info("=" * 66)
    for label, text, refused in results:
        logger.info(f"  {'REFUSED' if refused else 'ok     '}  {label:<34} {text}")
    logger.info("=" * 66)
    logger.info(f"gait cycles completed: {cycles_done} of {total_cycles} planned")
    if battery_min is not None:
        logger.info(
            f"battery: lowest load={battery_min[0]:.2f}V, final load={battery_last[0]:.2f}V"
        )
    if aborted:
        logger.warning("Run was ABORTED - the robot was stood up and relaxed.")
    elif not CLOSED_LOOP and not DEG_PER_CYCLE:
        logger.info(
            f"Calibration run. Measure how far the robot actually rotated, then "
            f"divide by {cycles_done} to get degrees per cycle and re-run with "
            f"PIBOT_TURN_DEG_PER_CYCLE=<value> for a full {TARGET_DEGREES:.0f}deg turn."
        )

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
