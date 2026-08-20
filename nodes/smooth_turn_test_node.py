#!/usr/bin/env python3
"""Smooth-turn test node — is the turn continuous, and does it land?

Turning used to stutter. Not as a tuning problem but a structural one: a turn
has no stride, and `condition_monitor` treated any strideless command as
single-shot — one gait cycle, then the queue was cleared. So `turn_to` had to
drive the rotation from outside as a sequence of separate one-cycle commands,
each waiting for the queue to clear and pausing to let the body settle before
measuring. The robot turned in visible discrete lurches.

Since 2026-08-19 the single-shot rule applies only to the genuine stop-and-stand
command, so a turn stays queued and `run_gait` re-enters it cycle after cycle.
`turn_to` now runs ONE continuous command and re-trims its steering angle as
the robot rotates.

This graph checks the two things that could still be wrong.

**Smoothness** is yours to judge — watch the robot, not the log. It should
rotate continuously, without the stop-start it had before. There should be no
pause between gait cycles.

**Accuracy** the log settles. Turning is now open to a failure the stuttering
version did not have: a gait cycle cannot be interrupted, so the robot always
finishes the cycle it is in. `turn_to` handles that by predicting where the
in-flight cycle will end and shrinking the angle as the target nears, and the
sequence below is built to expose it if that goes wrong:

  * four 90deg turns, which return the robot to its starting heading. Errors
    accumulate rather than cancelling, so this is far more revealing than one
    90deg turn — mark the robot's facing before it starts and compare at the end.
  * a 180deg turn each way, where the angle has to wind all the way up and back
    down again.
  * two 20deg turns, which must complete in about one gait cycle. This is the
    case that catches a planner that only reconsiders at cycle boundaries: it
    will overshoot a short turn badly, having committed to a large angle it
    cannot take back.

LOCOMOTION: rotates in place, does not travel. Needs room to turn and a
charged pack.

Env:
  PIBOT_SMOOTHTURN_TOLERANCE  stop tolerance in degrees, default 5
  PIBOT_SMOOTHTURN_PAUSE      pause between turns, default 3
  PIBOT_SMOOTHTURN_ABORT_V    abort below this load voltage, default 4.9
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

NODE = "smooth_turn"
logger = get_logger(NODE)

TOLERANCE = max(2.0, min(45.0, float(os.environ.get("PIBOT_SMOOTHTURN_TOLERANCE", "5"))))
PAUSE_S = max(1.0, float(os.environ.get("PIBOT_SMOOTHTURN_PAUSE", "3")))
ABORT_V = float(os.environ.get("PIBOT_SMOOTHTURN_ABORT_V", "4.9"))
STEP_TIMEOUT_S = 180.0

# (label, degrees). Grouped so the robot returns to its starting heading at the
# end of each group, which is what makes the error visible without instruments.
SEQUENCE = [
    ("A1: quarter turn right", 90),
    ("A2: quarter turn right", 90),
    ("A3: quarter turn right", 90),
    ("A4: quarter turn right — should be back where it started", 90),
    ("B1: half turn left", -180),
    ("B2: half turn right — back to start again", 180),
    ("C1: small turn right (should take about ONE gait cycle)", 20),
    ("C2: small turn left — back to start", -20),
]


def main() -> None:
    node = Node()

    steps = [("stance neutral", "set_stance", {"stance": "neutral"})]
    for label, degrees in SEQUENCE:
        steps.append((label, "turn_to", {"degrees": degrees, "tolerance": TOLERANCE}))
    steps.append(("stance neutral", "set_stance", {"stance": "neutral"}))
    steps.append(("relax servos", "relax", {"enabled": True}))

    logger.info(
        f"smooth-turn test: {len(SEQUENCE)} turns, tolerance {TOLERANCE:.0f}deg, "
        f"abort below {ABORT_V:.2f}V"
    )
    logger.info("MARK THE ROBOT'S FACING DIRECTION NOW — a strip of tape under the nose.")
    logger.info("WATCH THE ROBOT: each turn should be one continuous rotation, no stutter.")

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
                or "refused" in text
                or "aborted" in text
            )
            logger.info(f"    {'FAILED' if refused else 'ok'}: {text}")
            results.append((steps[index][0], steps[index][2].get("degrees"), text, refused))
            if refused:
                abort(f"step {steps[index][0]!r} did not complete")
            pending = None
            next_at = time.time() + PAUSE_S

        elif event["id"] == "tick":
            now = time.time()
            if pending is not None:
                if now - sent_at > STEP_TIMEOUT_S:
                    logger.warning("    no result within timeout")
                    results.append((steps[index][0], None, "no result within timeout", True))
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

    def residual_of(text):
        marker = "residual "
        if marker not in text:
            return None
        try:
            return float(text.split(marker, 1)[1].split("deg", 1)[0])
        except ValueError:
            return None

    logger.info("=" * 80)
    logger.info("smooth-turn summary")
    logger.info("=" * 80)
    logger.info(f"{'turn':<52} {'asked':>7} {'residual':>9}")
    residuals = []
    for label, degrees, text, failed in results:
        r = residual_of(text)
        if r is not None:
            residuals.append(r)
        shown = f"{r:+.1f}" if r is not None else "-"
        asked = f"{degrees:+d}" if isinstance(degrees, int) else "-"
        logger.info(f"{('FAILED ' if failed else '') + label:<52} {asked:>7} {shown:>9}")

    logger.info("=" * 80)
    if residuals:
        worst = max(abs(r) for r in residuals)
        logger.info(f"  worst residual {worst:.1f}deg against a {TOLERANCE:.0f}deg tolerance")
        logger.info(f"  accumulated error over all turns: {sum(residuals):+.1f}deg")
        logger.info(
            f"  verdict: {'PASS' if worst <= TOLERANCE else 'OUT OF TOLERANCE'}"
        )
    logger.info("")
    logger.info("  NOW LOOK AT THE ROBOT: after turns A1-A4 and again after B1-B2 it should")
    logger.info("  be facing its original direction. And the question only you can answer —")
    logger.info("  did each turn rotate smoothly, or did it still stutter between cycles?")
    logger.info("=" * 80)
    good = sum(1 for r in results if not r[3])
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
