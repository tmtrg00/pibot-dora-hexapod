"""Yaw sensing and heading-hold steering for the hexapod.

Three things live here, all built on the MPU6050's z gyro:

1. `YawTracker` — integrates the z gyro into a relative yaw angle in a
   background thread. Lifted out of `hardware_node.py` unchanged in substance,
   because closed-loop *turning* is no longer its only consumer: heading-hold
   walking needs the same instrument.

2. The gyro sign convention, learned once and remembered. Nothing in the
   drivers tells us which way the chip is mounted, so "does yaw go positive
   when the robot turns right?" is an empirical fact about this robot. Both
   `turn_to` and `walk_straight` discover it as a side effect of moving; this
   module persists it to `data/gyro_sense.json` so it is discovered once for
   the life of the robot rather than once per command.

3. `HeadingHold` — the PI steering controller that turns a heading error into
   a gait steering angle.

Why heading hold matters: `run_gait` walks by displacing every foot by the same
stride, which is straight only if all six legs slip equally. They do not —
calibration error, surface, and a sagging battery all bias it — so an open-loop
walk arcs. The gait engine already accepts a steering `angle` alongside the
stride, so the correction costs nothing structurally: measure the drift, fold a
small angle into the next cycle, and the robot walks the line it was told to.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional, Tuple

# MPU6050 z-gyro, read raw: one I2C word per sample instead of the seven
# transactions get_gyro_data() spends, because the sampler shares the bus with
# ~1800 servo writes/s while the gait runs.
GYRO_Z_REG = 0x47
GYRO_LSB_PER_DPS = 131.0  # the 250deg/s range src/imu.py configures

# Where the learned sign is remembered. Relative to the project root, which
# common.bootstrap() has already chdir'd into by the time any node calls this.
SIGN_PATH = os.path.join("data", "gyro_sense.json")

# Env override, for a bench where the IMU has been remounted and the stored
# value is stale. "+1" or "-1".
SIGN_ENV = "PIBOT_GYRO_YAW_SIGN"


class YawTracker:
    """Integrates the z gyro into a yaw angle while the robot moves.

    Yaw from gyro integration drifts, but a turn or a walk lasts tens of
    seconds and the bias is measured immediately beforehand with the robot
    standing still, so the drift over one command is well under the tolerances
    that use it. The AHRS in src/imu.py would do no better here: with no
    magnetometer its yaw is the same integration, just harder to reason about.
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


# --------------------------------------------------------------------------
# The gyro sign convention
# --------------------------------------------------------------------------
# `+1` means: when the robot is commanded to turn RIGHT (a positive `angle` in
# CMD_MOVE), the integrated z yaw goes POSITIVE. Everything that converts a
# raw yaw reading into "degrees turned to the right" multiplies by this.


