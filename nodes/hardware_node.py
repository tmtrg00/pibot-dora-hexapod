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

# Closed-loop turning: degrees the body rotates per angle unit per gait cycle.
# Only a seed — the turn measures the real figure as it goes and converges on
# it. 4.5 came from a single 2026-08-18 cycle at angle=8; two turn_to runs on
# 2026-08-19 measured 3.2 and 3.3, so the seed was consistently over-predicting
# each cycle by about a third and every turn undershot its target.
TURN_SEED_DEG_PER_ANGLE_UNIT = 3.3
TURN_TOLERANCE_DEG = 5.0
TURN_SPEED = int(os.environ.get("PIBOT_TURN_SPEED", "6"))

# How often a turn samples yaw. Shorter than the heading-hold interval on
# purpose. A turn measures its own degrees-per-angle-unit by watching how far
# the body moved over one gait cycle, and it can only notice a cycle boundary
# at its next sample — so every measurement is late by up to one interval and
# includes that much of the NEXT cycle's rotation. That is a systematic
# over-estimate of the cycle's effect, not noise: at 0.35s against a 2.46s
# cycle it inflated the estimate by 14%, and an inflated estimate makes the
# turn believe it has further to go than it has, so it stops short.
TURN_POLL_S = 0.08

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

# Return to the neutral stance after this many seconds with nothing moving.
# A stance is adopted for a movement; when the movement is over the robot
# should stand normally again rather than stay crouched or splayed. 0 disables.
IDLE_STANCE_RESET_S = float(os.environ.get("PIBOT_IDLE_STANCE_RESET_S", "20"))

# How many intermediate poses a stance change is split into, and how long to
# settle between them. One step is the old behaviour: the body drops or the
# feet snap outward as fast as the servos can slew. Four is enough to read as a
# movement rather than a jolt without making a stance change feel slow.
STANCE_RAMP_STEPS = max(1, min(12, int(os.environ.get("PIBOT_STANCE_RAMP_STEPS", "4"))))
STANCE_RAMP_PAUSE_S = max(
    0.0, min(1.0, float(os.environ.get("PIBOT_STANCE_RAMP_PAUSE_S", "0.12")))
)


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


# Closed-loop approach. The HC-SR04 is noisy and occasionally returns a wild
# value, so readings are median-filtered over this many samples before the stop
# decision reads them; and a reading older than the staleness bound means the
# distance sensor has stopped answering, which must stop the robot rather than
# let it walk on blind.
APPROACH_MEDIAN_SAMPLES = 3
APPROACH_STALE_S = 2.0
APPROACH_MIN_CM = 2.0
APPROACH_MAX_CM = 400.0


