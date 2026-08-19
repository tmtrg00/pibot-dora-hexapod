#!/usr/bin/env python3
"""Hardware node — owns the entire I2C bus and all motion.

Why this node is deliberately *not* split further: both PCA9685 servo drivers
(0x40/0x41), the MPU6050 IMU (0x68) and the ADS7830 ADC (0x48) share one I2C
bus. In the single-process runtime the GIL serialised bus access for free.
Across processes nothing does — and these drivers issue multi-step
write-then-read transactions that interleave badly. So servos, IMU and battery
ADC stay fused in one process. This is the honest limit of what the dora split
buys on this hardware; see docs/DESIGN.md.

It also owns the gait thread (`Control.condition_monitor`), which is where the
upstream silent-failure scar lives: that daemon thread swallowed exceptions, so
the NumPy 2.0 `np.mat` removal broke every attitude/balance command and showed
up only as a timeout. Here the thread is watched — if it dies, this node says
so on `health` and stops claiming motion works.

Battery gate: this node reads the pack itself, so it can enforce the project's
binding pre-flight rule in code rather than by convention — no motion below
6.0V, including the servo movement that `Control()` performs at construction.
"""

from __future__ import annotations

import math
import os
import sys
import time
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import (
    BATTERY_FLOOR_V,
    MOTION_TOOLS,
    decode,
    encode,
    get_logger,
    owns,
)

common.bootstrap()

import stances  # noqa: E402
from heading import (  # noqa: E402
    HeadingHold,
    YawTracker,
    load_yaw_sign,
    save_yaw_sign,
)

from dora import Node  # noqa: E402
from src.actions import execute as run_action  # noqa: E402
from src.adc import ADC  # noqa: E402
from src.command import COMMAND as cmd  # noqa: E402
from src.control import Control  # noqa: E402

NODE = "hardware"
logger = get_logger(NODE)

# Overridable for bench work on a regulated supply, but the default is the
# safety floor from AGENTS.md and lowering it is a deliberate act.
FLOOR_V = float(os.environ.get("PIBOT_BATTERY_FLOOR", BATTERY_FLOOR_V))

# A battery reading costs ~1s on the ADS7830, so we do not take one before
# every command. Anything younger than this is good enough to gate on.
BATTERY_MAX_AGE_S = 10.0

# Telemetry-only mode: bring up the ADC but never construct Control(), which
# would drive the legs to the standing pose just by being instantiated. Lets
# the graph be exercised on a bench with nothing able to move.
NO_MOTION = os.environ.get("PIBOT_NO_MOTION", "").lower() in {"1", "true", "yes"}

# Closed-loop turning. One turn gait cycle at angle=8 rotated the body ~36deg
# (observed 2026-08-18 — the 2026-08-16 "7.8deg/cycle" was per *commanded*
# cycle, but turn commands are single-shot in condition_monitor, so 23
# commanded cycles were ~5 real ones). ~4.5deg per angle unit seeds the
# planner; each cycle's measured rotation then refines it.
TURN_SEED_DEG_PER_ANGLE_UNIT = 4.5
TURN_TOLERANCE_DEG = 5.0
TURN_SPEED = int(os.environ.get("PIBOT_TURN_SPEED", "6"))

# Heading-hold walking. The steering loop samples yaw and re-queues the gait
# command with a trimmed angle this often. Shorter than one gait cycle on
# purpose: `run_gait` picks up whatever angle is queued when it starts its next
# cycle, so sampling faster than the cycle means the freshest correction is
# always the one that gets applied.
STEER_INTERVAL_S = 0.35

# How far the stride goes in each direction, mirroring `walk` in src/actions.py
# so both open- and closed-loop walking travel the same distance per cycle.
WALK_STRIDE_MM = 35

# The steering angle used to learn the gyro's sign when this robot has never
# been measured. Small: it is a deliberate curve that the heading loop then has
# to take back out.
SIGN_PROBE_ANGLE = 1
SIGN_PROBE_MIN_DEG = 2.0
SIGN_PROBE_TIMEOUT_S = 6.0

