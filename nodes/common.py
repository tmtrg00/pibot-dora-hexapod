"""Shared plumbing for every PiBot-Hexapod dora node.

Three jobs:

1. `bootstrap()` — make a dora node process look exactly like the original
   single-process runtime: chdir into the upstream project root and put it on
   `sys.path`, so every relative path baked into the upstream code
   (`config/config.yaml`, `point.txt`, `data/*`, `.env`) resolves unchanged.
   This is what lets us import and reuse `src.control`, `src.actions`, etc.
   instead of forking 5,900 lines of driver code.

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

# Root of the upstream (non-dora) project. Every node reuses its drivers,
# config, servo calibration and data directory. Override to point at a fork.
PIBOT_HOME = os.environ.get("PIBOT_HOME", "/opt/pibot-hexapod")

_BOOTSTRAPPED = False


def bootstrap() -> str:
    """Make the upstream project importable and its relative paths valid.

    Idempotent. Returns the project root.
    """
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED:
        if not os.path.isdir(os.path.join(PIBOT_HOME, "src")):
            raise RuntimeError(
                f"PIBOT_HOME={PIBOT_HOME!r} does not look like the PiBot-Hexapod "
                f"project (no src/ directory). Set PIBOT_HOME correctly."
            )
        if PIBOT_HOME not in sys.path:
            sys.path.insert(0, PIBOT_HOME)
        os.chdir(PIBOT_HOME)
        _BOOTSTRAPPED = True
    return PIBOT_HOME


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
