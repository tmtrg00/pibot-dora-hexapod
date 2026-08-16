#!/usr/bin/env python3
"""Interactive PiBot-Hexapod chat interface (text + voice + wake word)."""

import json
import logging
import os
import sys
import threading

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from src import memory_db
from src.actions import TOOLS
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("pi_chat_hexapod")

    print("=" * 60)
    print("PiBot-Hexapod Chat")
    print("=" * 60)
    print("Commands:")
    print("  v / voice        Record one voice command")
    print("  w / wake         Wake-word loop (say 'Hey Pi Bot')")
    print("  look             Capture and describe current view")
    print("  image <path>     Analyze a local image")
    print("  quit / exit      Leave")
    print("=" * 60)

    with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r", encoding="utf-8") as file_handle:
        config = yaml.safe_load(file_handle)

    logger.info("Initializing LEDs/display...")
    led = Led()
    display = LedDisplay(config, led)
    display.animate_boot()

    logger.info("Initializing audio...")
    audio = Audio(config, display=display)

    logger.info("Initializing LLM...")
    llm = LLMHandler()

    logger.info("Initializing hexapod control...")
    control = None
    try:
        control = Control()
        control.condition_thread.start()
        control.relax(False)
    except Exception as exc:
        logger.warning(f"Control unavailable: {exc}")

    ultrasonic = None
    buzzer = None
    adc = None
    try:
        ultrasonic = Ultrasonic()
    except Exception as exc:
        logger.warning(f"Ultrasonic unavailable: {exc}")
    try:
        buzzer = Buzzer()
    except Exception as exc:
        logger.warning(f"Buzzer unavailable: {exc}")
    try:
        adc = ADC()
    except Exception as exc:
        logger.warning(f"ADC unavailable: {exc}")

    logger.info("Initializing camera...")
    camera = CameraAdapter()
    camera_available = camera.initialize()
    logger.info("Camera available" if camera_available else "Camera unavailable")

    hardware = {
        "control": control,
        "led": led,
        "ultrasonic": ultrasonic,
        "buzzer": buzzer,
        "adc": adc,
        "servo": control.servo if control is not None else None,
        "camera": camera,
    }

    # Conversation history
    history_cfg = config.get("history", {})
    history_file = history_cfg.get("file", "data/conversation_history.json")
    history_window = history_cfg.get("window", 10)
    voice_history = VoiceHistory(max_turns=history_window)

    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as file_handle:
                voice_history.load(json.load(file_handle))
            logger.info(f"Loaded {len(voice_history.to_list()) // 2} previous turns")
        except (OSError, json.JSONDecodeError):
            logger.warning("History file unreadable, starting fresh")

    def save_history() -> None:
        os.makedirs(os.path.dirname(history_file) or ".", exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as file_handle:
            json.dump(voice_history.to_list(), file_handle, indent=2)

    # Memory DB
    memory_cfg = config.get("memory", {})
    memory_enabled = memory_cfg.get("enabled", False)
    memory_max_items = memory_cfg.get("max_items", 5)
    memory_prompt = memory_cfg.get("summary_prompt")

    if memory_enabled:
        try:
            memory_db.ensure_schema()
            memory_db.maintain()
            logger.info("Memory DB enabled")
        except Exception as exc:
            logger.warning(f"Memory DB unavailable: {exc}")
            memory_enabled = False

    def memory_context(query_text: str) -> str:
        if not memory_enabled:
            return ""
        return build_memory_context(memory_db, query_text=query_text, max_items=memory_max_items)

    def save_memory(user_text: str, assistant_text: str, tool_results: dict[str, str] | None = None) -> None:
        if not memory_enabled:
            return
        store_memory_async(
            llm,
            memory_db,
            user_text,
            assistant_text or "",
            tool_results=tool_results,
            prompt_override=memory_prompt,
            source="chat",
        )

    initiative = InitiativePolicy(config.get("initiative", {}))

    def query_with_policy(user_text: str, *, tools=None, force_disable_proactive: bool = False):
        allow_proactive, turn_instructions = initiative.prepare_turn(
            user_text,
            force_disable=force_disable_proactive,
            has_memory_context=bool(memory_context(user_text).strip()),
        )
        result = llm.query(
            user_text,
            tools=tools,
            history=voice_history.get_context(),
            memory_context=memory_context(user_text),
            turn_instructions=turn_instructions,
        )

        if isinstance(result, dict):
            text = result.get("text", "")
            tool_calls = result.get("tool_calls", []) or []
        else:
            text = str(result)
            tool_calls = []

        initiative.register_assistant_turn(
            text,
            allowed=allow_proactive,
            had_tool_calls=bool(tool_calls),
        )
        return result

    def handle_tool_response(result, response_text: str):
        tool_results = {}
        tool_calls = []
        if isinstance(result, dict):
            tool_calls = result.get("tool_calls", []) or []

        if tool_calls:
            extra, tool_results = execute_tool_calls(
                tool_calls,
                llm,
                hardware,
                display,
                camera_available,
            )
            if extra:
                response_text = extra
            elif not response_text and tool_results:
                response_text = "Done."

        return response_text, tool_calls, tool_results

    display.show_emotion("happy")
    audio.speak("Hello. PiBot-Hexapod chat is ready.")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue

            if user_input.lower() in {"quit", "exit", "q"}:
                display.show_emotion("neutral")
                audio.speak("Goodbye.")
                break

            if user_input.lower() in {"v", "voice", "listen"}:
                text = record_and_transcribe(audio, display)
                if not text:
                    print("Pi: I did not catch that.")
                    audio.speak("I did not catch that.")
                    continue

                logger.info(f"User (voice): {text}")
                voice_history.add_user(text)
                result = query_with_policy(text, tools=TOOLS)

                if isinstance(result, dict):
                    response_text = result.get("text", "")
                else:
                    response_text = str(result)

                response_text, tool_calls, tool_results = handle_tool_response(result, response_text)

                if response_text:
                    print(f"Pi: {response_text}")
                    speak_response(response_text, audio, display)

                if tool_calls:
                    voice_history.add_tool_response(result, tool_results)
                elif response_text:
                    voice_history.add_assistant(response_text)
                save_history()
                save_memory(text, response_text or "", tool_results)
                continue

            if user_input.lower() in {"w", "wake", "wakeword"}:
                wake_cfg = config.get("wake_word", {})
                model_path = wake_cfg.get("model_path", "config/Hey-Pi-Bot_en_raspberry-pi_v4_0_0.ppn")
                stop_wake_word = threading.Event()

                logger.info("Wake mode active (Ctrl+C to exit wake mode)")
                print("Say 'Hey Pi Bot' to talk.")

                try:
                    while True:
                        display.show_emotion("neutral")
                        stop_wake_word.clear()
                        if not audio.listen_for_wake_word(model_path, stop_event=stop_wake_word):
                            break

                        display.show_emotion("curious")
                        audio.speak("Yes?")
                        text = record_and_transcribe(audio, display)
                        if not text:
                            audio.speak("I did not catch that.")
                            continue

                        logger.info(f"User (wake): {text}")
                        voice_history.add_user(text)
                        result = query_with_policy(text, tools=TOOLS)

                        if isinstance(result, dict):
                            response_text = result.get("text", "")
                        else:
                            response_text = str(result)

                        response_text, tool_calls, tool_results = handle_tool_response(result, response_text)

                        if response_text:
                            print(f"Pi: {response_text}")
                            interrupted = speak_response(response_text, audio, display)
                        else:
                            interrupted = False

                        while interrupted:
                            text = record_and_transcribe(audio, display)
                            if not text:
                                break
                            voice_history.add_user(text)
                            result = query_with_policy(
                                text,
                                tools=TOOLS,
                                force_disable_proactive=initiative.disable_during_interrupts,
                            )

                            if isinstance(result, dict):
                                response_text = result.get("text", "")
                            else:
                                response_text = str(result)

                            response_text, tool_calls, tool_results = handle_tool_response(result, response_text)
                            if response_text:
                                print(f"Pi: {response_text}")
                                interrupted = speak_response(response_text, audio, display)
                            else:
                                interrupted = False

                            if tool_calls:
                                voice_history.add_tool_response(result, tool_results)
                            elif response_text:
                                voice_history.add_assistant(response_text)
                            save_history()
                            save_memory(text, response_text or "", tool_results)

                        if tool_calls:
                            voice_history.add_tool_response(result, tool_results)
                        elif response_text:
                            voice_history.add_assistant(response_text)
                        save_history()
                        save_memory(text, response_text or "", tool_results)

                except KeyboardInterrupt:
                    stop_wake_word.set()
                    logger.info("Leaving wake mode")
                continue

            if user_input.lower().startswith("image "):
                path = user_input.split(" ", 1)[1].strip()
                if not path:
                    print("Usage: image <path>")
                    continue
                if not os.path.exists(path):
                    print("Pi: File not found.")
                    continue

                display.show_emotion("thinking")
                response = llm.query("What do you see in this image?", image_path=path)
                print(f"Pi: {response}")
                speak_response(str(response), audio, display)

                voice_history.add_user(user_input)
                voice_history.add_assistant(str(response))
                save_history()
                save_memory(user_input, str(response))
                continue

            if user_input.lower() in {"look", "what do you see"} or "take a photo" in user_input.lower():
                if not camera_available:
                    print("Pi: Camera is unavailable.")
                    audio.speak("Camera is unavailable.")
                    continue
                display.show_emotion("curious")
                image = camera.capture("data/pi_view.jpg")
                if not image:
                    print("Pi: Photo capture failed.")
                    audio.speak("Photo capture failed.")
                    continue

                display.show_emotion("thinking")
                response = llm.query("Describe what you see. Be concise and conversational.", image_path=image)
                print(f"Pi: {response}")
                speak_response(str(response), audio, display)

                voice_history.add_user(user_input)
                voice_history.add_assistant(str(response))
                save_history()
                save_memory(user_input, str(response))
                continue

            # Regular text query with tool support
            display.show_emotion("thinking")
            voice_history.add_user(user_input)
            result = query_with_policy(user_input, tools=TOOLS)
            if isinstance(result, dict):
                response_text = result.get("text", "")
            else:
                response_text = str(result)

            response_text, tool_calls, tool_results = handle_tool_response(result, response_text)

            if response_text:
                print(f"Pi: {response_text}")
                speak_response(response_text, audio, display)
            else:
                print("Pi: Done.")

            if tool_calls:
                voice_history.add_tool_response(result, tool_results)
            elif response_text:
                voice_history.add_assistant(response_text)
            save_history()
            save_memory(user_input, response_text or "", tool_results)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            break
        except Exception as exc:
            logger.error(f"Unexpected error: {exc}")

    # Cleanup
    try:
        camera.close()
    except Exception:
        pass

    if ultrasonic is not None:
        try:
            ultrasonic.close()
        except Exception:
            pass

    if buzzer is not None:
        try:
            buzzer.close()
        except Exception:
            pass

    if adc is not None:
        try:
            adc.close_i2c()
        except Exception:
            pass

    if control is not None:
        try:
            control.relax(True)
        except Exception:
            pass

    audio.close()
    display.close()


if __name__ == "__main__":
    main()
