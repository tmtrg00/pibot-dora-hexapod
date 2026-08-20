#!/usr/bin/env python3
"""Idle-reset test node — does the robot stand back up when it stops?

A stance is a pose adopted for a purpose: `brace` to be stable, `crouch` to
drop the centre of gravity, `tall` to see over something. The purpose ends when
the movement does, but the pose used to persist until some later command
happened to change it — leaving the robot hunkered or splayed indefinitely,
with `wide` and `brace` in particular holding the legs near the outer end of
their reach where the servos work hardest just to hold the body up.

The rule added on 2026-08-19: once nothing has moved for
`PIBOT_IDLE_STANCE_RESET_S` seconds, return to `neutral`.

This graph shows the rule working and, just as importantly, shows it declining
to work when it should:

  1. Adopt a non-neutral stance, then do nothing. The robot should stand back
     up on its own after the idle interval. WATCH IT — this is the whole rule.
  2. Adopt one again, then `relax`. The robot should NOT stand up. Torque off
     is a state someone asked for, and silently re-energising the servos would
     both surprise them and draw current they were trying to save.
  3. Adopt one and keep it busy with small movements. It should stay in the
     stance while it is being used, because a stance is usually set precisely
     so that the movements which follow happen in it.

MOTION: changes pose and shifts the body, but does not travel. Needs servo
power and a charged pack; no floor space beyond the robot's own footprint.

Env:
  PIBOT_IDLE_STANCE      stance to adopt, default brace
  PIBOT_IDLE_WATCH_S     seconds to watch after each setup, default 30
  PIBOT_IDLE_ABORT_V     abort below this load voltage, default 4.9
  PIBOT_IDLE_STANCE_RESET_S   the rule's interval, read by the hardware node
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

NODE = "idle_reset"
logger = get_logger(NODE)

STANCE = os.environ.get("PIBOT_IDLE_STANCE", "brace").strip().lower()
WATCH_S = max(5.0, float(os.environ.get("PIBOT_IDLE_WATCH_S", "30")))
ABORT_V = float(os.environ.get("PIBOT_IDLE_ABORT_V", "4.9"))
RESET_S = float(os.environ.get("PIBOT_IDLE_STANCE_RESET_S", "20"))
STEP_TIMEOUT_S = 60.0


def build_steps() -> list:
    if STANCE not in stances.STANCES:
        logger.error(
            f"unknown stance {STANCE!r}; available: {', '.join(sorted(stances.STANCES))}"
        )
        return []
    ok, reaches, reason = stances.validate(stances.STANCES[STANCE])
    if not ok:
        logger.error(f"stance {STANCE!r} fails the offline reach check — {reason}")
        return []

    # (label, tool, args, seconds to wait afterwards, what to watch for)
    return [
        (f"1a: adopt {STANCE!r}", "set_stance", {"stance": STANCE}, 2.0,
         None),
        ("1a': turn the head aside", "move_head", {"pan": 30, "tilt": 15}, 1.0,
         None),
        (f"1b: stand idle for {WATCH_S:.0f}s", None, None, WATCH_S,
         f"the robot SHOULD stand back up to neutral about {RESET_S:.0f}s in, "
         f"and the head should return to level with it"),

        (f"2a: adopt {STANCE!r} again", "set_stance", {"stance": STANCE}, 2.0,
         None),
        ("2b: relax the servos", "relax", {"enabled": True}, 1.0, None),
        (f"2c: stand idle for {WATCH_S:.0f}s", None, None, WATCH_S,
         "the robot should NOT stand up — the servos were relaxed on purpose"),

        ("3a: re-enable torque", "relax", {"enabled": False}, 1.5, None),
        (f"3b: adopt {STANCE!r} once more", "set_stance", {"stance": STANCE}, 1.0,
         None),
        ("3c: keep it busy — small body shifts", None, None, 0.0,
         "the robot should STAY in the stance while it is being used"),
    ]


def main() -> None:
    node = Node()
    steps = build_steps()
    if not steps:
        logger.error("nothing to do; exiting")
        return

    logger.info(
        f"idle-reset test: stance {STANCE}, reset interval {RESET_S:.0f}s, "
        f"watching {WATCH_S:.0f}s each time, abort below {ABORT_V:.2f}V"
    )
    if WATCH_S <= RESET_S:
        logger.warning(
            f"PIBOT_IDLE_WATCH_S ({WATCH_S:.0f}s) is not longer than the reset "
            f"interval ({RESET_S:.0f}s) — the reset will not have time to fire."
        )

    index = -1
    pending = None
    sent_at = 0.0
    next_at = time.time() + 2.0
    results = []
    resets_seen = []
    battery_min = None
    aborted = False
    # Step 3 keeps the robot busy by nudging it faster than the reset interval.
    busy_until = 0.0
    busy_next = 0.0

    def send_next():
        nonlocal index, pending, sent_at, next_at, busy_until, busy_next
        index += 1
        if index >= len(steps):
            pending = None
            return
        label, name, args, wait, watch = steps[index]
        logger.info(f"[{index + 1}/{len(steps)}] {label}")
        if watch:
            logger.info(f"    WATCH: {watch}")

        if label.startswith("3c"):
            # Nudge every third of the reset interval, for two intervals'
            # worth. If the rule fires during this, it is firing while the
            # robot is in use, which would be wrong.
            busy_until = time.time() + RESET_S * 2
            busy_next = time.time()
            pending = None
            next_at = busy_until + 2.0
            return

        if name is None:
            pending = None
            next_at = time.time() + wait
            return

        pending = f"c{index}"
        sent_at = time.time()
        node.send_output("tool_call", encode({"id": pending, "name": name, "args": args}))

    def abort(reason):
        nonlocal aborted, steps, index
        if aborted:
            return
        aborted = True
        logger.error(f"ABORT: {reason}")
        steps = steps[: index + 1] + [
            ("abort: neutral", "set_stance", {"stance": "neutral"}, 1.0, None),
            ("abort: relax", "relax", {"enabled": True}, 0.0, None),
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

        elif event["id"] == "health":
            payload = decode(event) or {}
            if "stance_reset" in payload:
                step = steps[index][0] if 0 <= index < len(steps) else "?"
                resets_seen.append((time.time(), step))
                logger.info(f"    >>> HARDWARE RESET THE STANCE (during {step})")

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
            if now < busy_until:
                if now >= busy_next:
                    busy_next = now + RESET_S / 3.0
                    logger.info("    nudging (keeping the robot in use)")
                    node.send_output(
                        "tool_call",
                        encode({"id": f"busy{int(now)}", "name": "set_position",
                                "args": {"x": 0, "y": 0, "z": 5}}),
                    )
                continue
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
    logger.info("idle-reset summary")
    logger.info("=" * 78)
    if resets_seen:
        for _, step in resets_seen:
            logger.info(f"  stance reset fired during: {step}")
    else:
        logger.info("  the hardware node never reported a stance reset")
    logger.info("")
    logger.info("  EXPECTED: exactly one reset, during step 1b.")
    logger.info("  A reset during 2c would mean relaxed servos were re-energised.")
    logger.info("  A reset during 3c would mean the rule fires while the robot is in use.")
    during = {step[:2] for _, step in resets_seen}
    verdict = (
        "PASS" if during == {"1b"}
        else f"CHECK — resets fired during {sorted(during) or 'nothing'}, expected just 1b"
    )
    logger.info(f"  verdict: {verdict}")
    logger.info("=" * 78)
    good = sum(1 for r in results if not r[2])
    logger.info(
        f"{good}/{len(results)} commands ok"
        + (f", lowest battery {battery_min:.2f}V" if battery_min else "")
    )
    if aborted:
        logger.warning("Run was ABORTED.")

    # Stop the timer-driven device nodes so `dora run` returns instead of
    # idling on their ticks after this driver exits (CHANGELOG 2026-08-20).
    common.send_shutdown(node)


if __name__ == "__main__":
    main()
