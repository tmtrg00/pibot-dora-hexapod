"""
test_audio.py — Microphone, speaker, TTS, and wake word test
Run from project root: python test/test_audio.py

Requires: OPENAI_API_KEY in .env for TTS test
          PICOVOICE_ACCESS_KEY in .env for wake word test
"""
import time
import sys
import os
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from dotenv import load_dotenv
load_dotenv()

with open("config/config.yaml") as f:
    config = yaml.safe_load(f)

from src.audio import Audio


def pause(msg="Press Enter to continue..."):
    try:
        input(f"\n  --> {msg}")
    except EOFError:
        time.sleep(1)


def main():
    print("=== Audio System Test ===")
    print("Device: Jabra EVOLVE LINK MS (USB, hw:2,0)\n")

    a = Audio(config["audio"])

    try:
        # ── 1. Record and play back ──────────────────────────────────────────
        print("[1] Microphone recording — 3 seconds")
        pause("Press Enter to start recording (speak something)...")
        path = a.record(duration=3, filepath="/tmp/test_recording.wav")
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        print(f"    Recorded {size:,} bytes → {path}")

        print("\n[2] Playback — playing back what you just said")
        pause("Press Enter to play back...")
        a.play("/tmp/test_recording.wav")

        # ── 2. VAD (voice activity detection) recording ─────────────────────
        print("\n[3] VAD recording — speaks until silence (max 10s)")
        pause("Press Enter then speak. Recording stops when you stop talking...")
        vad_path = a.record_vad(filepath="/tmp/test_vad.wav", max_duration=10)
        if vad_path and os.path.exists(vad_path):
            size = os.path.getsize(vad_path)
            print(f"    VAD recorded {size:,} bytes")
            a.play(vad_path)
        else:
            print("    VAD returned no file")

        # ── 3. TTS ──────────────────────────────────────────────────────────
        print("\n[4] Text-to-speech (OpenAI TTS)")
        pause("Press Enter to hear TTS...")
        a.speak(
            "Hello! I am Pi Bot. My hexapod body has six legs, an ultrasonic sensor, "
            "RGB LEDs, and a camera. I am ready to walk, dance, and explore.",
            filepath="/tmp/test_tts.wav"
        )

        # ── 4. Wake word ─────────────────────────────────────────────────────
        print("\n[5] Wake word detection — say 'Hey Pi Bot' within 15 seconds")
        pause("Press Enter to start listening...")
        stop = threading.Event()
        result = [False]

        def listen():
            result[0] = a.listen_for_wake_word(
                model_path="config/Hey-Pi-Bot_en_raspberry-pi_v4_0_0.ppn",
                stop_event=stop
            )

        t = threading.Thread(target=listen, daemon=True)
        t.start()

        for remaining in range(15, 0, -1):
            if result[0]:
                break
            print(f"    Listening... {remaining}s remaining", end="\r", flush=True)
            time.sleep(1)

        stop.set()
        t.join(timeout=2)

        if result[0]:
            print("\n    Wake word DETECTED!")
            a.speak("Yes? I heard you!", filepath="/tmp/test_wakeword_ack.wav")
        else:
            print("\n    No wake word detected in 15s (say 'Hey Pi Bot' next time)")

        print("\nAudio test COMPLETE")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        a.close()


if __name__ == "__main__":
    main()