class Approach:
    """State for one closed-loop approach, driven by the node's event loop.

    Why this is a state machine rather than a blocking loop like `turn_to`:
    the gait runs on `Control.condition_monitor`, a thread of its own, so once
    a walk command is queued the robot keeps walking without this node's
    attention. Blocking here to drive it would therefore be worse than
    pointless — it would stop the node receiving the very `distance` messages
    the approach is closing the loop on, since those arrive from another
    process as dora events. Handing control back to the event loop between
    readings is what lets the robot walk continuously *and* watch where it is
    going.
    """

    __slots__ = (
        "call_id", "stop_cm", "speed", "direction", "max_cycles", "started_cycles",
        "started_at", "samples", "last_at", "last_cm", "closing_rate", "tracker",
        "controller", "yaw_sign", "applied", "corrections", "worst_cm", "lead_cm",
        "walking", "last_steer_at", "last_battery_at",
        "travel_cm_per_cycle", "mark_cm", "mark_cycles",
    )

    def __init__(self, call_id, stop_cm, speed, direction, max_cycles,
                 started_cycles, tracker, controller, yaw_sign):
        self.call_id = call_id
        self.stop_cm = stop_cm
        self.speed = speed
        self.direction = direction
        self.max_cycles = max_cycles
        self.started_cycles = started_cycles
        self.started_at = time.monotonic()
        self.samples = []
        self.last_at = 0.0
        self.last_cm = None
        self.closing_rate = 0.0
        self.tracker = tracker
        self.controller = controller
        self.yaw_sign = yaw_sign
        self.applied = 0
        self.corrections = 0
        self.worst_cm = None
        self.lead_cm = 0.0
        # Nothing moves until a distance reading has arrived. Walking first and
        # looking afterwards would mean the robot advances blind for at least
        # one gait cycle, which is exactly the cycle it cannot take back.
        self.walking = False
        self.last_steer_at = time.monotonic()
        self.last_battery_at = 0.0
        # How far the robot actually travels per gait cycle, measured. Seeded
        # from the stride, then learned — the same approach the turn takes to
        # degrees-per-angle-unit, and for the same reason: the nominal figure
        # is geometry, and what the robot does on this floor is not.
        self.travel_cm_per_cycle = WALK_STRIDE_MM / 10.0
        self.mark_cm = None
        self.mark_cycles = 0

    def satisfied(self, predicted_cm: float) -> bool:
        """Has the target been reached, in whichever direction is being asked?

        Approaching, the distance falls and the goal is to get below the
        target. Retreating, it rises and the goal is to get above it. The same
        comparison cannot serve both — using the approach test for a retreat
        made every retreat stop instantly, since the robot starts closer than
        the gap it was asked to open.
        """
        if self.direction == "forward":
            return predicted_cm <= self.stop_cm
        return predicted_cm >= self.stop_cm

    def add_reading(self, cm: float) -> Optional[float]:
        """Median-filter a reading. Returns the filtered distance, or None."""
        if not (APPROACH_MIN_CM <= cm <= APPROACH_MAX_CM):
            return None
        now = time.monotonic()
        self.samples.append(cm)
        if len(self.samples) > APPROACH_MEDIAN_SAMPLES:
            self.samples.pop(0)
        filtered = sorted(self.samples)[len(self.samples) // 2]

        if self.last_cm is not None and now > self.last_at:
            # Positive when closing on the obstacle.
            rate = (self.last_cm - filtered) / (now - self.last_at)
            self.closing_rate = 0.6 * self.closing_rate + 0.4 * rate
        self.last_cm, self.last_at = filtered, now
        if self.worst_cm is None or filtered < self.worst_cm:
            self.worst_cm = filtered
        return filtered


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
        # Which named stance the robot is currently standing in, and when it
        # last did anything. Together these drive the idle stance reset: a
        # stance is a pose for a purpose, and once the purpose is over the
        # robot should not be left crouched or splayed indefinitely.
        self.applied_stance = "neutral"
        # The height and tilt currently held, so a stance change can be ramped
        # *from* where the robot actually is rather than from an assumed zero.
        self.applied_z = 0
        self.applied_roll = 0
        self.applied_pitch = 0
        self.last_motion_at = time.time()
        self.relaxed = False
        # The closed-loop approach in flight, if any. Held as state rather than
        # run as a blocking loop so the node keeps receiving distance messages
        # while the robot walks — see the Approach docstring.
        self.approach: Optional[Approach] = None

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

        # Ramp into the stance instead of jumping to it.
        #
        # A stance change is a single `set_leg_angles()` away, and taken in one
        # step that is exactly what it looks like: the body drops or the feet
        # snap outward as fast as eighteen servos can slew, which lurches the
        # robot and can skid the planted feet. Nothing about the pose requires
        # that — the intermediate poses between two valid stances are
        # themselves valid — so the transition is split into several smaller
        # moves. The robot arrives in the same place, having got there as a
        # movement rather than a jolt.
        #
        # Each intermediate footprint and height is reach-checked before it is
        # applied, because interpolating between two reachable poses does not
        # by itself guarantee the path between them stays inside the 90..248mm
        # window, and `set_leg_angles()` fails silently when it does not.
        start_footprint = [[p[0], p[1]] for p in previous]
        start_z, start_roll, start_pitch = self.applied_z, self.applied_roll, self.applied_pitch
        completed = True
        for step in range(1, STANCE_RAMP_STEPS + 1):
            t = step / STANCE_RAMP_STEPS
            partial = [
                [sx + (tx - sx) * t, sy + (ty - sy) * t]
                for (sx, sy), (tx, ty) in zip(start_footprint, footprint)
            ]
            z = int(round(start_z + (stance.z - start_z) * t))
            roll = int(round(start_roll + (stance.roll - start_roll) * t))
            pitch = int(round(start_pitch + (stance.pitch - start_pitch) * t))

            reaches = stances.leg_reach(partial, z)
            low = stances.MIN_REACH_MM + stances.REACH_MARGIN_MM
            high = stances.MAX_REACH_MM - stances.REACH_MARGIN_MM
            if min(reaches) < low or max(reaches) > high:
                for i, (x, y) in enumerate(previous):
                    control.body_points[i][0] = x
                    control.body_points[i][1] = y
                return False, (
                    f"Stance {stance.name!r} refused: step {step}/{STANCE_RAMP_STEPS} of "
                    f"the transition would need a leg reach of "
                    f"{min(reaches):.1f}..{max(reaches):.1f}mm, outside the usable "
                    f"{low:.0f}..{high:.0f}mm. Footprint reverted."
                )

            # Widen or narrow the resting footprint. run_gait deep-copies
            # body_points, so this changes the walking gait too.
            for i, (x, y) in enumerate(partial):
                control.body_points[i][0] = x
                control.body_points[i][1] = y

            completed = queue([cmd.CMD_POSITION, "0", "0", str(z)]) and completed
            if roll or pitch or start_roll or start_pitch:
                completed = queue(
                    [cmd.CMD_ATTITUDE, str(roll), str(pitch), "0"]
                ) and completed
            if step < STANCE_RAMP_STEPS and STANCE_RAMP_PAUSE_S:
                time.sleep(STANCE_RAMP_PAUSE_S)

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
        self.applied_stance = stance.name
        self.applied_z = stance.z
        self.applied_roll = stance.roll
        self.applied_pitch = stance.pitch
        actual = max(
            math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p in control.leg_positions
        )
        note = "" if completed else " (timed out waiting for the gait thread)"
        return True, (
            f"Stance set to {stance.name!r}: {stance.description}. "
            f"spread={stance.spread:.2f} z={stance.z} roll={stance.roll} pitch={stance.pitch}, "
            f"max leg reach {actual:.0f}mm (predicted {max(reaches):.0f}mm), "
            f"ramped in {STANCE_RAMP_STEPS} step(s){note}"
        )

    def turn_to(self, degrees, tolerance=None) -> str:
        """Rotate in place by `degrees`, closed-loop on the z gyro.

        The turn runs as ONE continuous gait command whose steering angle is
        re-trimmed as the robot rotates, rather than as a sequence of separate
        single-cycle commands. That is what makes it smooth: the earlier
        version queued one cycle, waited for the gait thread to clear the
        queue, paused 0.4s to let the body settle, measured, re-planned, and
        only then queued the next one — so the robot turned in visible discrete
        lurches (owner observation, 2026-08-19). It could not simply be made
        continuous at the time, because `condition_monitor` treated any command
        without a stride as single-shot; that has since been narrowed to the
        genuine stop-and-stand command, so a turn now stays queued and
        `run_gait` re-enters it cycle after cycle.

        Closed-loop behaviour is unchanged in substance: the commanded angle is
        scaled to the rotation still to go, so gait slip, surface and battery
        state stop mattering, and an overshoot is corrected by the angle simply
        changing sign. Positive degrees is right, negative is left.

        Landing accuracy comes from that scaling rather than from stopping at
        exactly the right instant, which is not possible: `run_gait` only reads
        the queue between cycles, so a stop always takes effect at a cycle
        boundary. Because the angle shrinks as the target nears, the final
        cycle is a small one — angle 1 is about 3deg — and the unavoidable
        overshoot is smaller than the tolerance.

        Blocks this node's event loop for the whole turn, exactly as a long
        `walk` already does; the battery is re-read as it goes rather than
        waiting on tick telemetry.
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
            return (
                f"turn_to: target {target:.1f}deg is already within the "
                f"{tol:.1f}deg tolerance, not moving"
            )

        control = self.control
        cycle_s = cycle_duration_estimate(1, TURN_SPEED)

        # Stand first: bias calibration needs a motionless robot, and a turn
        # started from a slumped pose drags feet.
        logger.info(f"turn_to: target {target:+.1f}deg, tolerance {tol:.1f}deg — standing")
        run_action("stand", {}, self.hardware_dict)

        tracker = YawTracker(control.imu.sensor)
        bias, spread = tracker.calibrate(1.0)
        note = (
            "" if spread <= 8.0
            else f" (gyro spread {spread:.1f}deg/s during calibration — robot was not still)"
        )

        yaw_sign = load_yaw_sign()
        per_unit = TURN_SEED_DEG_PER_ANGLE_UNIT
        logger.info(
            f"turn_to: gyro bias {bias:+.2f}deg/s, spread {spread:.2f}deg/s, "
            f"sign {'unknown — will learn' if yaw_sign is None else f'{yaw_sign:+.0f}'}"
        )

        def queue(angle: int) -> None:
            control.command_queue = [
                cmd.CMD_MOVE, "1", "0", "0", str(TURN_SPEED), str(angle)
            ]
            control.timeout = time.time()

        def plan(remaining: float) -> int:
            """Steering angle for the rotation still to go, signed.

            Truncates rather than rounds, so a cycle is more likely to fall
            just short of the remaining rotation than to overshoot it. That
            matters because overshooting has to be undone by a cycle in the
            opposite direction, and alternating between the two is precisely
            the hunting this loop must not do.
            """
            magnitude = max(1, min(8, int(abs(remaining) / max(per_unit, 0.5))))
            return magnitude if remaining > 0 else -magnitude

        # Planning happens at CYCLE BOUNDARIES and predicts one cycle ahead,
        # which is the only way to land accurately on a gait that cannot be
        # interrupted. `run_gait` reads the queue once, when a cycle begins, so
        # by the time this loop notices a cycle has ended the next one is
        # already turning with whatever angle was queued before. Planning from
        # the heading right now would therefore always be one cycle late — that
        # cost a 15.6deg overshoot on a 90deg target in simulation, because the
        # angle was still at maximum when the target was 10deg away.
        #
        # So each boundary decides the angle for the cycle AFTER the one just
        # started, using the heading that in-flight cycle is predicted to end
        # at. The invariant that makes this work: `applied` is always the angle
        # of the currently running cycle, because every change is queued a full
        # cycle before it takes effect.
        deadline = time.monotonic() + abs(target) / max(per_unit, 1.0) * cycle_s * 4 + 30.0
        # Plan the opening cycle for only half the target. Until a cycle has
        # been measured, `per_unit` is a seed from another surface on another
        # day, and the first cycle is committed before any measurement exists
        # to correct it — a seed 65% low would overshoot a small turn outright.
        # Halving bounds that, and costs nothing on a large turn, where half
        # the target still saturates the maximum steering angle.
        applied = plan(target / 2.0)
        angle_running = applied
        outcome = "reached tolerance"
        turned_right = 0.0
        boundary_turned = 0.0
        boundary_at = 0.0
        cycles_seen = control.gait_cycles
        last_battery_check = time.time()
        last_heartbeat = time.monotonic()
        stalled_cycles = 0
        units_commanded = 0

        tracker.start()
        queue(applied)
        try:
            while True:
                time.sleep(TURN_POLL_S)
                now = time.monotonic()
                raw_yaw = tracker.yaw()

                if yaw_sign is None:
                    # Never measured on this robot. The command is already
                    # turning toward the target, so whichever way yaw moves is
                    # by definition the direction the commanded sign produces.
                    if abs(raw_yaw) < SIGN_PROBE_MIN_DEG:
                        if now > deadline:
                            outcome = "aborted: the gyro never moved, cannot turn closed-loop"
                            break
                        continue
                    yaw_sign = (1.0 if raw_yaw > 0 else -1.0) * (1.0 if applied > 0 else -1.0)
                    save_yaw_sign(yaw_sign, "turn_to")
                    logger.info(
                        f"turn_to: learned gyro yaw sign {yaw_sign:+.0f} "
                        f"(positive yaw = turning right) and saved it"
                    )

                turned_right = raw_yaw * yaw_sign
                remaining = target - turned_right

                # Where the cycle now running will leave us. It cannot be
                # interrupted, so this — not the heading right now — is what
                # decides whether to stop. `angle_running` rather than
                # `applied`: a new angle is queued a cycle before it takes
                # effect, so the two differ for most of every cycle.
                delivered = turned_right - boundary_turned
                outstanding = angle_running * per_unit - delivered
                still_coming = (
                    max(0.0, outstanding) if angle_running > 0 else min(0.0, outstanding)
                )
                # Only count the cycle in flight once it has definitely begun.
                # `gait_cycles` is incremented at the END of a cycle, a moment
                # BEFORE condition_monitor re-reads the queue for the next one,
                # so immediately after a boundary there is a window in which the
                # next cycle has not picked up its angle. Stopping inside that
                # window replaces the queued angle with the stop and the cycle
                # never runs at all — which cost a 90deg turn its last 10deg
                # (2026-08-19). Assume nothing more is coming until the window
                # has passed; one sample later the prediction is trustworthy.
                if now - boundary_at < TURN_POLL_S:
                    still_coming = 0.0
                predicted_end = turned_right + still_coming
                if abs(target - predicted_end) <= tol:
                    # Inside tolerance: stop. Do not try to do better.
                    #
                    # An earlier version continued whenever one more cycle
                    # promised a closer landing. On the robot that never
                    # terminated: the smallest correction the gait can make is
                    # about one angle unit, ~2.7deg, which is the same size as
                    # the residual being chased, so each cycle overshot and the
                    # next was planned to come back. Observed 2026-08-19 — a
                    # 90deg turn arrived at 89.0deg and then alternated -1, +1,
                    # -1 around the target, which the owner described as the
                    # robot dancing, until the stall guard aborted it.
                    #
                    # Tolerance is the contract. Landing 4deg out and stopping
                    # is invisible; hunting for 1deg is not.
                    logger.info(
                        f"turn_to: stopping — the cycle in flight lands "
                        f"{target - predicted_end:+.1f}deg from target, inside the "
                        f"{tol:.0f}deg tolerance"
                    )
                    break

                if now > deadline:
                    outcome = f"stopped on a timeout with {remaining:+.1f}deg still to go"
                    break

                if time.time() - last_battery_check > 2.0:
                    last_battery_check = time.time()
                    self.read_battery(force=True)
                    refusal = self.motion_refusal("walk", {})
                    if refusal is not None:
                        outcome = f"aborted: {refusal}"
                        break

                cycles_now = control.gait_cycles
                if cycles_now == cycles_seen:
                    if now - last_heartbeat > HEARTBEAT_S:
                        last_heartbeat = now
                        logger.info(
                            f"turn_to: {remaining:+.1f}deg to go, turning at {applied:+d}"
                        )
                    continue

                # --- a cycle just ended, the next has just begun -------------
                cycles_seen = cycles_now
                moved = turned_right - boundary_turned
                boundary_turned = turned_right

                # Learn the real rotation per angle unit from the cycle that
                # just finished, so the plan converges on this robot, this
                # surface and this battery rather than on a seeded constant.
                # Estimate the rotation per angle unit over the WHOLE turn so
                # far, not from the cycle that just ended. A per-cycle figure
                # is noisy: this loop only notices a boundary at its next
                # sample, up to a sampling interval late, so each cycle's
                # measured rotation carries that jitter — enough to read 3.6
                # on a robot really doing 3.3, which is plenty to misplace the
                # stop. Dividing total rotation by total angle units commanded
                # averages the jitter out and converges within a cycle or two.
                # It is also signed throughout, so a correction that reverses
                # direction subtracts correctly instead of inflating the total.
                units_commanded += angle_running
                if abs(units_commanded) >= 1:
                    estimate = turned_right / units_commanded
                    if 1.0 <= estimate <= 12.0:
                        per_unit = estimate

                # A stall is a cycle that rotated far less than the angle it
                # was given should have produced — not merely a small rotation.
                # A deliberate 1-unit trim near the target moves the body about
                # 3deg, and judging that on an absolute threshold reported the
                # gait as broken when it was working correctly (2026-08-19).
                expected = abs(angle_running) * per_unit
                if expected > 0.5 and abs(moved) < 0.3 * expected:
                    stalled_cycles += 1
                    if stalled_cycles >= 2:
                        outcome = (
                            f"aborted: two consecutive cycles rotated far less than "
                            f"commanded ({moved:+.2f}deg where {expected:.1f}deg was "
                            f"expected) — the gait is not turning the body"
                        )
                        break
                else:
                    stalled_cycles = 0

                # The cycle that just started is committed to whatever was
                # queued, which is `applied`. Record that as the running angle
                # before changing it, so the prediction above stays honest for
                # the rest of this cycle.
                angle_running = applied
                boundary_at = now
                short_by = target - (turned_right + applied * per_unit)
                if abs(short_by) <= tol:
                    # The cycle that just started already lands inside
                    # tolerance. Leave the queue alone and let the stop check
                    # above end the turn — planning another cycle here is what
                    # produced the reversal that read as the robot dancing.
                    continue
                wanted = plan(short_by)
                if wanted != applied:
                    applied = wanted
                    queue(applied)
                    logger.info(
                        f"turn_to: {remaining:+.1f}deg to go, next cycle at {applied:+d} "
                        f"({per_unit:.1f}deg per unit measured)"
                    )
        finally:
            # Zero stride and zero angle is the single-shot stop: it puts the
            # feet back on the resting footprint and clears the queue, so the
            # turn actually ends rather than running on after this returns.
            control.command_queue = [cmd.CMD_MOVE, "1", "0", "0", str(TURN_SPEED), "0"]
            control.timeout = time.time()
            end = time.time() + 15.0
            while time.time() < end:
                queue_now = getattr(control, "command_queue", None)
                if isinstance(queue_now, list) and queue_now and queue_now[0] == "":
                    break
                time.sleep(0.05)
            final_yaw = tracker.stop()
            run_action("stand", {}, self.hardware_dict)

        achieved = final_yaw * yaw_sign if yaw_sign else 0.0
        battery = self.last_battery
        battery_txt = f", battery {battery[0]:.2f}V/{battery[1]:.2f}V" if battery else ""
        return (
            f"turn_to {outcome}: target {target:+.1f}deg, rotated {achieved:+.1f}deg "
            f"(residual {target - achieved:+.1f}deg) in one continuous turn, "
            f"measured {per_unit:.1f}deg per angle unit per cycle{battery_txt}{note}. "
            f"{tracker.health()}"
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

    # ----------------------------------------------------------------------
    # Closed-loop approach
    # ----------------------------------------------------------------------

    def start_approach(self, call_id, args) -> Optional[str]:
        """Begin walking toward an obstacle. Returns a refusal, or None if started."""
        if self.approach is not None:
            return "Refused: an approach is already running"

        direction = str(args.get("direction", "forward")).strip().lower()
        if direction not in ("forward", "backward"):
            return f"approach: direction must be forward or backward, got {direction!r}"
        try:
            stop_cm = float(args.get("stop_cm", 20))
        except (TypeError, ValueError):
            stop_cm = 20.0
        stop_cm = max(5.0, min(200.0, stop_cm))
        try:
            speed = max(2, min(10, int(args.get("speed", 5))))
        except (TypeError, ValueError):
            speed = 5
        try:
            max_cycles = max(1, min(40, int(args.get("max_cycles", 25))))
        except (TypeError, ValueError):
            max_cycles = 25

        control = self.control
        run_action("stand", {}, self.hardware_dict)

        tracker = YawTracker(control.imu.sensor)
        tracker.calibrate(1.0)
        tracker.start()

        self.approach = Approach(
            call_id=call_id,
            stop_cm=stop_cm,
            speed=speed,
            direction=direction,
            max_cycles=max_cycles,
            started_cycles=control.gait_cycles,
            tracker=tracker,
            controller=HeadingHold(),
            yaw_sign=load_yaw_sign(),
        )
        self.last_motion_at = time.time()
        logger.info(
            f"approach: will walk {direction} until {stop_cm:.0f}cm away, "
            f"speed {speed}, cap {max_cycles} cycles — waiting for a distance "
            f"reading before moving"
        )
        return None

    def _start_walking(self, state) -> None:
        x, y = (0, WALK_STRIDE_MM) if state.direction == "forward" else (0, -WALK_STRIDE_MM)
        self.control.command_queue = [
            cmd.CMD_MOVE, "1", str(x), str(y), str(state.speed), "0"
        ]
        self.control.timeout = time.time()
        state.walking = True
        state.started_cycles = self.control.gait_cycles
        self.last_motion_at = time.time()

    def approach_distance(self, cm: float) -> Optional[str]:
        """Feed a distance reading in. Returns a result string once finished."""
        state = self.approach
        if state is None:
            return None
        filtered = state.add_reading(cm)
        if filtered is None:
            return None

        if not state.walking:
            # First reading. If the robot is already where it was asked to be,
            # say so and move nothing at all.
            if state.satisfied(filtered):
                return self.finish_approach(
                    f"needed no movement: already {filtered:.1f}cm away, which "
                    f"satisfies a {state.direction} target of {state.stop_cm:.0f}cm"
                )
            self._start_walking(state)
            logger.info(f"approach: {filtered:.1f}cm away, walking {state.direction}")
            return None

        cycles = self.control.gait_cycles - state.started_cycles
        if cycles >= state.max_cycles:
            return self.finish_approach(
                f"stopped at the {state.max_cycles}-cycle safety cap without reaching "
                f"{state.stop_cm:.0f}cm (last reading {filtered:.1f}cm)"
            )

        # The gait cannot be interrupted mid-cycle: a stop queued now takes
        # effect at the next cycle boundary, and the robot keeps moving until
        # then. So decide on where it will BE at that boundary, not where it is.
        #
        # The lead is one cycle of travel, MEASURED. Deriving it from a
        # sample-to-sample closing rate did not work on the robot: the HC-SR04
        # is noisy enough that a rate taken over 200ms is dominated by that
        # noise, and the resulting lead ran to 7.7cm where a cycle covers about
        # 3.5cm — so the first hardware run stopped 6.9cm short of a 25cm
        # target, and 7.1cm short backing off to 50cm (2026-08-19). Distance
        # covered divided by cycles run is the same quantity with the noise
        # averaged out over seconds instead of milliseconds.
        if state.mark_cm is None:
            state.mark_cm, state.mark_cycles = filtered, cycles
        elif cycles - state.mark_cycles >= 2:
            travelled = abs(filtered - state.mark_cm)
            per_cycle = travelled / (cycles - state.mark_cycles)
            if 0.5 <= per_cycle <= 15.0:
                state.travel_cm_per_cycle = per_cycle
            state.mark_cm, state.mark_cycles = filtered, cycles

        state.lead_cm = state.travel_cm_per_cycle
        predicted = (
            filtered - state.lead_cm if state.direction == "forward"
            else filtered + state.lead_cm
        )
        if state.satisfied(predicted):
            return self.finish_approach(
                f"reached {filtered:.1f}cm from the obstacle "
                f"(target {state.stop_cm:.0f}cm, stopped {state.lead_cm:.1f}cm early "
                f"for the cycle in flight, measured {state.travel_cm_per_cycle:.1f}cm "
                f"per cycle)"
            )
        return None

    def approach_tick(self) -> Optional[str]:
        """Safety and steering, once per node tick. Returns a result when done."""
        state = self.approach
        if state is None:
            return None
        now = time.monotonic()
        control = self.control

        if state.last_cm is None:
            if now - state.started_at > APPROACH_STALE_S * 2:
                return self.finish_approach(
                    "ABORTED: the distance sensor never reported, so nothing moved "
                    "— is the ultrasonic node in this graph?"
                )
            return None
        if now - state.last_at > APPROACH_STALE_S:
            return self.finish_approach(
                f"ABORTED: no distance reading for {now - state.last_at:.1f}s — "
                f"stopping rather than walking on blind"
            )
        if not state.walking:
            return None

        cycles = control.gait_cycles - state.started_cycles
        if cycles >= state.max_cycles:
            return self.finish_approach(
                f"stopped at the {state.max_cycles}-cycle safety cap without reaching "
                f"{state.stop_cm:.0f}cm (last reading {state.last_cm})"
            )

        # A forced battery read costs about a second on the ADS7830, and this
        # runs on every node tick — four times a second in the approach graph.
        # Reading it every time would spend most of the approach blocked in the
        # ADC rather than watching the obstacle.
        if now - state.last_battery_at > 2.0:
            state.last_battery_at = now
            self.read_battery(force=True)
            refusal = self.motion_refusal("walk", {})
            if refusal is not None:
                return self.finish_approach(f"ABORTED: {refusal}")

        # Hold heading while approaching, so "walk up to the wall" goes
        # straight at it rather than arcing off to one side.
        if state.yaw_sign is not None:
            error = state.tracker.yaw() * state.yaw_sign
            # Measure the interval rather than assuming one: this is driven by
            # the node's tick, whose rate differs between graphs, and the
            # controller's integrator is tuned in gait cycles.
            elapsed = max(1e-3, now - state.last_steer_at)
            state.last_steer_at = now
            wanted = state.controller.steer(
                error, elapsed / max(0.2, cycle_duration_estimate(1, state.speed))
            )
            if wanted != state.applied:
                state.applied = wanted
                state.corrections += 1
                x, y = (0, WALK_STRIDE_MM) if state.direction == "forward" else (0, -WALK_STRIDE_MM)
                control.command_queue = [
                    cmd.CMD_MOVE, "1", str(x), str(y), str(state.speed), str(wanted)
                ]
                control.timeout = time.time()
        self.last_motion_at = time.time()
        return None

    def finish_approach(self, reason: str) -> str:
        """Stop the gait and produce the tool result."""
        state = self.approach
        self.approach = None
        control = self.control
        control.command_queue = [cmd.CMD_MOVE, "1", "0", "0", str(state.speed), "0"]
        control.timeout = time.time()
        end = time.time() + 15.0
        while time.time() < end:
            queue_now = getattr(control, "command_queue", None)
            if isinstance(queue_now, list) and queue_now and queue_now[0] == "":
                break
            time.sleep(0.05)
        final_yaw = state.tracker.stop()
        self.last_motion_at = time.time()

        cycles = control.gait_cycles - state.started_cycles if state.walking else 0
        drift = final_yaw * state.yaw_sign if state.yaw_sign else 0.0
        battery = self.last_battery
        battery_txt = f", battery {battery[0]:.2f}V/{battery[1]:.2f}V" if battery else ""
        return (
            f"approach {reason}. Walked {state.direction} for {cycles} cycles, "
            f"closest {state.worst_cm if state.worst_cm is not None else '?'}cm, "
            f"heading drift {drift:+.1f}deg over {state.corrections} correction(s)"
            f"{battery_txt}. {state.tracker.health()}"
        )

    def idle_stance_reset(self) -> Optional[str]:
        """Return to the neutral stance once the robot has stopped and settled.

        A stance is a pose adopted for a purpose — `brace` to be stable,
        `crouch` to drop the centre of gravity, `tall` to clear something. The
        purpose ends when the movement does, but the pose used to persist
        indefinitely, so the robot sat splayed or hunkered until the next
        command happened to change it. That is not a neutral state to leave
        hardware in: `wide` and `brace` hold the legs near the outer end of
        their reach, where the servos work hardest to support the body, and it
        also means the *next* command starts from a stance nobody chose.

        So: once nothing has moved for `PIBOT_IDLE_STANCE_RESET_S`, stand back
        up in `neutral`. Deliberately time-based rather than fired at the end
        of each command, because a stance is usually set precisely so that the
        movements that follow happen in it — resetting between them would
        defeat the point. Set the variable to 0 to disable.

        Returns a description if it acted, None otherwise.
        """
        if IDLE_STANCE_RESET_S <= 0:
            return None
        if self.control is None or self.blocked_reason:
            return None
        # Never re-energise servos that were deliberately relaxed. Torque off
        # is a state someone asked for, and quietly undoing it would both
        # surprise them and draw current they were trying to save.
        if self.relaxed:
            return None
        if self.applied_stance == "neutral":
            return None
        if time.time() - self.last_motion_at < IDLE_STANCE_RESET_S:
            return None
        # Same battery gate as any other movement; this drives servos.
        refusal = self.motion_refusal("set_stance", {})
        if refusal is not None:
            return None

        previous = self.applied_stance
        ok, text = self.apply_stance("neutral")
        self.last_motion_at = time.time()
        if not ok:
            logger.warning(f"idle stance reset from {previous!r} failed: {text}")
            return None
        return f"idle for {IDLE_STANCE_RESET_S:.0f}s, reset stance {previous!r} -> 'neutral'"

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

            if event["id"] == "distance":
                payload = decode(event) or {}
                try:
                    cm = float(payload.get("cm"))
                except (TypeError, ValueError):
                    continue
                # Capture the id first: finishing clears the state.
                call_id = hw.approach.call_id if hw.approach is not None else None
                done = hw.approach_distance(cm)
                if done is not None:
                    logger.info(done)
                    node.send_output(
                        "tool_result",
                        encode({"id": call_id, "name": "approach",
                                "text": done, "refused": "ABORTED" in done}),
                    )
                continue

            if event["id"] == "tick":
                if hw.approach is not None:
                    call_id = hw.approach.call_id
                    done = hw.approach_tick()
                    if done is not None:
                        logger.info(done)
                        node.send_output(
                            "tool_result",
                            encode({"id": call_id, "name": "approach",
                                    "text": done, "refused": "ABORTED" in done}),
                        )
                voltage = hw.read_battery(force=True)
                if voltage is not None:
                    node.send_output(
                        "battery",
                        encode({"load_v": voltage[0], "pi_v": voltage[1], "floor_v": FLOOR_V}),
                    )

                # Surface a dead gait thread instead of letting motion commands
                # keep returning "success" while nothing moves.
                reset = hw.idle_stance_reset()
                if reset is not None:
                    logger.info(reset)
                    node.send_output("health", encode({"stance_reset": hw.applied_stance}))

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

                # Anything that drives servos counts as activity for the idle
                # stance reset, and `relax` decides whether resetting is
                # allowed to re-energise them at all. `relax` counts as
                # activity too even though it is not in MOTION_TOOLS: without
                # that, re-enabling torque after a long relaxed spell would
                # look like the robot had been idle the whole time and trigger
                # a reset in the same instant.
                if name in MOTION_TOOLS or name == "relax":
                    hw.last_motion_at = time.time()
                if name == "relax":
                    hw.relaxed = bool(args.get("enabled", True))

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

                # approach is served here and, unlike every other tool, does
                # not answer immediately: it starts a closed loop and the
                # result is sent when the robot stops. The call id is kept so
                # the answer can be matched to the request later.
                if name == "approach":
                    refusal = hw.motion_refusal(name, args)
                    if refusal is None:
                        refusal = hw.start_approach(call.get("id"), args)
                    if refusal is not None:
                        node.send_output(
                            "tool_result",
                            encode({"id": call.get("id"), "name": name,
                                    "text": refusal, "refused": True}),
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
        # Leave the robot safe: stop the gait, then torque off, then release
        # the bus. Stopping first matters because an approach is a state
        # machine — the gait keeps running on its own thread whether or not
        # this loop is still alive, so exiting mid-approach without clearing
        # the command would leave a walking robot behind until the servos
        # actually lose power.
        if hw.control is not None and hw.approach is not None:
            try:
                logger.warning("stopping mid-approach")
                logger.info(hw.finish_approach("ABORTED: the node is shutting down"))
            except Exception:
                pass
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
