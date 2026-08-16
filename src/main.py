#!/usr/bin/env python3
"""
PiBot-Hexapod autonomous runtime.

Wake word + voice interaction + memory + hexapod actions.
"""

import json
import logging
import os
import random
import sys
import threading
import time

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from src import memory_db
from src.actions import TOOLS, execute as run_action
from src.adc import ADC
from src.audio import Audio
from src.buzzer import Buzzer
from src.camera_adapter import CameraAdapter
from src.control import Control
from src.led import Led
from src.led_display import LedDisplay
from src.llm_handler import LLMHandler
from src.ultrasonic import Ultrasonic
from src.voice import (
    InitiativePolicy,
    VoiceHistory,
    build_memory_context,
    execute_tool_calls,
    record_and_transcribe,
    speak_response,
    store_memory_async,
)


class PiBotHexapod:
    def __init__(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        self.logger = logging.getLogger("pibot_hexapod")

        cfg_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
        with open(cfg_path, "r", encoding="utf-8") as file_handle:
            self.config = yaml.safe_load(file_handle)

        self.logger.info("=" * 50)
        self.logger.info("Initializing PiBot-Hexapod")
        self.logger.info("=" * 50)

        # 1) LED subsystem first for boot feedback
        self.logger.info("1. Initializing LEDs...")
        self.led = Led()
        self.display = LedDisplay(self.config, self.led)
        self.display.animate_boot()

        # 2) Audio subsystem
        self.logger.info("2. Initializing audio...")
        self.audio = Audio(self.config, display=self.display)

        # 3) Hexapod control + gait thread
        self.logger.info("3. Initializing hexapod control...")
        self.control = None
        try:
            self.control = Control()
            self.control.condition_thread.start()
            self.control.relax(False)
            self.logger.info("Hexapod control ready")
        except Exception as exc:
            self.logger.warning(f"Hexapod control unavailable: {exc}")

        # 4) Sensors and outputs
        self.logger.info("4. Initializing sensors...")
        self.ultrasonic = None
        self.buzzer = None
        self.adc = None

        try:
            self.ultrasonic = Ultrasonic()
        except Exception as exc:
            self.logger.warning(f"Ultrasonic unavailable: {exc}")

        try:
            self.buzzer = Buzzer()
        except Exception as exc:
            self.logger.warning(f"Buzzer unavailable: {exc}")

        try:
            self.adc = ADC()
        except Exception as exc:
            self.logger.warning(f"ADC unavailable: {exc}")

        # 5) Camera adapter
        self.logger.info("5. Initializing camera...")
        self.camera = CameraAdapter()
        self.camera_available = self.camera.initialize()
        if self.camera_available:
            self.logger.info("Camera ready")
        else:
            self.logger.warning("Camera unavailable")

        # 6) LLM + memory
        self.logger.info("6. Initializing LLM...")
        self.llm = LLMHandler()

        # Shared hardware dict for tool dispatch
        self.hardware = {
            "control": self.control,
            "led": self.led,
            "ultrasonic": self.ultrasonic,
            "buzzer": self.buzzer,
            "adc": self.adc,
            "servo": self.control.servo if self.control is not None else None,
            "camera": self.camera,
        }

        # State tracking
        self.last_action = time.time()
        self.idle_threshold = 30

        self.history_cfg = self.config.get("history", {})
        self.history_file = self.history_cfg.get("file", "data/conversation_history.json")
        self.history_window = self.history_cfg.get("window", 10)
        self.voice_history = VoiceHistory(max_turns=self.history_window)
        self._load_history()

        self.running = False
        self.in_voice_mode = threading.Event()
        self.wake_word_detected = threading.Event()
        self.stop_wake_word = threading.Event()

        wake_cfg = self.config.get("wake_word", {})
        self.wake_word_enabled = wake_cfg.get("enabled", True)
        self.wake_word_model = wake_cfg.get("model_path", "config/Hey-Pi-Bot_en_raspberry-pi_v4_0_0.ppn")

        # Memory config
        self.memory_cfg = self.config.get("memory", {})
        self.memory_enabled = self.memory_cfg.get("enabled", False)
        self.memory_max_items = self.memory_cfg.get("max_items", 5)
        self.memory_prompt = self.memory_cfg.get("summary_prompt")

        if self.memory_enabled:
            try:
                memory_db.ensure_schema()
                memory_db.maintain()
                self.logger.info("Memory DB enabled")
            except Exception as exc:
                self.logger.warning(f"Memory DB unavailable: {exc}")
                self.memory_enabled = False

        self.behavior_cfg = self.config.get("behavior", {})
        self.initiative_cfg = self.config.get("initiative", {})
        self.initiative_policy = InitiativePolicy(self.initiative_cfg)

        self.last_observation = time.time()
        self.observation_interval = self.behavior_cfg.get("observation_interval", 60)
        self.post_voice_cooldown = self.behavior_cfg.get("post_voice_cooldown", 20)
        self.last_voice_interaction = 0.0

        self.logger.info("Initialization complete")

    def _load_history(self) -> None:
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r", encoding="utf-8") as file_handle:
                    self.voice_history.load(json.load(file_handle))
                turns = len(self.voice_history.to_list()) // 2
                self.logger.info(f"Loaded {turns} previous conversation turns")
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.warning(f"History load failed: {exc}")

    def _save_history(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.history_file) or ".", exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as file_handle:
                json.dump(self.voice_history.to_list(), file_handle, indent=2)
        except OSError as exc:
            self.logger.warning(f"Failed to save history: {exc}")

    def boot_sequence(self) -> None:
        self.display.show_emotion("happy")
        self.audio.speak("Hello. PiBot-Hexapod is online.")
        if self.control is not None:
            run_action("stand", {}, self.hardware)

    def observe_environment(self) -> str | None:
        if not self.camera_available:
            return None

        self.display.show_emotion("curious")
        image = self.camera.capture("data/observation.jpg")
        if not image:
            return None

        self.display.show_emotion("thinking")
        response = self.llm.query(
            "What do you see in this image? Describe it briefly and conversationally.",
            image_path=image,
        )

        if response:
            self.display.show_emotion("happy")
            self.audio.speak(str(response))
            if self.memory_enabled:
                try:
                    memory_db.add_observation(summary=str(response), image_path=image, tags=["autonomous"])
                except Exception:
                    pass

        self.last_action = time.time()
        return str(response) if response else None

    def idle_behavior(self) -> None:
        actions = [self.look_around, self.casual_movement, self.sensor_check]
        random.choice(actions)()
        self.last_action = time.time()

    def look_around(self) -> None:
        self.display.show_emotion("curious")
        if self.hardware.get("servo") is not None:
            run_action("move_head", {"pan": -25, "tilt": 5}, self.hardware)
            time.sleep(0.4)
            run_action("move_head", {"pan": 25, "tilt": 5}, self.hardware)
            time.sleep(0.4)
            run_action("move_head", {"pan": 0, "tilt": 0}, self.hardware)
        else:
            time.sleep(1.0)
        self.display.show_emotion("neutral")

    def casual_movement(self) -> None:
        if self.control is None:
            return
        self.display.show_emotion("curious")
        run_action("set_attitude", {"roll": random.choice([-8, 8]), "pitch": 0, "yaw": 0}, self.hardware)
        time.sleep(0.2)
        run_action("set_attitude", {"roll": 0, "pitch": 0, "yaw": 0}, self.hardware)
        self.display.show_emotion("neutral")

    def sensor_check(self) -> None:
        distance_text = run_action("get_distance", {}, self.hardware)
        battery_text = run_action("get_battery", {}, self.hardware)
        if distance_text or battery_text:
            self.display.show_emotion("thinking")
        # Only speak battery warning when clearly low.
        if battery_text and "load=" in battery_text:
            try:
                load_v = float(battery_text.split("load=")[1].split("V")[0])
                pi_v = float(battery_text.split("pi=")[1].split("V")[0])
                if load_v < 5.5 or pi_v < 6.0:
                    self.display.show_emotion("surprised")
                    self.audio.speak("Battery is getting low. Please recharge soon.")
            except Exception:
                pass
        self.display.show_emotion("neutral")

    def _wake_word_thread(self) -> None:
        while self.running:
            if self.in_voice_mode.is_set():
                time.sleep(0.1)
                continue

            self.stop_wake_word.clear()
            if self.audio.listen_for_wake_word(self.wake_word_model, stop_event=self.stop_wake_word):
                self.wake_word_detected.set()
                while self.wake_word_detected.is_set() and self.running:
                    time.sleep(0.1)

    def _query_voice_turn(self, user_text: str, force_disable_proactive: bool = False):
        memory_context = ""
        if self.memory_enabled:
            memory_context = build_memory_context(
                memory_db,
                query_text=user_text,
                max_items=self.memory_max_items,
            )

        allow_proactive, turn_instructions = self.initiative_policy.prepare_turn(
            user_text,
            force_disable=force_disable_proactive,
            has_memory_context=bool(memory_context.strip()),
        )

        result = self.llm.query(
            user_text,
            tools=TOOLS,
            history=self.voice_history.get_context(),
            memory_context=memory_context,
            turn_instructions=turn_instructions,
        )

        if isinstance(result, str):
            response_text = result
            tool_calls = []
        else:
            response_text = result.get("text", "")
            tool_calls = result.get("tool_calls", []) or []

        self.initiative_policy.register_assistant_turn(
            response_text,
            allowed=allow_proactive,
            had_tool_calls=bool(tool_calls),
        )
        return result

    def handle_voice_interaction(self) -> None:
        self.in_voice_mode.set()
        self.stop_wake_word.set()
        time.sleep(0.15)

        try:
            self.display.show_emotion("curious")
            self.audio.speak("Yes?")

            user_text = record_and_transcribe(self.audio, self.display)
            if not user_text:
                self.audio.speak("I did not catch that.")
                self.display.show_emotion("neutral")
                return

            self.logger.info(f"User: {user_text}")
            self.voice_history.add_user(user_text)
            self.display.show_emotion("thinking")

            result = self._query_voice_turn(user_text)
            if isinstance(result, str):
                response_text = result
                tool_calls = []
            else:
                response_text = result.get("text", "")
                tool_calls = result.get("tool_calls", []) or []

            tool_results: dict[str, str] = {}
            if tool_calls:
                extra, tool_results = execute_tool_calls(
                    tool_calls,
                    self.llm,
                    self.hardware,
                    self.display,
                    self.camera_available,
                )
                if extra:
                    response_text = extra

            if tool_calls:
                self.voice_history.add_tool_response(result, tool_results)
            elif response_text:
                self.voice_history.add_assistant(response_text)
            self._save_history()

            if response_text:
                interrupted = speak_response(response_text, self.audio, self.display)
                while interrupted:
                    user_text = record_and_transcribe(self.audio, self.display)
                    if not user_text:
                        break

                    self.logger.info(f"User (interrupt): {user_text}")
                    self.voice_history.add_user(user_text)
                    self.display.show_emotion("thinking")

                    result = self._query_voice_turn(
                        user_text,
                        force_disable_proactive=self.initiative_policy.disable_during_interrupts,
                    )
                    if isinstance(result, str):
                        response_text = result
                        tool_calls = []
                    else:
                        response_text = result.get("text", "")
                        tool_calls = result.get("tool_calls", []) or []

                    tool_results = {}
                    if tool_calls:
                        extra, tool_results = execute_tool_calls(
                            tool_calls,
                            self.llm,
                            self.hardware,
                            self.display,
                            self.camera_available,
                        )
                        if extra:
                            response_text = extra

                    if tool_calls:
                        self.voice_history.add_tool_response(result, tool_results)
                    elif response_text:
                        self.voice_history.add_assistant(response_text)
                    self._save_history()

                    interrupted = bool(response_text) and speak_response(response_text, self.audio, self.display)

                    if self.memory_enabled:
                        store_memory_async(
                            self.llm,
                            memory_db,
                            user_text,
                            response_text or "",
                            tool_results=tool_results,
                            prompt_override=self.memory_prompt,
                            source="voice",
                        )
            else:
                interrupted = False

            if self.memory_enabled:
                store_memory_async(
                    self.llm,
                    memory_db,
                    user_text,
                    response_text or "",
                    tool_results=tool_results,
                    prompt_override=self.memory_prompt,
                    source="voice",
                )

            self.last_voice_interaction = time.time()
            self.display.show_emotion("neutral")

        finally:
            self.in_voice_mode.clear()
            self.wake_word_detected.clear()
            self.last_action = time.time()

    def run(self) -> None:
        self.running = True

        try:
            self.boot_sequence()

            if self.wake_word_enabled:
                wake_thread = threading.Thread(target=self._wake_word_thread, daemon=True)
                wake_thread.start()
                self.logger.info("Wake word enabled: say 'Hey Pi Bot'")

            self.logger.info("PiBot-Hexapod running. Press Ctrl+C to stop")

            while self.running:
                if self.wake_word_detected.is_set():
                    self.logger.info("Wake word detected")
                    self.handle_voice_interaction()

                if not self.in_voice_mode.is_set():
                    idle_time = time.time() - self.last_action
                    if idle_time > self.idle_threshold:
                        self.idle_behavior()

                    if self.camera_available:
                        if (
                            time.time() - self.last_observation > self.observation_interval
                            and time.time() - self.last_voice_interaction > self.post_voice_cooldown
                        ):
                            self.observe_environment()
                            self.last_observation = time.time()

                time.sleep(1.0)

        except KeyboardInterrupt:
            self.logger.info("Shutting down...")
            self.running = False
            self.stop_wake_word.set()
            time.sleep(0.3)
            self.cleanup()

    def cleanup(self) -> None:
        self.display.show_emotion("neutral")
        self.audio.speak("Goodbye.")
        time.sleep(1.0)

        try:
            self.camera.close()
        except Exception:
            pass

        if self.ultrasonic is not None:
            try:
                self.ultrasonic.close()
            except Exception:
                pass

        if self.buzzer is not None:
            try:
                self.buzzer.close()
            except Exception:
                pass

        if self.adc is not None:
            try:
                self.adc.close_i2c()
            except Exception:
                pass

        if self.control is not None:
            try:
                run_action("relax", {"enabled": True}, self.hardware)
            except Exception:
                pass

        self.audio.close()
        self.display.close()
        self.logger.info("Shutdown complete")


if __name__ == "__main__":
    PiBotHexapod().run()
