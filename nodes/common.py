"""Shared plumbing for every PiBot-Hexapod dora node.

Three jobs:

1. `bootstrap()` — chdir into this project's root and put it on `sys.path`, so
   every relative path baked into the driver code (`config/config.yaml`,
   `point.txt`, `data/*`, `.env`) resolves, and `src.control`, `src.actions`
   etc. import.

   This project is self-contained: `src/`, `config/`, `point.txt` and
   `params.json` live here and it runs with `/opt/pibot-hexapod` deleted. The
   cost of that independence is that the drivers and the servo calibration are
   now a *copy* — fixes on either side do not reach the other. See
   docs/RUNBOOKS.md for how to re-sync them deliberately.

2. Message encoding. Dora carries Arrow arrays; we standardise on a single
   JSON string element. Chatty, low-rate control messages are far easier to
   debug this way than a bespoke binary schema, and none of them are hot.
   Camera frames are the one thing that would justify zero-copy Arrow buffers,
   and today they still travel as file paths (see docs/DESIGN.md).

3. Tool routing. The upstream `src/actions.py` `execute()` dispatches every
   tool against one shared `hardware` dict. Split across processes, no single
   node holds all the devices, so `TOOL_OWNER` says which node owns which
   tool. Each device node subscribes to the same broadcast `tool_call` stream
   and simply ignores calls it does not own.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import pyarrow as pa

# This project's own root: /opt/pibot-dora, the parent of nodes/. Derived from
# this file's location rather than hardcoded, so the project can be moved or
# cloned elsewhere without editing anything. PIBOT_HOME still overrides, which
# is what lets a node run against a different checkout.
PROJECT_ROOT = os.environ.get(
    "PIBOT_HOME",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

# Retained under the old name so existing scripts and docs keep working.
PIBOT_HOME = PROJECT_ROOT

_BOOTSTRAPPED = False


def bootstrap() -> str:
    """Make this project importable and its relative paths valid.

    Idempotent. Returns the project root.
    """
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED:
        if not os.path.isdir(os.path.join(PROJECT_ROOT, "src")):
            raise RuntimeError(
                f"{PROJECT_ROOT!r} has no src/ directory, so the robot drivers "
                f"cannot be imported. If PIBOT_HOME is set, check it points at a "
                f"full checkout."
            )
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        os.chdir(PROJECT_ROOT)
        _BOOTSTRAPPED = True
    return PROJECT_ROOT


def get_logger(name: str) -> logging.Logger:
    """Per-node logger. Dora captures stdout/stderr into its own log files."""
    logging.basicConfig(
        level=os.environ.get("PIBOT_LOG_LEVEL", "INFO").upper(),
        format=f"%(asctime)s [%(levelname)s] {name}: %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)


def load_config() -> Dict[str, Any]:
    """Load the upstream config/config.yaml (requires bootstrap() first)."""
    import yaml

    with open(os.path.join(PIBOT_HOME, "config", "config.yaml"), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# --------------------------------------------------------------------------
# Message encoding
# --------------------------------------------------------------------------

def encode(payload: Any) -> pa.Array:
    """Wrap a JSON-serialisable payload as a single-element Arrow string array."""
    return pa.array([json.dumps(payload)])


def decode(event: Dict[str, Any]) -> Any:
    """Unwrap a dora INPUT event produced by `encode`.

    Tolerates an empty array (returns None) so a node never dies on a stray
    or malformed message from a peer.
    """
    value = event.get("value")
    if value is None or len(value) == 0:
        return None
    return json.loads(value[0].as_py())


# --------------------------------------------------------------------------
# Tool routing
# --------------------------------------------------------------------------

# Which node owns which LLM tool. Mirrors the device split in dataflow.yml.
# `take_photo` is deliberately absent: the brain handles it as a capture +
# vision round-trip rather than a plain tool dispatch, exactly as the upstream
# `execute_tool_calls` special-cases it.
TOOL_OWNER: Dict[str, str] = {
    "walk": "hardware",
    "set_position": "hardware",
    "set_attitude": "hardware",
    "toggle_balance": "hardware",
    "stand": "hardware",
    "relax": "hardware",
    "dance": "hardware",
    "move_head": "hardware",
    "get_battery": "hardware",
    "set_stance": "hardware",
    "turn_to": "hardware",
    "walk_straight": "hardware",
    "set_led": "led",
    "buzz": "buzzer",
    "get_distance": "ultrasonic",
    "take_photo": "camera",
}

# Tools that put current through the servos. The hardware node refuses these
# below the battery floor — see the pre-flight rule in the project AGENTS.md.
MOTION_TOOLS = {
    "walk",
    "set_position",
    "set_attitude",
    "toggle_balance",
    "stand",
    "dance",
    "move_head",
    "set_stance",
    "turn_to",
    "walk_straight",
}


def stance_tool_schema() -> Dict[str, Any]:
    """OpenAI tool schema for set_stance, appended to the upstream TOOLS list.

    Defined here rather than in src/actions.py because the stance work lives in
    this project and the upstream tool list is left untouched.
    """
    import stances

    names = sorted(stances.STANCES)
    described = "; ".join(f"{n}: {stances.STANCES[n].description}" for n in names)
    return {
        "type": "function",
        "function": {
            "name": "set_stance",
            "description": (
                "Adopt a named body stance, changing ride height, foot spread and "
                f"tilt together. Available stances -- {described}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stance": {
                        "type": "string",
                        "enum": names,
                        "description": "Which stance to adopt.",
                    }
                },
                "required": ["stance"],
            },
        },
    }

def turn_tool_schema() -> Dict[str, Any]:
    """OpenAI tool schema for turn_to, appended to the upstream TOOLS list.

    Like set_stance, this lives here because closed-loop turning is this
    project's work and the upstream tool list stays untouched. `walk` with
    turn_left/turn_right remains the open-loop primitive; turn_to is the
    accurate version, closed on the gyro.
    """
    return {
        "type": "function",
        "function": {
            "name": "turn_to",
            "description": (
                "Rotate in place by a measured angle, closed-loop on the gyro. "
                "Positive degrees turn right (clockwise from above), negative "
                "turn left. More accurate than walk(turn_*), which is open-loop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "degrees": {
                        "type": "number",
                        "description": "Signed rotation target in degrees (-360..360).",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Stop when within this many degrees of the target (default 5).",
                    },
                },
                "required": ["degrees"],
            },
        },
    }


def walk_straight_tool_schema() -> Dict[str, Any]:
    """OpenAI tool schema for walk_straight, appended to the upstream TOOLS list.

    `walk` stays as the open-loop primitive — it is the right tool for a short
    shuffle or a deliberate arc. `walk_straight` is the accurate version:
    the gyro measures the drift while the gait runs and trims it out, so the
    robot ends up pointing the way it started.
    """
    return {
        "type": "function",
        "function": {
            "name": "walk_straight",
            "description": (
                "Walk in a straight line, holding heading closed-loop on the "
                "gyro. More accurate than walk(), which drifts off course. Use "
                "this whenever the robot should end up pointing the same way it "
                "started, or should travel along a line."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "left", "right"],
                        "description": (
                            "Travel direction; left/right are sideways crab "
                            "walking, not turns."
                        ),
                    },
                    "cycles": {
                        "type": "integer",
                        "description": "Gait cycles to walk (1-20). Roughly 3-4cm each.",
                    },
                    "speed": {
                        "type": "integer",
                        "description": "Gait speed (2-10). Higher is faster.",
                    },
                    "heading": {
                        "type": "number",
                        "description": (
                            "Line to hold, in degrees relative to the starting "
                            "heading (-45..45). Default 0, straight ahead."
                        ),
                    },
                },
                "required": ["direction"],
            },
        },
    }


# Volts. Below this the servos brown out mid-lift and the robot drops onto its
# own legs; deep-discharging the 2S pack can kill it.
BATTERY_FLOOR_V = 6.0


def owns(node_name: str, tool_name: str) -> bool:
    return TOOL_OWNER.get(tool_name) == node_name


# --------------------------------------------------------------------------
# Tool-call shim
# --------------------------------------------------------------------------
# The OpenAI SDK returns tool calls as objects with `.id` and
# `.function.name/.arguments`. Those cannot cross a process boundary as JSON,
# but the upstream `voice.build_tool_history()` and `execute_tool_calls()`
# expect that attribute shape. So we serialise to plain dicts on the wire and
# rebuild this duck-typed stand-in on the far side, letting the upstream
# helpers run unmodified.

class _ToolFunction:
    __slots__ = ("name", "arguments")

    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class ToolCall:
    __slots__ = ("id", "type", "function")

    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.type = "function"
        self.function = _ToolFunction(name, arguments)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.function.name,
            "arguments": self.function.arguments,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(data["id"], data["name"], data.get("arguments") or "{}")

    @property
    def args(self) -> Dict[str, Any]:
        try:
            return json.loads(self.function.arguments) if self.function.arguments else {}
        except json.JSONDecodeError:
            return {}


def tool_calls_to_dicts(tool_calls: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Serialise SDK tool-call objects (or our shim) for the wire."""
    out: List[Dict[str, Any]] = []
    for tc in tool_calls or []:
        out.append(
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments or "{}",
            }
        )
    return out


def tool_calls_from_dicts(data: Optional[List[Dict[str, Any]]]) -> List[ToolCall]:
    return [ToolCall.from_dict(item) for item in data or []]