def load_yaw_sign() -> Optional[float]:
    """The remembered sign, or None if this robot has never been measured."""
    override = os.environ.get(SIGN_ENV, "").strip()
    if override:
        try:
            value = float(override)
        except ValueError:
            value = 0.0
        if value:
            return 1.0 if value > 0 else -1.0

    try:
        with open(SIGN_PATH, "r", encoding="utf-8") as fh:
            value = float(json.load(fh).get("yaw_sign", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if value not in (1.0, -1.0):
        return None
    return value


def save_yaw_sign(sign: float, source: str) -> bool:
    """Remember the sign. Returns True if it was written.

    Failure to persist is not worth failing a movement command over — the
    caller has already learned the sign for this run — so this reports rather
    than raises.
    """
    if sign not in (1.0, -1.0):
        return False
    try:
        os.makedirs(os.path.dirname(SIGN_PATH), exist_ok=True)
        with open(SIGN_PATH, "w", encoding="utf-8") as fh:
            json.dump(
                {"yaw_sign": sign, "learned_from": source, "learned_at": time.time()},
                fh,
            )
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Heading hold
# --------------------------------------------------------------------------

# The gait's steering `angle` runs 1..8 in each direction. A correction bigger
# than this stops being a correction and becomes a turn, which would fight the
# stride rather than trim it.
MAX_STEER_UNITS = 3

# Below this the error is inside the gyro's own noise over a walk, and
# correcting it just makes the robot weave.
DEADBAND_DEG = 1.5

# Angle units per degree of heading error. One angle unit rotates the body
# roughly 4.5deg over a full cycle (measured 2026-08-18), so 0.12 asks for
# roughly half the error to be taken out per cycle. This value and the ratio
# below were chosen by sweeping both against a simulated plant across every
# plausible drift (0.5-5deg/cycle) and steering response (2-7deg/unit): they
# hold the worst settled error to 5.5deg over that whole range, where pure
# proportional control (ratio 0) let it reach 21-38deg.
DEFAULT_GAIN = float(os.environ.get("PIBOT_HEADING_GAIN", "0.12"))

# Integral gain, as a fraction of the proportional gain. Tied to it rather than
# set independently so that one knob (`gain`) still scales the whole
# controller, and so a gain of 0 disables correction completely.
INTEGRAL_RATIO = float(os.environ.get("PIBOT_HEADING_I_RATIO", "0.40"))


class HeadingHold:
    """PI steering: heading error in, gait steering angle out.

    The I term is not optional here, which is worth explaining because the
    plant looks like it should not need one. Steering angle sets yaw *rate* and
    heading error is the integral of that rate, so the loop is already
    first-order and P alone is stable. But the disturbance — the gait's
    inherent bias, from calibration error and uneven slip — enters at the same
    place as the control, as a constant deg-per-cycle rate. Against a
    rate disturbance, P alone settles at a *non-zero* error of
    `drift / (gain x response)` and sits there: the controller needs standing
    error to produce the standing steer that cancels the bias. Simulated at a
    5deg/cycle drift, that residual was 20deg — better than open loop, but the
    robot would still visibly walk a diagonal. The integrator supplies that
    standing steer on its own, so the error can return to zero.

    The output is coarse: the gait's smallest steering unit rotates the body
    ~4.5deg per cycle, which is larger than the per-cycle drift being
    corrected. So the settled behaviour is not a constant trim but a dither —
    a correction applied every second or third cycle — and a residual limit
    cycle of a few degrees is expected, not a fault.
    """

    def __init__(self, gain: float = DEFAULT_GAIN, deadband: float = DEADBAND_DEG,
                 max_units: int = MAX_STEER_UNITS,
                 integral_ratio: float = INTEGRAL_RATIO) -> None:
        self.gain = gain
        self.integral_gain = gain * integral_ratio
        self.deadband = deadband
        self.max_units = max_units
        self.integral = 0.0

    def reset(self) -> None:
        self.integral = 0.0

    def steer(self, error_deg: float, dt_cycles: float = 1.0) -> int:
        """Steering angle for a heading error, in gait angle units.

        `error_deg` is positive when the robot has drifted to the RIGHT of the
        line it should be on; the returned angle is negative (steer left) to
        bring it back.

        `dt_cycles` is how many gait cycles have elapsed since the last call.
        The integrator is scaled by it so the controller behaves the same
        whether it is sampled once per cycle or three times per cycle — the
        caller samples faster than the gait runs, to keep correction latency
        below one cycle, and without this the I term would wind up several
        times too fast and the tuning above would not mean anything.
        """
        if self.gain == 0.0:
            # "Measure, do not correct" — used to take an honest open-loop
            # baseline through this exact code path.
            return 0

        # Accumulate outside the deadband only. Inside it the robot is on the
        # line, and integrating gyro noise there would slowly manufacture a
        # correction for a problem that does not exist.
        if abs(error_deg) >= self.deadband:
            self.integral += error_deg * dt_cycles
        if self.integral_gain > 0.0:
            # Anti-windup: the integrator may never ask for more than the
            # actuator can deliver, or it spends the end of a walk unwinding a
            # demand that was never applied.
            limit = self.max_units / self.integral_gain
            self.integral = max(-limit, min(limit, self.integral))

        units = -(self.gain * error_deg + self.integral_gain * self.integral)
        limited = max(-self.max_units, min(self.max_units, units))
        rounded = round(limited)
        if rounded == 0 and abs(error_deg) >= self.deadband and abs(limited) > 0.25:
            # The gait's smallest steer is one unit. Rather than drop a
            # correction the loop has decided it wants, round away from zero —
            # applied for one cycle out of several, this is how a coarse
            # actuator delivers a fine average correction.
            rounded = 1 if limited > 0 else -1
        return int(rounded)
