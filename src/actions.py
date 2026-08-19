#!/usr/bin/env python3
"""
Hexapod action registry.

TOOLS: OpenAI tool schemas exposed to the LLM.
execute(): dispatch one tool call against available hardware.
"""

import threading
import time
from typing import Any, Dict, Optional

from src.command import COMMAND as cmd
from src.Thread import stop_thread


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "walk",
            "description": "Walk or rotate the hexapod for a finite number of gait cycles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "left", "right", "turn_left", "turn_right"],
                        "description": "Movement direction.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Approximate gait cycles to run (1-10).",
                    },
                    "speed": {
                        "type": "integer",
                        "description": "Gait speed (2-10). Higher is faster.",
                    },
                    "gait": {
                        "type": "integer",
                        "enum": [1, 2],
                        "description": "1=tripod gait, 2=wave gait.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_position",
            "description": "Set body translation offsets in millimeters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Left/right offset (-40..40)."},
                    "y": {"type": "integer", "description": "Forward/back offset (-40..40)."},
                    "z": {"type": "integer", "description": "Height offset (-20..20)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_attitude",
            "description": "Set body roll/pitch/yaw attitude in degrees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "roll": {"type": "integer", "description": "Roll (-15..15)."},
                    "pitch": {"type": "integer", "description": "Pitch (-15..15)."},
                    "yaw": {"type": "integer", "description": "Yaw (-15..15)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_balance",
            "description": "Enable or disable IMU self-balancing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "description": "True to enable, false to disable."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stand",
            "description": "Return to neutral standing pose.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "relax",
            "description": "Disable or re-enable servo holding torque.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True relaxes/powers down torque, false re-enables torque.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dance",
            "description": "Run a short playful movement sequence.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_photo",
            "description": "Capture an image from the camera.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Optional output path. Default: data/voice_photo.jpg",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_led",
            "description": "Set LED color or pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["off", "solid", "chase", "blink", "rainbow", "rainbow_cycle"],
                        "description": "LED mode.",
                    },
                    "r": {"type": "integer", "description": "Red channel (0-255)."},
                    "g": {"type": "integer", "description": "Green channel (0-255)."},
                    "b": {"type": "integer", "description": "Blue channel (0-255)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buzz",
            "description": "Play the buzzer for a short duration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "number",
                        "description": "Buzz duration in seconds (0.05 to 5).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_distance",
            "description": "Measure ultrasonic obstacle distance in centimeters.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_battery",
            "description": "Read both battery voltage channels.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_head",
            "description": "Move camera pan/tilt servos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pan": {"type": "integer", "description": "Left/right angle (-90..90)."},
                    "tilt": {"type": "integer", "description": "Up/down angle (-90..90)."},
                    "auto_relax": {
                        "type": "boolean",
                        "description": "Release head servo torque shortly after moving (default: true).",
                    },
                    "hold_s": {
                        "type": "number",
                        "description": "How long to hold before auto-relax, in seconds (default: 0.4).",
                    },
                },
            },
        },
    },
]


ACTION_EMOTIONS = {
    "walk": "happy",
    "set_position": "thinking",
    "set_attitude": "thinking",
    "toggle_balance": "curious",
    "stand": "neutral",
    "relax": "neutral",
    "dance": "happy",
    "take_photo": "curious",
    "set_led": "curious",
    "buzz": "surprised",
    "get_distance": "curious",
    "get_battery": "thinking",
    "move_head": "curious",
}

HEAD_SERVO_CHANNELS = (0, 1)
HEAD_AUTO_RELAX_DEFAULT_S = 0.4


