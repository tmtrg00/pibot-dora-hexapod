#!/usr/bin/env python3
"""Audio node — owns the microphone, the speaker, Porcupine, Whisper and TTS.

This is the node that most justifies the port. Upstream, wake-word detection,
VAD recording, Whisper upload and TTS playback all shared one process with the
gait loop, the LED animations and the vision calls, fighting over the GIL.
Here they get a core to themselves.

Everything that touches PyAudio lives in *one* node on purpose: the mic is a
single exclusive device, and the wake-word listener and the VAD recorder have
to hand it back and forth. Upstream did that with a `threading.Event`; splitting
them across processes would mean arbitrating an exclusive device over the
network for no gain.

Structure: the blocking audio state machine runs on a worker thread, while the
main thread owns the dora event loop and is the only caller of `send_output`.
The two exchange work through queues, so no thread-safety is assumed of the
dora handle.

Outputs:
  wake         — wake word heard, before the prompt is spoken
  user_text    — a transcribed utterance (`interrupt` marks barge-in)
  speech_state — talking on/off, drives the LED mouth animation
  speech_done  — a `speak` request finished; `interrupted` if the user barged in
"""

from __future__ import annotations

import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger, load_config

common.bootstrap()

from dora import Node  # noqa: E402
from src.audio import Audio  # noqa: E402

NODE = "audio"
logger = get_logger(NODE)

POLL_TIMEOUT_S = 0.1


class AudioWorker(threading.Thread):
    """Runs the blocking mic/speaker state machine."""

    def __init__(self, audio: Audio, config: dict, outbox: "queue.Queue[tuple]"):
        super().__init__(daemon=True)
        self.audio = audio
        self.outbox = outbox
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.running = True

        wake_cfg = config.get("wake_word", {}) or {}
        self.wake_enabled = bool(wake_cfg.get("enabled", True))
        self.wake_model = wake_cfg.get(
            "model_path", "config/Hey-Pi-Bot_en_raspberry-pi_v4_0_0.ppn"
        )
        behaviour = config.get("behavior", {}) or {}
        self.ack_phrase = str(behaviour.get("wake_ack_phrase", "Yes?"))

        # Set to break the wake-word listener out of its blocking loop when a
        # speak request arrives — the robot must never talk over itself.
        self.stop_wake = threading.Event()

    def emit(self, output_id: str, payload: dict) -> None:
        self.outbox.put((output_id, payload))

    def submit(self, command: dict) -> None:
        self.inbox.put(command)
        self.stop_wake.set()

    def _take_command(self) -> dict | None:
        try:
            return self.inbox.get_nowait()
        except queue.Empty:
            return None

    def _speak(self, text: str, interruptible: bool = True) -> bool:
        """Speak, reporting mouth state to the LED node. Returns interrupted."""
        if not text:
            return False
        self.emit("speech_state", {"talking": True})
        try:
            return bool(self.audio.speak(text, interruptible=interruptible))
        except Exception as exc:
            logger.warning(f"speak failed: {exc}")
            return False
        finally:
            self.emit("speech_state", {"talking": False})

    def _listen_and_transcribe(self, interrupt: bool = False) -> None:
        try:
            recording = self.audio.record_vad(filepath="data/voice_input.wav")
        except Exception as exc:
            logger.warning(f"record failed: {exc}")
            recording = None

        if not recording:
            self.emit("user_text", {"text": "", "heard": False, "interrupt": interrupt})
            return

        try:
            text = self.audio.transcribe(recording)
        except Exception as exc:
            logger.warning(f"transcribe failed: {exc}")
            text = None

        text = (text or "").strip()
        self.emit("user_text", {"text": text, "heard": bool(text), "interrupt": interrupt})

    def run(self) -> None:
        while self.running:
            command = self._take_command()

            if command is not None:
                kind = command.get("kind")
                if kind == "speak":
                    interrupted = self._speak(
                        str(command.get("text") or ""),
                        interruptible=bool(command.get("interruptible", True)),
                    )
                    self.emit("speech_done", {"interrupted": interrupted})
                    if interrupted:
                        # Barge-in: the user started talking over the reply, so
                        # capture what they said instead of returning to idle.
                        self._listen_and_transcribe(interrupt=True)
                elif kind == "listen":
                    self._listen_and_transcribe(interrupt=False)
                continue

            if not self.wake_enabled:
                # Nothing to do but wait for the next command.
                self.stop_wake.wait(timeout=POLL_TIMEOUT_S)
                self.stop_wake.clear()
                continue

            self.stop_wake.clear()
            try:
                detected = self.audio.listen_for_wake_word(
                    self.wake_model, stop_event=self.stop_wake
                )
            except Exception as exc:
                logger.warning(f"wake word listener failed: {exc}")
                self.stop_wake.wait(timeout=1.0)
                continue

            if not detected:
                continue

            logger.info("wake word detected")
            self.emit("wake", {})
            self._speak(self.ack_phrase, interruptible=False)
            self._listen_and_transcribe(interrupt=False)


def main() -> None:
    node = Node()
    config = load_config()

    # display=None: the mouth animation is driven by `speech_state` messages to
    # the LED node instead of an in-process object.
    audio = Audio(config, display=None)

    outbox: "queue.Queue[tuple]" = queue.Queue()
    worker = AudioWorker(audio, config, outbox)
    worker.start()
    logger.info(
        f"audio ready (wake word {'enabled' if worker.wake_enabled else 'disabled'})"
    )

    running = True
    try:
        while running:
            # Forward anything the worker produced.
            while True:
                try:
                    output_id, payload = outbox.get_nowait()
                except queue.Empty:
                    break
                node.send_output(output_id, encode(payload))

            event = node.next(timeout=POLL_TIMEOUT_S)
            if event is None:
                continue
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue

            payload = decode(event) or {}
            if event["id"] == "speak":
                worker.submit({"kind": "speak", **payload})
            elif event["id"] == "listen":
                worker.submit({"kind": "listen", **payload})
    finally:
        running = False
        worker.running = False
        worker.stop_wake.set()
        try:
            audio.close()
        except Exception:
            pass
        logger.info("stopped")


if __name__ == "__main__":
    main()
