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
import threading
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

# MPU6050 z-gyro, read raw: one I2C word per sample instead of the seven
# transactions get_gyro_data() spends, because the sampler shares the bus with
# ~1800 servo writes/s while the gait runs.
GYRO_Z_REG = 0x47
GYRO_LSB_PER_DPS = 131.0  # the 250deg/s range src/imu.py configures


class YawTracker:
    """Integrates the z gyro into a yaw angle while the robot turns.

    Yaw from gyro integration drifts, but a turn lasts tens of seconds and the
    bias is measured immediately beforehand with the robot standing still, so
    the drift over one turn is well under the stopping tolerance. The AHRS in
    src/imu.py would do no better here: with no magnetometer its yaw is the
    same integration, just harder to reason about.
    """

    def __init__(self, sensor) -> None:
        self.sensor = sensor
        self.bias_dps = 0.0
        self._yaw_deg = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _read_dps(self) -> float:
        return self.sensor.read_i2c_word(GYRO_Z_REG) / GYRO_LSB_PER_DPS

    def calibrate(self, seconds: float = 1.0) -> Tuple[float, float]:
        """Measure gyro bias at rest. Returns (bias deg/s, sample spread deg/s)."""
        samples = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            samples.append(self._read_dps())
            time.sleep(0.005)
        self.bias_dps = sum(samples) / len(samples)
        return self.bias_dps, max(samples) - min(samples)

    def start(self) -> None:
        self._yaw_deg = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._integrate, daemon=True)
        self._thread.start()

    def _integrate(self) -> None:
        last = time.monotonic()
        while self._running:
            try:
                dps = self._read_dps() - self.bias_dps
            except Exception:
                # One bad bus transaction mid-gait is survivable; a dead
                # sampler thread would silently freeze the yaw estimate, so
                # keep the loop alive and let the next sample land.
                time.sleep(0.01)
                last = time.monotonic()
                continue
            now = time.monotonic()
            with self._lock:
                self._yaw_deg += dps * (now - last)
            last = now
            time.sleep(0.005)

    def yaw(self) -> float:
        with self._lock:
            return self._yaw_deg

    def stop(self) -> float:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return self.yaw()


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