def _clamp(value: Any, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _queue_command(control: Any, parts: list[str]) -> None:
    control.command_queue = parts
    control.timeout = time.time()


def _wait_for_clear(control: Any, timeout_s: float = 15.0, interval_s: float = 0.05) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        queue = getattr(control, "command_queue", None)
        if isinstance(queue, list) and queue and queue[0] == "":
            return True
        time.sleep(interval_s)
    return False


def _map_value(value: int, from_low: int, from_high: int, to_low: int, to_high: int) -> float:
    return (to_high - to_low) * (value - from_low) / float(from_high - from_low) + to_low


def _estimated_cycle_duration(gait: int, speed: int) -> float:
    """Rough seconds per gait cycle, from the frame count and the frame delay.

    An underestimate by design: it counts run_gait's 10ms sleep per frame but
    not the 18 servo writes each frame also spends on the I2C bus. Use it to
    size a timeout, not to decide when a walk is finished — `_wait_for_cycles`
    does that by counting real cycles.
    """
    if gait == 1:
        f_value = round(_map_value(speed, 2, 10, 126, 22))
    else:
        f_value = round(_map_value(speed, 2, 10, 171, 45))
    return max(0.2, (f_value * 0.01) + 0.05)


def _cycles_run(control: Any) -> int:
    """Completed gait cycles, or 0 against a Control that predates the counter."""
    return getattr(control, "gait_cycles", 0)


def _wait_for_cycles(control: Any, cycles: int, timeout_s: float,
                     fallback_s: float = 0.0, interval_s: float = 0.02) -> int:
    """Block until `cycles` more gait cycles have run. Returns how many did.

    Counting beats timing here. Sleeping for an estimated duration made the
    distance travelled depend on how well the estimate matched the hardware,
    and it never did: the estimate ignores I2C time, so a walk consistently
    stopped short of the cycles it was asked for. `Control.gait_cycles` counts
    completed cycles directly, so "walk 5 cycles" now means five.

    `timeout_s` is a safety bound, not the expected duration, so a Control that
    predates the counter sleeps `fallback_s` — the old estimate — rather than
    the timeout, which would be far too long.
    """
    start = getattr(control, "gait_cycles", None)
    if start is None:
        time.sleep(fallback_s if fallback_s else timeout_s)
        return cycles

    end = time.time() + timeout_s
    while time.time() < end:
        done = control.gait_cycles - start
        if done >= cycles:
            return done
        time.sleep(interval_s)
    return control.gait_cycles - start


def _relax_servo_channel(servo: Any, channel: int) -> None:
    """Disable PWM on one channel without affecting other servos."""
    if channel < 16:
        servo.pwm_41.set_pwm(channel, 4096, 4096)
    else:
        servo.pwm_40.set_pwm(channel - 16, 4096, 4096)


def _release_head_after_delay(servo: Any, delay_s: float, token: int, hardware: Dict[str, Any]) -> None:
    time.sleep(delay_s)
    if hardware.get("_head_release_token") != token:
        return
    for channel in HEAD_SERVO_CHANNELS:
        _relax_servo_channel(servo, channel)


def _schedule_head_release(servo: Any, hardware: Dict[str, Any], delay_s: float) -> float:
    delay_s = max(0.0, min(5.0, float(delay_s)))
    token = time.time_ns()
    hardware["_head_release_token"] = token
    thread = threading.Thread(
        target=_release_head_after_delay,
        args=(servo, delay_s, token, hardware),
        daemon=True,
    )
    thread.start()
    hardware["_head_release_thread"] = thread
    return delay_s


def _ensure_led_mode(led: Any, mode_code: str, r: int, g: int, b: int, hardware: Dict[str, Any]) -> str:
    led_thread = hardware.get("_led_thread")
    if led_thread is not None and led_thread.is_alive():
        try:
            stop_thread(led_thread)
        except Exception:
            pass
        hardware["_led_thread"] = None

    if mode_code == "0":
        led.process_light_command([cmd.CMD_LED_MOD, "0"])
        return "LEDs off"

    if mode_code == "1":
        led.process_light_command([cmd.CMD_LED_MOD, "1"])
        led.process_light_command([cmd.CMD_LED, str(r), str(g), str(b)])
        return f"LED solid color set to ({r}, {g}, {b})"

    if mode_code in {"2", "3", "4", "5"}:
        if mode_code in {"2", "3"}:
            led.process_light_command([cmd.CMD_LED, str(r), str(g), str(b)])
        thread = threading.Thread(
            target=led.process_light_command,
            args=([cmd.CMD_LED_MOD, mode_code],),
            daemon=True,
        )
        thread.start()
        hardware["_led_thread"] = thread
        mode_name = {
            "2": "chase",
            "3": "blink",
            "4": "rainbow",
            "5": "rainbow_cycle",
        }[mode_code]
        return f"LED mode set to {mode_name}"

    return "Invalid LED mode"


def execute(name: str, args: Dict[str, Any], hardware: Dict[str, Any]) -> Optional[str]:
    """Dispatch a tool call and return a short textual result.

    Returns None when required hardware is missing or action is unknown.
    """
    control = hardware.get("control")
    led = hardware.get("led")
    ultrasonic = hardware.get("ultrasonic")
    buzzer = hardware.get("buzzer")
    adc = hardware.get("adc")
    servo = hardware.get("servo")
    camera = hardware.get("camera")

    try:
        if name == "walk":
            if control is None:
                return None
            direction = str(args.get("direction", "forward")).strip().lower()
            steps = _clamp(args.get("steps", 2), 1, 10)
            speed = _clamp(args.get("speed", 6), 2, 10)
            gait = _clamp(args.get("gait", 1), 1, 2)

            if direction == "forward":
                x, y, angle = 0, 35, 0
            elif direction == "backward":
                x, y, angle = 0, -35, 0
            elif direction == "left":
                x, y, angle = -35, 0, 0
            elif direction == "right":
                x, y, angle = 35, 0, 0
            elif direction == "turn_left":
                x, y, angle = 0, 0, -8
            elif direction == "turn_right":
                x, y, angle = 0, 0, 8
            else:
                return "Invalid walk direction"

            move = [cmd.CMD_MOVE, str(gait), str(x), str(y), str(speed), str(angle)]
            # Generous: a cycle can run well over its estimate on a tired pack.
            budget = max(3.0, _estimated_cycle_duration(gait, speed) * 3 + 2.0)
            before = _cycles_run(control)

            if x == 0 and y == 0:
                # A turn is single-shot in condition_monitor: it runs one cycle
                # and then clears the queue, so each cycle has to be re-queued.
                # Waiting on the queue clearing rather than on the cycle counter
                # matters here — the counter is incremented *before* the queue is
                # cleared, so re-queueing the moment it ticks would hand
                # condition_monitor a fresh command that it then wipes.
                queued = 0
                while queued < steps:
                    at_queue_time = _cycles_run(control)
                    _queue_command(control, move)
                    if not _wait_for_clear(control, timeout_s=budget):
                        break
                    if _cycles_run(control) == at_queue_time:
                        break  # the queue cleared without a cycle actually running
                    queued += 1
            else:
                # A stride keeps the queue, so run_gait re-enters itself and
                # all this has to do is count cycles off until it has enough.
                # It waits for one *fewer* than asked: run_gait only re-reads
                # the queue between cycles, so the stop has to be queued while
                # the final cycle is still running. Queueing it after the Nth
                # completed would let an N+1th start, overshooting by a stride.
                _queue_command(control, move)
                # Let condition_monitor pick the command up before a
                # steps=1 walk can stop it again.
                time.sleep(0.15)
                _wait_for_cycles(
                    control,
                    max(0, steps - 1),
                    budget * steps,
                    fallback_s=_estimated_cycle_duration(gait, speed) * steps,
                )

            _queue_command(control, [cmd.CMD_MOVE, str(gait), "0", "0", str(speed), "0"])
            _wait_for_clear(control)
            # Count after the stop has landed, so this reports what the robot
            # actually did rather than what was waited for.
            done = _cycles_run(control) - before
            measured = getattr(control, "last_cycle_s", 0.0)
            shortfall = "" if done >= steps else f", short of the {steps} asked for"
            return (
                f"Walked {direction} for {done} cycles"
                + (f" at {measured:.2f}s per cycle" if measured else "")
                + shortfall
            )

        if name == "set_position":
            if control is None:
                return None
            x = _clamp(args.get("x", 0), -40, 40)
            y = _clamp(args.get("y", 0), -40, 40)
            z = _clamp(args.get("z", 0), -20, 20)
            _queue_command(control, [cmd.CMD_POSITION, str(x), str(y), str(z)])
            ok = _wait_for_clear(control)
            return f"Body position set to x={x}, y={y}, z={z}" + ("" if ok else " (timeout waiting for completion)")

        if name == "set_attitude":
            if control is None:
                return None
            roll = _clamp(args.get("roll", 0), -15, 15)
            pitch = _clamp(args.get("pitch", 0), -15, 15)
            yaw = _clamp(args.get("yaw", 0), -15, 15)
            _queue_command(control, [cmd.CMD_ATTITUDE, str(roll), str(pitch), str(yaw)])
            ok = _wait_for_clear(control)
            return f"Attitude set to roll={roll}, pitch={pitch}, yaw={yaw}" + ("" if ok else " (timeout waiting for completion)")

        if name == "toggle_balance":
            if control is None:
                return None
            enabled = bool(args.get("enabled", True))
            if enabled:
                _queue_command(control, [cmd.CMD_BALANCE, "1"])
                return "Balance mode enabled"
            control.command_queue = ["", "", "", "", "", ""]
            control.timeout = time.time()
            return "Balance mode disabled"

        if name == "stand":
            if control is None:
                return None
            _queue_command(control, [cmd.CMD_POSITION, "0", "0", "0"])
            _wait_for_clear(control)
            _queue_command(control, [cmd.CMD_ATTITUDE, "0", "0", "0"])
            _wait_for_clear(control)
            return "Standing neutral"

        if name == "relax":
            if control is None:
                return None
            enabled = bool(args.get("enabled", True))
            if enabled:
                control.relax(True)
                try:
                    control.servo_power_disable.on()
                except Exception:
                    pass
                return "Servos relaxed"
            try:
                control.servo_power_disable.off()
            except Exception:
                pass
            control.relax(False)
            return "Servos re-enabled"

        if name == "dance":
            if control is None:
                return None
            for roll in (10, -10, 10, -10, 0):
                _queue_command(control, [cmd.CMD_ATTITUDE, str(roll), "0", "0"])
                _wait_for_clear(control, timeout_s=5.0)
            _queue_command(control, [cmd.CMD_POSITION, "0", "0", "0"])
            _wait_for_clear(control, timeout_s=5.0)
            return "Dance sequence complete"

        if name == "take_photo":
            if camera is None:
                return None
            filepath = str(args.get("filepath", "data/voice_photo.jpg"))
            image = camera.capture(filepath)
            if image:
                return f"Photo captured: {image}"
            return "Photo capture failed"

        if name == "set_led":
            if led is None:
                return None
            mode = str(args.get("mode", "solid")).strip().lower()
            mode_code = {
                "off": "0",
                "solid": "1",
                "chase": "2",
                "blink": "3",
                "rainbow": "4",
                "rainbow_cycle": "5",
            }.get(mode, "1")
            r = _clamp(args.get("r", 0), 0, 255)
            g = _clamp(args.get("g", 0), 0, 255)
            b = _clamp(args.get("b", 0), 0, 255)
            return _ensure_led_mode(led, mode_code, r, g, b, hardware)

        if name == "buzz":
            if buzzer is None:
                return None
            duration = float(args.get("duration", 0.2))
            duration = max(0.05, min(5.0, duration))
            buzzer.set_state(True)
            time.sleep(duration)
            buzzer.set_state(False)
            return f"Buzzed for {duration:.2f}s"

        if name == "get_distance":
            if ultrasonic is None:
                return None
            distance = ultrasonic.get_distance()
            if distance is None:
                return "Distance unavailable"
            return f"Distance: {distance} cm"

        if name == "get_battery":
            if adc is None:
                return None
            battery_load, battery_pi = adc.read_battery_voltage()
            return f"Battery: load={battery_load}V, pi={battery_pi}V"

        if name == "move_head":
            if servo is None:
                return None
            pan = _clamp(args.get("pan", 0), -90, 90)
            tilt = _clamp(args.get("tilt", 0), -90, 90)
            x = _clamp(90 + pan, 50, 180)
            y = _clamp(90 + tilt, 0, 180)
            servo.set_servo_angle(0, x)
            servo.set_servo_angle(1, y)
            auto_relax = bool(args.get("auto_relax", True))
            if auto_relax:
                hold_s_raw = args.get("hold_s", HEAD_AUTO_RELAX_DEFAULT_S)
                try:
                    hold_s = float(hold_s_raw)
                except (TypeError, ValueError):
                    hold_s = HEAD_AUTO_RELAX_DEFAULT_S
                hold_s = _schedule_head_release(servo, hardware, hold_s)
                return f"Head moved to pan={pan}, tilt={tilt} (auto-relax in {hold_s:.2f}s)"
            return f"Head moved to pan={pan}, tilt={tilt}"

        return None

    except Exception as exc:
        return f"{name} failed: {exc}"