# Log the heading this often even when the steering command is unchanged, so a
# long walk never goes silent.
HEARTBEAT_S = 3.0


def cycle_duration_estimate(gait: int, speed: int) -> float:
    """How long one gait cycle is expected to take, in seconds.

    Mirrors `_estimated_cycle_duration` in src/actions.py: `run_gait` runs F
    frames with a 10ms sleep in each, where F comes from the same speed
    mapping. It is an estimate — the 18 servo writes per frame take real bus
    time that this does not count — so anything that needs the true duration
    should measure it rather than trust this.
    """
    if gait == 1:
        frames = round((22 - 126) * (speed - 2) / (10 - 2) + 126)
    else:
        frames = round((45 - 171) * (speed - 2) / (10 - 2) + 171)
    return max(0.2, (frames * 0.01) + 0.05)


class Hardware:
    def __init__(self) -> None:
        self.adc: Optional[ADC] = None
        self.control: Optional[Control] = None
        self.last_battery: Optional[Tuple[float, float]] = None
        self.last_battery_at = 0.0
        self.blocked_reason: Optional[str] = None
        # The footprint this node last applied via set_stance (None = stock).
        # The geometry drift check compares against this, not the stock
        # footprint, or every stance change away from a spread stance would
        # misread the current stance as drift.
        self.applied_footprint = None

        # ADC first and on its own: it is the one device we must be able to
        # read *before* deciding whether it is safe to energise the servos.
        try:
            self.adc = ADC()
            logger.info("ADS7830 ready")
        except Exception as exc:
            logger.warning(f"ADC unavailable: {exc}")

        if NO_MOTION:
            self.blocked_reason = "PIBOT_NO_MOTION is set (telemetry-only mode)"
            logger.warning("telemetry-only mode: servos will not be initialised or driven")
            self.read_battery(force=True)
            return

        voltage = self.read_battery(force=True)
        if voltage is None:
            logger.warning(
                "Could not read battery before init. Bringing up servos anyway "
                "(upstream behaviour), but the motion gate will stay closed "
                "until a reading succeeds."
            )
        elif min(voltage) < FLOOR_V:
            self.blocked_reason = (
                f"battery {voltage[0]:.2f}V/{voltage[1]:.2f}V is below the "
                f"{FLOOR_V:.1f}V floor"
            )
            logger.error(
                f"REFUSING to initialise servos: {self.blocked_reason}. "
                f"Charge the pack. No motion commands will be accepted."
            )
            return

        # Control() calibrates and drives the legs to the standing pose as a
        # side effect of construction, which is why it happens after the gate.
        try:
            self.control = Control()
            self.control.condition_thread.start()
            self.control.relax(False)
            logger.info("hexapod control ready, gait thread started")
        except Exception as exc:
            logger.warning(f"Hexapod control unavailable: {exc}")
            self.control = None

    @property
    def hardware_dict(self) -> dict:
        return {
            "control": self.control,
            "servo": self.control.servo if self.control is not None else None,
            "adc": self.adc,
        }

    def read_battery(self, force: bool = False) -> Optional[Tuple[float, float]]:
        if self.adc is None:
            return None
        if not force and time.time() - self.last_battery_at < BATTERY_MAX_AGE_S:
            return self.last_battery
        try:
            load_v, pi_v = self.adc.read_battery_voltage()
        except Exception as exc:
            logger.warning(f"Battery read failed: {exc}")
            return self.last_battery
        self.last_battery = (float(load_v), float(pi_v))
        self.last_battery_at = time.time()
        return self.last_battery

    def gait_thread_alive(self) -> bool:
        return self.control is not None and self.control.condition_thread.is_alive()

    def apply_stance(self, name: str) -> Tuple[bool, str]:
        """Move to a named stance, verifying it actually took effect.

        Returns (ok, text) so the caller can set the refused flag on the tool
        result — the first stancewalk run reported "8/8 steps ok" while a
        stance had in fact been refused, because failure only lived in the
        text.
        """
        stance = stances.STANCES.get(str(name).strip().lower())
        if stance is None:
            return False, f"Unknown stance {name!r}. Available: {', '.join(sorted(stances.STANCES))}"

        ok, reaches, reason = stances.validate(stance)
        if not ok:
            return False, f"Stance {stance.name!r} rejected: {reason}"

        # The geometry in stances.py is a mirror of control.py; make sure it has
        # not drifted before trusting the reach check we just did. Compare
        # against whatever footprint we last applied, not the stock one.
        matched, detail = stances.verify_against(self.control, self.applied_footprint)
        if not matched:
            return False, f"Stance {stance.name!r} refused: {detail}"

        control = self.control
        footprint = stance.footprint()
        previous = [[p[0], p[1]] for p in control.body_points]

        # Widen or narrow the resting footprint. run_gait deep-copies
        # body_points, so this changes the walking gait too.
        for i, (x, y) in enumerate(footprint):
            control.body_points[i][0] = x
            control.body_points[i][1] = y

        def queue(parts, timeout_s=15.0):
            control.command_queue = parts
            control.timeout = time.time()
            end = time.time() + timeout_s
            while time.time() < end:
                queue_now = getattr(control, "command_queue", None)
                if isinstance(queue_now, list) and queue_now and queue_now[0] == "":
                    return True
                time.sleep(0.05)
            return False

        completed = queue([cmd.CMD_POSITION, "0", "0", str(stance.z)])
        if stance.roll or stance.pitch:
            completed = queue([cmd.CMD_ATTITUDE, str(stance.roll), str(stance.pitch), "0"]) and completed

        # set_leg_angles() silently declines to move if a leg is out of range,
        # so confirm against the robot's own validity check rather than
        # assuming the command landed.
        moved = control.check_point_validity()
        if not moved:
            for i, (x, y) in enumerate(previous):
                control.body_points[i][0] = x
                control.body_points[i][1] = y
            return False, (
                f"Stance {stance.name!r} FAILED: the robot reports its leg positions "
                f"out of range, so nothing moved. Footprint reverted."
            )

        self.applied_footprint = footprint
        actual = max(
            math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p in control.leg_positions
        )
        note = "" if completed else " (timed out waiting for the gait thread)"
        return True, (
            f"Stance set to {stance.name!r}: {stance.description}. "
            f"spread={stance.spread:.2f} z={stance.z} roll={stance.roll} pitch={stance.pitch}, "
            f"max leg reach {actual:.0f}mm (predicted {max(reaches):.0f}mm){note}"
        )

    def turn_to(self, degrees, tolerance=None) -> str:
        """Rotate in place by `degrees`, closed-loop on the z gyro.

        Runs single turn gait cycles with the angle scaled to the remaining
        error and re-plans from the measured rotation after each one, so gait
        slip, surface and battery state stop mattering: the loop keeps
        stepping (in either direction) until the integrated yaw is within
        tolerance of the target. Positive degrees is turn_right, negative
        turn_left; the gyro's sign convention is not assumed — it is learned
        from the first cycle.

        Blocks this node's event loop for the whole turn, exactly as a long
        `walk` already does; the battery is re-read between segments here
        rather than waiting on tick telemetry.
        """
        try:
            target = float(degrees)
        except (TypeError, ValueError):
            return f"turn_to needs a numeric degrees value, got {degrees!r}"
        target = max(-360.0, min(360.0, target))
        try:
            tol = float(tolerance) if tolerance is not None else TURN_TOLERANCE_DEG
        except (TypeError, ValueError):
            tol = TURN_TOLERANCE_DEG
        tol = max(2.0, min(45.0, tol))

        if abs(target) <= tol:
            return f"turn_to: target {target:.1f}deg is already within the {tol:.1f}deg tolerance, not moving"

        # Stand first: bias calibration needs a motionless robot, and a turn
        # started from a slumped pose drags feet.
        logger.info(f"turn_to: target {target:+.1f}deg, tolerance {tol:.1f}deg — standing")
        run_action("stand", {}, self.hardware_dict)

        tracker = YawTracker(self.control.imu.sensor)
        bias, spread = tracker.calibrate(1.0)
        still = spread <= 8.0
        note = "" if still else f" (gyro spread {spread:.1f}deg/s during calibration — robot was not still)"
        logger.info(f"turn_to: gyro bias {bias:+.2f}deg/s, spread {spread:.2f}deg/s")

        # A turn command (x=0, y=0) is SINGLE-SHOT in condition_monitor: one
        # run_gait cycle, queue cleared — walk()'s `steps` argument does not
        # multiply it. Measured 2026-08-18: at angle=8 one cycle rotates ~36deg,
        # so going through walk() gives a 36deg quantum that can never settle
        # into a small tolerance (the robot oscillates across the target
        # forever). Instead each step here queues CMD_MOVE directly with an
        # angle scaled to the remaining error: angle 1..8 spans roughly
        # 4.5..36deg per cycle, which is fine enough to land inside tolerance.
        per_unit = TURN_SEED_DEG_PER_ANGLE_UNIT
        sense = 0.0  # +1/-1 once the first step reveals the gyro's sign for a right turn
        max_steps = int(math.ceil(abs(target) / TURN_SEED_DEG_PER_ANGLE_UNIT)) + 12
        steps_done = 0
        weak_steps = 0
        outcome = "reached tolerance"

        def one_cycle(angle: int) -> bool:
            """Queue a single turn gait cycle and wait for it to complete."""
            control = self.control
            control.command_queue = [cmd.CMD_MOVE, "1", "0", "0", str(TURN_SPEED), str(angle)]
            control.timeout = time.time()
            end = time.time() + 15.0
            while time.time() < end:
                queue_now = getattr(control, "command_queue", None)
                if isinstance(queue_now, list) and queue_now and queue_now[0] == "":
                    return True
                time.sleep(0.05)
            return False

        tracker.start()
        try:
            while True:
                progress = tracker.yaw() * sense if sense else 0.0
                remaining = abs(target) - progress
                if abs(remaining) <= tol:
                    break
                if steps_done >= max_steps:
                    outcome = f"stopped at the {max_steps}-step safety cap"
                    break

                self.read_battery(force=True)
                refusal = self.motion_refusal("walk", {})
                if refusal is not None:
                    outcome = f"aborted: {refusal}"
                    break

                # Positive angle is turn_right (walk() uses +8/-8); flip it
                # when correcting an overshoot.
                magnitude = max(1, min(8, round(abs(remaining) / per_unit)))
                rightward = (target > 0) == (remaining > 0)
                angle = magnitude if rightward else -magnitude
                battery = self.last_battery
                logger.info(
                    f"turn_to: step {steps_done + 1}: angle {angle:+d} "
                    f"(remaining {remaining:+.1f}deg, est {per_unit:.1f}deg/unit"
                    + (f", battery {battery[0]:.2f}V" if battery else "")
                    + ")"
                )
                before = tracker.yaw()
                completed = one_cycle(angle)
                time.sleep(0.4)  # let the step settle before trusting the yaw delta
                delta = tracker.yaw() - before
                steps_done += 1

                logger.info(
                    f"turn_to: step {steps_done} rotated {delta:+.1f}deg, "
                    f"integrated yaw {tracker.yaw():+.1f}deg"
                    + ("" if completed else " (gait thread never cleared the command)")
                )
                if not completed:
                    outcome = "aborted: the gait thread did not execute a turn cycle"
                    break
                if abs(delta) < 1.0:
                    weak_steps += 1
                    if weak_steps >= 2:
                        outcome = (
                            f"aborted: two consecutive cycles rotated under 1deg "
                            f"({delta:+.2f}deg last) — the gait is not rotating the body"
                        )
                        break
                    continue
                weak_steps = 0

                if sense == 0.0:
                    # The first productive cycle always runs toward the target
                    # (progress is 0 until sense is known), so whatever yaw
                    # sign it produced is, by definition, the sign of progress.
                    sense = 1.0 if delta > 0 else -1.0
                    # `sense` is relative to the target's direction; the
                    # convention worth remembering is the absolute one — which
                    # way yaw runs for a commanded RIGHT turn. Persist it so
                    # heading-hold walking does not have to rediscover it.
                    canonical = sense * (1.0 if angle > 0 else -1.0)
                    if save_yaw_sign(canonical, "turn_to"):
                        logger.info(
                            f"turn_to: learned gyro yaw sign {canonical:+.0f} "
                            f"(positive yaw = turning right) and saved it"
                        )
                # Blend toward the measured per-angle-unit rate; one noisy
                # cycle should not swing the plan hard.
                measured_unit = abs(delta) / magnitude
                per_unit = max(1.5, min(12.0, 0.6 * per_unit + 0.4 * measured_unit))
        finally:
            final_yaw = tracker.stop()
            run_action("stand", {}, self.hardware_dict)

        achieved = final_yaw * sense if sense else 0.0
        signed_achieved = achieved if target > 0 else -achieved
        battery = self.last_battery
        battery_txt = f", battery {battery[0]:.2f}V/{battery[1]:.2f}V" if battery else ""
        return (
            f"turn_to {outcome}: target {target:+.1f}deg, rotated {signed_achieved:+.1f}deg "
            f"(residual {target - signed_achieved:+.1f}deg) in {steps_done} gait cycle(s), "
            f"measured {per_unit:.1f}deg per angle unit, "
            f"gyro bias {bias:+.2f}deg/s{battery_txt}{note}"
        )

    def walk_straight(self, direction, cycles, speed, heading=None, gain=None) -> str:
        """Walk in a straight line, holding heading closed-loop on the z gyro.

        `walk` displaces all six feet by the same stride each cycle, which is
        straight only if every leg slips equally. They do not, so an open-loop
        walk arcs — by a few degrees per cycle, in a direction that changes
        with surface, calibration and battery state. Here the gyro measures the
        drift while the gait runs and a small steering angle is folded into the
        next cycle to take it back out.

        Why this can steer without interrupting the walk: a CMD_MOVE with a
        non-zero stride is *continuous* in `condition_monitor` — the queue is
        not cleared, so `run_gait` is re-entered cycle after cycle, re-reading
        `command_queue` each time. Re-queueing the same command with a
        different angle therefore steers the very next cycle, with no stop and
        no pose change in between.

        `heading` is the line to hold, in degrees relative to where the robot
        is pointing when the command starts; the default of 0 is "straight
        ahead from here". `gain` overrides the steering gain, and a gain of 0
        measures the drift without correcting it — which is how the test graph
        gets an honest open-loop baseline through the identical code path.
        Blocks this node's event loop for the whole walk, exactly as `walk` and
        `turn_to` already do.
        """
        stride = {
            "forward": (0, WALK_STRIDE_MM),
            "backward": (0, -WALK_STRIDE_MM),
            "left": (-WALK_STRIDE_MM, 0),
            "right": (WALK_STRIDE_MM, 0),
        }.get(str(direction).strip().lower())
        if stride is None:
            return (
                f"walk_straight: unknown direction {direction!r}; use forward, "
                f"backward, left or right (turning is turn_to's job)"
            )
        x, y = stride

        try:
            cycles = max(1, min(20, int(cycles)))
        except (TypeError, ValueError):
            cycles = 3
        try:
            speed = max(2, min(10, int(speed)))
        except (TypeError, ValueError):
            speed = 6
        try:
            target = float(heading) if heading is not None else 0.0
        except (TypeError, ValueError):
            target = 0.0
        target = max(-45.0, min(45.0, target))

        control = self.control
        cycle_s = cycle_duration_estimate(1, speed)
        # The walk ends when the gait has run the cycles asked for, counted off
        # Control.gait_cycles — not when an estimated duration has elapsed. The
        # estimate ignores I2C time, so timing the walk made it stop short.
        # The estimate is still used to size the safety timeout and to scale
        # the controller's integrator.
        started_cycles = control.gait_cycles
        deadline = time.monotonic() + cycle_s * cycles * 3 + 10.0
        # The steering loop samples faster than the gait cycles; tell the
        # controller how much of a cycle each sample covers so its integrator
        # is tuned in cycles, not in whatever interval this node happens to use.
        dt_cycles = STEER_INTERVAL_S / cycle_s

        # Stand first: a walk started from a slumped pose drags feet, and the
        # gyro bias measurement below needs a motionless robot.
        run_action("stand", {}, self.hardware_dict)

        tracker = YawTracker(control.imu.sensor)
        bias, spread = tracker.calibrate(1.0)
        still_note = (
            "" if spread <= 8.0
            else f" (gyro spread {spread:.1f}deg/s during calibration — robot was not still)"
        )

        sign = load_yaw_sign()
        try:
            gain_value = float(gain) if gain is not None else None
        except (TypeError, ValueError):
            gain_value = None
        controller = HeadingHold() if gain_value is None else HeadingHold(gain=gain_value)
        measuring_only = gain_value == 0.0
        logger.info(
            f"walk_straight: {direction} {cycles} cycles at speed {speed}, "
            f"holding {target:+.1f}deg, gyro bias {bias:+.2f}deg/s, "
            f"sign {'unknown — will learn' if sign is None else f'{sign:+.0f}'}"
            + (", gain 0 — MEASURING ONLY, no correction" if measuring_only else "")
        )

        def queue(angle: int) -> None:
            control.command_queue = [
                cmd.CMD_MOVE, "1", str(x), str(y), str(speed), str(angle)
            ]
            control.timeout = time.time()

        applied = 0
        corrections = 0
        worst_error = 0.0
        outcome = "completed"
        probe_started = None
        probe_until = 0.0
        last_heartbeat = time.monotonic()

        tracker.start()
        queue(0)
        last_battery_check = time.time()
        try:
            while True:
                time.sleep(STEER_INTERVAL_S)

                # Stop one cycle early. In continuous mode `run_gait` only
                # re-reads the queue between cycles, so a stop queued now takes
                # effect at the end of the cycle already running — queueing it
                # after the Nth cycle completes would let an N+1th start and
                # overshoot by a full stride. The leading sleep above
                # guarantees condition_monitor has picked the walk up before
                # this can fire, which is what makes cycles=1 work.
                if control.gait_cycles - started_cycles >= cycles - 1:
                    break
                if time.monotonic() > deadline:
                    outcome = (
                        f"stopped on a timeout after "
                        f"{control.gait_cycles - started_cycles}/{cycles} cycles"
                    )
                    break

                # Re-read the pack mid-walk: a long walk is exactly where a
                # marginal pack sags below the floor, and stopping late means
                # stopping by browning out.
                if time.time() - last_battery_check > 2.0:
                    last_battery_check = time.time()
                    self.read_battery(force=True)
                    refusal = self.motion_refusal("walk", {})
                    if refusal is not None:
                        outcome = f"aborted: {refusal}"
                        break

                if sign is None and not measuring_only:
                    # Never measured on this robot: steer gently one way and
                    # watch which way yaw moves. That is the whole calibration,
                    # and it happens once in the robot's life. Skipped when
                    # only measuring, because deliberately curving a baseline
                    # walk would corrupt the very number it exists to produce.
                    if probe_started is None:
                        probe_started = tracker.yaw()
                        probe_until = time.monotonic() + SIGN_PROBE_TIMEOUT_S
                        applied = SIGN_PROBE_ANGLE
                        queue(applied)
                        continue
                    moved = tracker.yaw() - probe_started
                    if abs(moved) < SIGN_PROBE_MIN_DEG:
                        if time.monotonic() < probe_until:
                            continue
                        # The probe steered and the gyro barely moved, so the
                        # sign cannot be trusted. Straighten up and finish the
                        # walk open-loop rather than holding a steer forever on
                        # the strength of a measurement that never arrived.
                        applied = 0
                        queue(applied)
                        measuring_only = True
                        logger.warning(
                            f"walk_straight: sign probe moved only {moved:+.1f}deg in "
                            f"{SIGN_PROBE_TIMEOUT_S:.0f}s — giving up on heading hold, "
                            f"walking OPEN-LOOP. Is the gyro responding?"
                        )
                        continue
                    sign = 1.0 if moved > 0 else -1.0
                    save_yaw_sign(sign, "walk_straight")
                    logger.info(
                        f"walk_straight: learned gyro yaw sign {sign:+.0f} "
                        f"from a {moved:+.1f}deg probe and saved it"
                    )

                if sign is None:
                    # Measuring only, on a robot whose sign is not yet known.
                    # The magnitude of the drift is still exactly right; only
                    # which way it leans is unknown, and the summary says so.
                    continue

                error = tracker.yaw() * sign - target
                worst_error = max(worst_error, abs(error))
                wanted = controller.steer(error, dt_cycles)
                if wanted != applied:
                    applied = wanted
                    corrections += 1
                    queue(applied)
                    logger.info(
                        f"walk_straight: heading {error:+.1f}deg off line, "
                        f"steering {applied:+d}"
                    )
                elif time.monotonic() - last_heartbeat > HEARTBEAT_S:
                    # Log even when nothing changes. Without this a long walk
                    # holding one correction goes silent for tens of seconds,
                    # which is exactly the window in which a runaway is
                    # invisible in the logs afterwards (2026-08-19).
                    last_heartbeat = time.monotonic()
                    logger.info(
                        f"walk_straight: cycle "
                        f"{control.gait_cycles - started_cycles}/{cycles}, heading "
                        f"{error:+.1f}deg off line, holding steer {applied:+d}"
                    )
        finally:
            # A zero-stride CMD_MOVE is the single-shot branch: it returns the
            # feet to the resting footprint and clears the queue, so the gait
            # actually stops rather than running on after this method returns.
            control.command_queue = [cmd.CMD_MOVE, "1", "0", "0", str(speed), "0"]
            control.timeout = time.time()
            end = time.time() + 15.0
            while time.time() < end:
                queue_now = getattr(control, "command_queue", None)
                if isinstance(queue_now, list) and queue_now and queue_now[0] == "":
                    break
                time.sleep(0.05)
            final_yaw = tracker.stop()

        if sign is not None:
            final_error = final_yaw * sign - target
            unknown = ""
        else:
            # Sign unresolved: the drift magnitude is measured correctly, we
            # just cannot say which way it leans. Report the magnitude rather
            # than a zero that would read as a perfect walk.
            final_error = abs(final_yaw - target)
            unknown = " (gyro sign unknown — magnitude only, direction not known)"
        worst_error = max(worst_error, abs(final_error))

        battery = self.last_battery
        battery_txt = (
            f", battery {battery[0]:.2f}V/{battery[1]:.2f}V" if battery else ""
        )
        mode = "measured uncorrected" if measuring_only else f"{corrections} steering correction(s)"
        ran = control.gait_cycles - started_cycles
        gyro_health = tracker.health()
        measured = getattr(control, "last_cycle_s", 0.0)
        timing = (
            f", {measured:.2f}s per cycle measured vs {cycle_s:.2f}s estimated"
            if measured else ""
        )
        return (
            f"walk_straight {outcome}: {direction} {ran}/{cycles} cycles at speed {speed}, "
            f"final heading error {final_error:+.1f}deg (worst {worst_error:.1f}deg), "
            f"{mode}{unknown}{timing}{battery_txt}{still_note}. {gyro_health}"
        )

    def motion_refusal(self, tool_name: str, args: dict) -> Optional[str]:
        """Return a refusal string if this motion command must not run."""
        gated = tool_name in MOTION_TOOLS
        # Relaxing servos *reduces* current draw and is always allowed;
        # re-enabling torque is a motion command.
        if tool_name == "relax" and not bool(args.get("enabled", True)):
            gated = True

        if not gated:
            return None
        if self.blocked_reason:
            return f"Refused: {self.blocked_reason}"
        if self.control is None:
            return "Refused: hexapod control unavailable"

        voltage = self.read_battery()
        if voltage is None:
            return "Refused: battery voltage unknown, cannot verify it is safe to move"
        if min(voltage) < FLOOR_V:
            return (
                f"Refused: battery {voltage[0]:.2f}V/{voltage[1]:.2f}V is below "
                f"the {FLOOR_V:.1f}V floor. Charge the pack."
            )
        if not self.gait_thread_alive():
            return "Refused: gait thread is not running, motion commands would silently do nothing"
        return None


