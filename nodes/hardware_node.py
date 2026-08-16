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

from dora import Node  # noqa: E402
from src.actions import execute as run_action  # noqa: E402
from src.adc import ADC  # noqa: E402
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


class Hardware:
    def __init__(self) -> None:
        self.adc: Optional[ADC] = None
        self.control: Optional[Control] = None
        self.last_battery: Optional[Tuple[float, float]] = None
        self.last_battery_at = 0.0
        self.blocked_reason: Optional[str] = None

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
