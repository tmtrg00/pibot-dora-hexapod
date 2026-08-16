#!/usr/bin/env python3
"""Brain node — behaviour and conversation state machine.

This replaces the `while True` loop in the upstream `src/main.py`. It owns no
hardware at all: it decides, and everything physical happens by message. That
is the whole point of the split — the brain can never block on a servo, a
network call or a microphone.

The upstream turn was written straight-line (record -> query -> tools -> speak,
with a nested loop for barge-in). Straight-line code cannot survive being split
across processes, so the same behaviour is expressed here as an explicit state
machine:

    IDLE ---wake--> LISTENING ---user_text--> THINKING
                                                  |
                        +----- tool_calls --------+------ plain reply -----+
                        v                                                  |
                     ACTING ---all results in---> (reply text) ------------+
                                                                           v
                                                                       SPEAKING
                                                                           |
                                            interrupted? --yes--> LISTENING
                                                           --no--> IDLE

Every wait has a deadline. A node that dies mid-turn leaves the brain stuck
forever otherwise, which is precisely the silent-hang failure mode this project
has been bitten by before.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import (
    TOOL_OWNER,
    decode,
    encode,
    get_logger,
    load_config,
    tool_calls_from_dicts,
)

common.bootstrap()

from dora import Node  # noqa: E402
from src.actions import ACTION_EMOTIONS  # noqa: E402
from src.voice import VoiceHistory  # noqa: E402

NODE = "brain"
logger = get_logger(NODE)

# How long to wait for the pieces of a turn before giving up and moving on.
LLM_TIMEOUT_S = 45.0
TOOL_TIMEOUT_S = 30.0
SPEECH_TIMEOUT_S = 90.0
LISTEN_TIMEOUT_S = 30.0

# Don't nag about a low battery more than this often.
BATTERY_WARN_COOLDOWN_S = 300.0


class Brain:
    def __init__(self, node: Node):
        self.node = node
        self.config = load_config()

        history_cfg = self.config.get("history", {}) or {}
        self.history_file = history_cfg.get("file", "data/conversation_history.json")
        self.history = VoiceHistory(max_turns=int(history_cfg.get("window", 10)))
        self._load_history()

        behaviour = self.config.get("behavior", {}) or {}
        self.observation_interval = float(behaviour.get("observation_interval", 60))
        self.post_voice_cooldown = float(behaviour.get("post_voice_cooldown", 20))
        self.idle_threshold = float(behaviour.get("idle_threshold", 30))

        self.state = "IDLE"
        self.state_deadline = 0.0

        self.request_seq = 0
        self.pending_llm_id: Optional[str] = None

        # Current turn.
        self.user_text = ""
        self.response_text = ""
        self.tool_calls: List[Any] = []
        self.tool_results: Dict[str, str] = {}
        self.pending_tools: set = set()
        self.photo_tool_id: Optional[str] = None

        self.last_action = time.time()
        self.last_observation = time.time()
        self.last_voice_interaction = 0.0
        self.last_battery: Optional[Dict[str, float]] = None
        self.last_battery_warning = 0.0
        self.last_distance_cm: Optional[float] = None
        self.camera_ok = True  # optimistic until a capture fails
        self.gait_ok = True

        # Deferred actions: (due_time, callable). Lets multi-step idle
        # behaviours run without ever blocking the event loop.
        self.script: List[tuple] = []

    # -- plumbing ---------------------------------------------------------

    def send(self, output_id: str, payload: dict) -> None:
        self.node.send_output(output_id, encode(payload))

    def emotion(self, name: str) -> None:
        self.send("emotion", {"emotion": name})

    def speak(self, text: str, interruptible: bool = True) -> None:
        if not text:
            self.set_state("IDLE")
            return
        self.emotion("happy")
        self.send("speak", {"text": text, "interruptible": interruptible})
        self.set_state("SPEAKING", SPEECH_TIMEOUT_S)

    def next_id(self) -> str:
        self.request_seq += 1
        return f"r{self.request_seq}"

    def set_state(self, state: str, timeout_s: Optional[float] = None) -> None:
        self.state = state
        self.state_deadline = time.time() + timeout_s if timeout_s else 0.0
        if state == "IDLE":
            self.last_action = time.time()

    def defer(self, delay_s: float, fn) -> None:
        self.script.append((time.time() + delay_s, fn))

    def _load_history(self) -> None:
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r", encoding="utf-8") as fh:
                    self.history.load(json.load(fh))
                logger.info(f"loaded {len(self.history.to_list()) // 2} previous turns")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"History load failed: {exc}")

    def _save_history(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.history_file) or ".", exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as fh:
                json.dump(self.history.to_list(), fh, indent=2)
        except OSError as exc:
            logger.warning(f"History save failed: {exc}")

    # -- conversation turn ------------------------------------------------

    def begin_turn(self, text: str) -> None:
        self.user_text = text
        self.response_text = ""
        self.tool_calls = []
        self.tool_results = {}
        self.pending_tools = set()
        self.photo_tool_id = None

        self.history.add_user(text)
        self.emotion("thinking")

        self.pending_llm_id = self.next_id()
        self.send(
            "llm_request",
            {
                "kind": "turn",
                "id": self.pending_llm_id,
                "user_text": text,
                "history": self.history.get_context(),
            },
        )
        self.set_state("THINKING", LLM_TIMEOUT_S)

    def on_turn_response(self, payload: dict) -> None:
        self.response_text = payload.get("text") or ""
        self.tool_calls = tool_calls_from_dicts(payload.get("tool_calls"))

        if not self.tool_calls:
            if self.response_text:
                self.history.add_assistant(self.response_text)
            self._save_history()
            self.finish_turn()
            return

        for call in self.tool_calls:
            name = call.function.name
            self.emotion(ACTION_EMOTIONS.get(name, "neutral"))
            self.pending_tools.add(call.id)

            if name == "take_photo":
                # Mirrors the upstream special case: capture, then describe the
                # image, and let that description become the spoken reply.
                self.photo_tool_id = call.id
                self.send(
                    "capture",
                    {
                        "purpose": "tool",
                        "path": call.args.get("filepath") or "data/voice_photo.jpg",
                    },
                )
            elif name in TOOL_OWNER:
                self.send("tool_call", {"id": call.id, "name": name, "args": call.args})
            else:
                self.tool_results[call.id] = f"{name} not available"
                self.pending_tools.discard(call.id)

        if self.pending_tools:
            self.set_state("ACTING", TOOL_TIMEOUT_S)
        else:
            self.finish_tools()

    def on_tool_result(self, payload: dict) -> None:
        call_id = payload.get("id")
        if call_id is None or call_id not in self.pending_tools:
            return
        self.tool_results[call_id] = str(payload.get("text") or "done")
        self.pending_tools.discard(call_id)
        if not self.pending_tools and self.state == "ACTING":
            self.finish_tools()

    def finish_tools(self) -> None:
        # Any tool that never answered gets an explicit note, so the model is
        # told the truth rather than silently seeing a missing result.
        for call in self.tool_calls:
            self.tool_results.setdefault(call.id, "no result (timed out)")

        result_shim = {"text": self.response_text, "tool_calls": self.tool_calls}
        self.history.add_tool_response(result_shim, self.tool_results)
        self._save_history()
        self.finish_turn()

    def finish_turn(self) -> None:
        self.last_voice_interaction = time.time()
        self.send(
            "llm_request",
            {
                "kind": "remember",
                "user_text": self.user_text,
                "assistant_text": self.response_text,
                "tool_results": self.tool_results,
                "source": "voice",
            },
        )
        if self.response_text:
            self.speak(self.response_text)
        else:
            self.emotion("neutral")
            self.set_state("IDLE")

    # -- autonomous behaviour ---------------------------------------------

    def start_observation(self) -> None:
        self.emotion("curious")
        self.send("capture", {"purpose": "observation", "path": "data/observation.jpg"})
        self.set_state("OBSERVING", LLM_TIMEOUT_S)
        self.last_observation = time.time()

    def on_image(self, payload: dict) -> None:
        purpose = payload.get("purpose")
        ok = bool(payload.get("ok"))

        if not ok:
            self.camera_ok = False
            if purpose == "tool" and self.photo_tool_id:
                self.tool_results[self.photo_tool_id] = (
                    f"Photo capture failed: {payload.get('error') or 'unknown error'}"
                )
                self.pending_tools.discard(self.photo_tool_id)
                self.photo_tool_id = None
                if not self.pending_tools and self.state == "ACTING":
                    self.finish_tools()
            elif purpose == "observation":
                self.set_state("IDLE")
            return

        self.camera_ok = True
        self.emotion("thinking")
        self.pending_llm_id = self.next_id()
        self.send(
            "llm_request",
            {
                "kind": "vision",
                "id": self.pending_llm_id,
                "image_path": payload.get("path"),
                "prompt": (
                    "Describe what you see briefly."
                    if purpose == "tool"
                    else "What do you see in this image? Describe it briefly and conversationally."
                ),
            },
        )
        self._vision_purpose = purpose
        self._vision_image = payload.get("path")

    def on_vision_response(self, payload: dict) -> None:
        text = payload.get("text") or ""
        purpose = getattr(self, "_vision_purpose", "observation")

        if purpose == "tool":
            described = text or "I took a photo but couldn't describe it."
            if self.photo_tool_id:
                self.tool_results[self.photo_tool_id] = described
                self.pending_tools.discard(self.photo_tool_id)
                self.photo_tool_id = None
            # Upstream lets the photo description replace the spoken reply.
            self.response_text = described
            if not self.pending_tools:
                self.finish_tools()
            else:
                self.set_state("ACTING", TOOL_TIMEOUT_S)
            return

        # Autonomous observation.
        if text:
            self.send(
                "llm_request",
                {
                    "kind": "observation",
                    "summary": text,
                    "image_path": getattr(self, "_vision_image", None),
                },
            )
            self.speak(text)
        else:
            self.set_state("IDLE")

    def idle_behaviour(self) -> None:
        random.choice([self.look_around, self.casual_movement, self.sensor_check])()
        self.last_action = time.time()

    def look_around(self) -> None:
        self.emotion("curious")
        self.send("tool_call", {"id": self.next_id(), "name": "move_head", "args": {"pan": -25, "tilt": 5}})
        self.defer(0.6, lambda: self.send(
            "tool_call", {"id": self.next_id(), "name": "move_head", "args": {"pan": 25, "tilt": 5}}))
        self.defer(1.2, lambda: self.send(
            "tool_call", {"id": self.next_id(), "name": "move_head", "args": {"pan": 0, "tilt": 0}}))
        self.defer(1.6, lambda: self.emotion("neutral"))

    def casual_movement(self) -> None:
        self.emotion("curious")
        roll = random.choice([-8, 8])
        self.send("tool_call", {"id": self.next_id(), "name": "set_attitude", "args": {"roll": roll, "pitch": 0, "yaw": 0}})
        self.defer(1.0, lambda: self.send(
            "tool_call", {"id": self.next_id(), "name": "set_attitude", "args": {"roll": 0, "pitch": 0, "yaw": 0}}))
        self.defer(1.6, lambda: self.emotion("neutral"))

    def sensor_check(self) -> None:
        # Sensor values now arrive continuously on their own streams, so this
        # no longer blocks on a hardware read the way the upstream version did.
        self.emotion("thinking")
        parts = []
        if self.last_distance_cm is not None:
            parts.append(f"distance {self.last_distance_cm:.0f}cm")
        if self.last_battery is not None:
            parts.append(
                f"battery load={self.last_battery['load_v']:.2f}V pi={self.last_battery['pi_v']:.2f}V"
            )
        if parts:
            logger.info("sensor check: " + ", ".join(parts))
        self.defer(0.5, lambda: self.emotion("neutral"))

    def on_battery(self, payload: dict) -> None:
        self.last_battery = {"load_v": float(payload["load_v"]), "pi_v": float(payload["pi_v"])}
        low = self.last_battery["load_v"] < 5.5 or self.last_battery["pi_v"] < 6.0
        if not low:
            return
        if time.time() - self.last_battery_warning < BATTERY_WARN_COOLDOWN_S:
            return
        self.last_battery_warning = time.time()
        logger.warning(f"battery low: {self.last_battery}")
        if self.state == "IDLE":
            self.emotion("surprised")
            self.speak("Battery is getting low. Please recharge soon.", interruptible=False)

    # -- tick -------------------------------------------------------------

    def on_tick(self) -> None:
        now = time.time()

        due = [fn for when, fn in self.script if when <= now]
        self.script = [(when, fn) for when, fn in self.script if when > now]
        for fn in due:
            try:
                fn()
            except Exception:
                logger.exception("deferred action failed")

        # A wait that never completed: recover rather than hang forever.
        if self.state_deadline and now > self.state_deadline:
            logger.warning(f"state {self.state} timed out, returning to idle")
            if self.state == "ACTING":
                self.finish_tools()
                return
            self.emotion("neutral")
            self.set_state("IDLE")

        if self.state != "IDLE":
            return

        if now - self.last_action > self.idle_threshold:
            self.idle_behaviour()

        if (
            self.camera_ok
            and now - self.last_observation > self.observation_interval
            and now - self.last_voice_interaction > self.post_voice_cooldown
        ):
            self.start_observation()

    # -- event dispatch ---------------------------------------------------

    def handle(self, input_id: str, payload: dict) -> None:
        if input_id == "tick":
            self.on_tick()

        elif input_id == "wake":
            logger.info("wake word detected")
            self.emotion("curious")
            self.set_state("LISTENING", LISTEN_TIMEOUT_S)

        elif input_id == "user_text":
            if not payload.get("heard"):
                self.speak("I did not catch that.", interruptible=False)
                return
            text = str(payload.get("text") or "")
            logger.info(f"user: {text}")
            self.begin_turn(text)

        elif input_id == "speech_done":
            if payload.get("interrupted"):
                logger.info("speech interrupted by user")
                self.set_state("LISTENING", LISTEN_TIMEOUT_S)
            else:
                self.emotion("neutral")
                self.set_state("IDLE")

        elif input_id == "llm_response":
            if payload.get("id") != self.pending_llm_id:
                return  # stale response from a timed-out request
            self.pending_llm_id = None
            if payload.get("kind") == "vision":
                self.on_vision_response(payload)
            else:
                self.on_turn_response(payload)

        elif input_id.startswith("tool_result"):
            # One input id per producing node (dora maps each input to exactly
            # one source), but they all mean the same thing here.
            self.on_tool_result(payload)

        elif input_id == "image":
            self.on_image(payload)

        elif input_id == "distance":
            self.last_distance_cm = float(payload.get("cm", 0.0))

        elif input_id == "battery":
            self.on_battery(payload)

        elif input_id == "health":
            alive = bool(payload.get("gait_thread_alive"))
            if alive != self.gait_ok:
                self.gait_ok = alive
                logger.error("gait thread down — motion commands will be refused" if not alive
                             else "gait thread recovered")


def main() -> None:
    node = Node()
    brain = Brain(node)

    brain.emotion("happy")
    brain.send("speak", {"text": "Hello. PiBot-Hexapod is online.", "interruptible": False})
    brain.set_state("SPEAKING", SPEECH_TIMEOUT_S)
    brain.send("tool_call", {"id": brain.next_id(), "name": "stand", "args": {}})
    logger.info("brain running")

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue
        payload = decode(event) or {}
        try:
            brain.handle(event["id"], payload)
        except Exception:
            logger.exception(f"handling {event['id']} failed")

    brain._save_history()
    logger.info("stopped")


if __name__ == "__main__":
    main()