def main() -> None:
    node = Node()
    hw = Hardware()
    gait_was_alive = hw.gait_thread_alive()

    try:
        for event in node:
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue

            if event["id"] == "tick":
                voltage = hw.read_battery(force=True)
                if voltage is not None:
                    node.send_output(
                        "battery",
                        encode({"load_v": voltage[0], "pi_v": voltage[1], "floor_v": FLOOR_V}),
                    )

                # Surface a dead gait thread instead of letting motion commands
                # keep returning "success" while nothing moves.
                alive = hw.gait_thread_alive()
                if alive != gait_was_alive:
                    gait_was_alive = alive
                    if not alive:
                        logger.error("gait thread has died — motion is no longer being executed")
                    node.send_output(
                        "health",
                        encode({"gait_thread_alive": alive, "control": hw.control is not None}),
                    )

            elif event["id"] == "tool_call":
                call = decode(event)
                if not call or not owns(NODE, call.get("name", "")):
                    continue

                name = call["name"]
                args = call.get("args") or {}

                # set_stance is served here rather than by src/actions.py,
                # since it manipulates Control.body_points directly.
                if name == "set_stance":
                    refusal = hw.motion_refusal("stand", args)
                    if refusal is not None:
                        applied, text = False, refusal
                    else:
                        applied, text = hw.apply_stance(args.get("stance", ""))
                    node.send_output(
                        "tool_result",
                        encode(
                            {
                                "id": call.get("id"),
                                "name": name,
                                "text": text,
                                "refused": not applied,
                            }
                        ),
                    )
                    continue

                # turn_to is served here rather than by src/actions.py: it
                # needs the IMU and re-plans between gait segments, both of
                # which live on this side of the process boundary.
                if name == "turn_to":
                    refusal = hw.motion_refusal(name, args)
                    text = (
                        refusal
                        if refusal is not None
                        else hw.turn_to(args.get("degrees", 0), args.get("tolerance"))
                    )
                    node.send_output(
                        "tool_result",
                        encode(
                            {
                                "id": call.get("id"),
                                "name": name,
                                "text": text,
                                "refused": refusal is not None,
                            }
                        ),
                    )
                    continue

                # walk_straight, like turn_to, is served here rather than by
                # src/actions.py: it needs the IMU and re-steers between gait
                # cycles, both of which live on this side of the boundary.
                if name == "walk_straight":
                    refusal = hw.motion_refusal(name, args)
                    text = (
                        refusal
                        if refusal is not None
                        else hw.walk_straight(
                            args.get("direction", "forward"),
                            args.get("cycles", 3),
                            args.get("speed", 6),
                            args.get("heading"),
                            args.get("gain"),
                        )
                    )
                    node.send_output(
                        "tool_result",
                        encode(
                            {
                                "id": call.get("id"),
                                "name": name,
                                "text": text,
                                "refused": refusal is not None or "aborted" in text,
                            }
                        ),
                    )
                    continue

                refusal = hw.motion_refusal(name, args)
                if refusal is not None:
                    logger.warning(f"{name}: {refusal}")
                    node.send_output(
                        "tool_result",
                        encode({"id": call.get("id"), "name": name, "text": refusal, "refused": True}),
                    )
                    continue

                text = run_action(name, args, hw.hardware_dict)
                node.send_output(
                    "tool_result",
                    encode(
                        {
                            "id": call.get("id"),
                            "name": name,
                            "text": text if text is not None else f"{name} not available",
                        }
                    ),
                )
    finally:
        # Leave the robot safe: torque off, then release the bus.
        if hw.control is not None:
            try:
                run_action("relax", {"enabled": True}, hw.hardware_dict)
            except Exception:
                pass
        if hw.adc is not None:
            try:
                hw.adc.close_i2c()
            except Exception:
                pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
