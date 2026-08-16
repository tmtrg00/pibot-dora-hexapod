"""
Audio module for PiBot - voice input/output with wake word detection
"""

import warnings
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

import pyaudio
import wave
import os
import time
import audioop
import subprocess
import struct
from openai import OpenAI
from dotenv import load_dotenv

class Audio:
    def __init__(self, config, display=None):
        """Initialize audio with config settings"""
        load_dotenv()
        
        self.config = config.get('audio', {})
        self.sample_rate = self.config.get('sample_rate', 44100)
        self.channels = self.config.get('channels', 1)
        self.chunk_size = self.config.get('chunk_size', 8192)
        
        # Display reference for talking animation
        self.display = display
        
        # OpenAI client for Whisper and TTS
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        # PyAudio instance
        self.audio = pyaudio.PyAudio()

        # Device selection (supports split input/output devices).
        # Backward-compatible: `device_index` applies to both when specific keys are missing.
        legacy_device = self.config.get('device_index')
        self.input_device_index = self.config.get('input_device_index', legacy_device)
        self.output_device_index = self.config.get('output_device_index', legacy_device)
        self.playback_timeout_factor = float(self.config.get('playback_timeout_factor', 3.0))
        self.playback_stall_timeout = float(self.config.get('playback_stall_timeout', 8.0))
        self._resolve_audio_devices()
        self.interrupt_monitoring_enabled = self._supports_duplex_interrupt()

        # Enable Auto Gain Control and max mic volume for USB audio
        try:
            subprocess.run(['amixer', '-c', '2', 'sset', 'Auto Gain Control', 'on'],
                          capture_output=True, check=False)
            subprocess.run(['amixer', '-c', '2', 'sset', 'Mic', '100%'],
                          capture_output=True, check=False)
        except Exception:
            pass  # Ignore if amixer not available

        print(
            f"Audio initialized: {self.sample_rate}Hz, "
            f"input={self.input_device_index}, output={self.output_device_index}, "
            f"interruptible={self.interrupt_monitoring_enabled}"
        )

    def _default_device_index(self, kind):
        """Return default input/output device index, or None."""
        try:
            if kind == "input":
                return int(self.audio.get_default_input_device_info().get("index"))
            return int(self.audio.get_default_output_device_info().get("index"))
        except Exception:
            return None

    def _validate_device(self, index, kind):
        """Return valid device index for kind, else None."""
        if index is None:
            return None
        try:
            info = self.audio.get_device_info_by_index(int(index))
            if kind == "input" and int(info.get("maxInputChannels", 0)) <= 0:
                return None
            if kind == "output" and int(info.get("maxOutputChannels", 0)) <= 0:
                return None
            return int(index)
        except Exception:
            return None

    def _resolve_audio_devices(self):
        """Choose working input/output indices (or None for system default)."""
        self.input_device_index = self._validate_device(self.input_device_index, "input")
        self.output_device_index = self._validate_device(self.output_device_index, "output")

        if self.input_device_index is None:
            self.input_device_index = self._validate_device(self._default_device_index("input"), "input")
        if self.output_device_index is None:
            self.output_device_index = self._validate_device(self._default_device_index("output"), "output")

    def _input_kwargs(self):
        kwargs = {}
        if self.input_device_index is not None:
            kwargs["input_device_index"] = self.input_device_index
        return kwargs

    def _output_kwargs(self):
        kwargs = {}
        if self.output_device_index is not None:
            kwargs["output_device_index"] = self.output_device_index
        return kwargs

    def _supports_duplex_interrupt(self):
        """
        Check if input+output monitoring should be enabled during playback.
        Disable interrupt monitor when full-duplex stream support is uncertain.
        """
        if self.input_device_index is None or self.output_device_index is None:
            return False
        try:
            self.audio.is_format_supported(
                16000,
                input_device=self.input_device_index,
                input_channels=1,
                input_format=pyaudio.paInt16,
                output_device=self.output_device_index,
                output_channels=1,
                output_format=pyaudio.paInt16,
            )
            return True
        except Exception:
            return False
    
    def record(self, duration=5, filepath="data/recording.wav"):
        """
        Record audio from microphone
        
        Args:
            duration: Recording duration in seconds
            filepath: Where to save the recording
        
        Returns:
            filepath if successful, None otherwise
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            print(f"🎤 Recording for {duration} seconds...")
            
            # Open stream
            stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                **self._input_kwargs(),
            )
            
            frames = []
            
            # Record
            for i in range(0, int(self.sample_rate / self.chunk_size * duration)):
                data = stream.read(self.chunk_size)
                frames.append(data)
            
            # Stop stream
            stream.stop_stream()
            stream.close()
            
            # Save to file
            wf = wave.open(filepath, 'wb')
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(frames))
            wf.close()
            
            print(f"✅ Recording saved: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"Recording error: {e}")
            return None
    
    def record_vad(self, filepath="data/voice_input.wav",
                   max_duration=10, silence_threshold=1.0, min_speech=0.3):
        """
        Record audio until the user stops speaking (VAD-based).

        Starts recording immediately.  Once speech is detected and then
        silence persists for `silence_threshold` seconds, recording stops.
        Hard-stops at `max_duration` seconds regardless.

        Returns filepath if speech was detected, None otherwise.
        """
        import webrtcvad

        VAD_RATE = 16000                                          # webrtcvad needs 8k/16k/32k
        FRAME_MS = 30                                             # ms per frame
        FRAME_SAMPLES = int(VAD_RATE * FRAME_MS / 1000)           # 480
        SILENCE_LIMIT = int(silence_threshold * 1000 / FRAME_MS)  # frames of silence to trigger stop
        MIN_SPEECH    = int(min_speech * 1000 / FRAME_MS)         # frames of speech before silence counts
        MAX_FRAMES    = int(max_duration * 1000 / FRAME_MS)

        try:
            os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)

            vad = webrtcvad.Vad(3)  # mode 3 — most aggressive

            stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=VAD_RATE,
                input=True,
                frames_per_buffer=FRAME_SAMPLES,
                **self._input_kwargs(),
            )

            print("🎤 Listening... (speak when ready)")

            frames         = []
            speech_frames  = 0   # total frames where speech was detected
            silence_frames = 0   # consecutive silent frames after speech

            for _ in range(MAX_FRAMES):
                chunk = stream.read(FRAME_SAMPLES, exception_on_overflow=False)
                frames.append(chunk)

                if vad.is_speech(chunk, VAD_RATE):
                    speech_frames  += 1
                    silence_frames  = 0
                elif speech_frames >= MIN_SPEECH:
                    # Only count silence after valid speech has been detected
                    silence_frames += 1
                    if silence_frames >= SILENCE_LIMIT:
                        break

            stream.stop_stream()
            stream.close()

            if speech_frames < MIN_SPEECH:
                print("⚠️  No speech detected")
                return None

            # Write WAV at VAD_RATE (Whisper accepts 16 kHz natively)
            wf = wave.open(filepath, 'wb')
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(VAD_RATE)
            wf.writeframes(b''.join(frames))
            wf.close()

            duration_s = len(frames) * FRAME_MS / 1000
            print(f"✅ Recorded {duration_s:.1f}s ({speech_frames} speech frames)")
            return filepath

        except Exception as e:
            print(f"VAD recording error: {e}")
            return None

    def listen_for_wake_word(self, model_path="config/Hey-Pi-Bot_en_raspberry-pi_v4_0_0.ppn", stop_event=None):
        """
        Block until wake word is detected (Porcupine).

        Args:
            model_path: Path to .ppn model file (relative to project root or absolute)
            stop_event: threading.Event to signal early termination

        Returns:
            True if wake word detected, False on error or if stopped
        """
        import pvporcupine

        access_key = os.getenv('PICOVOICE_ACCESS_KEY')
        if not access_key:
            print("❌ PICOVOICE_ACCESS_KEY not set in .env")
            return False

        # Resolve model path
        if not os.path.isabs(model_path):
            model_path = os.path.join(os.path.dirname(__file__), '..', model_path)
        model_path = os.path.abspath(model_path)

        if not os.path.exists(model_path):
            print(f"❌ Wake word model not found: {model_path}")
            return False

        porcupine = None
        stream = None

        try:
            porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=[model_path]
            )

            # Porcupine requires specific sample rate and frame length
            sample_rate = porcupine.sample_rate  # 16000
            frame_length = porcupine.frame_length  # 512

            stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                frames_per_buffer=frame_length,
                **self._input_kwargs(),
            )

            print("👂 Listening for wake word...")

            while True:
                # Check for external stop signal
                if stop_event and stop_event.is_set():
                    print("👂 Wake word listener stopped")
                    return False

                pcm = stream.read(frame_length, exception_on_overflow=False)
                pcm_unpacked = struct.unpack_from("h" * frame_length, pcm)

                keyword_index = porcupine.process(pcm_unpacked)
                if keyword_index >= 0:
                    print("🎯 Wake word detected!")
                    return True

        except Exception as e:
            print(f"Wake word error: {e}")
            return False

        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            if porcupine:
                porcupine.delete()

    def play(self, filepath, interruptible=False):
        """
        Play audio file through speaker.

        Args:
            filepath: Path to audio file to play
            interruptible: If True, monitor mic and stop if user speaks

        Returns:
            True if playback was interrupted, False if completed normally
        """
        import webrtcvad

        interrupted = False
        mic_stream = None
        out_stream = None
        wf = None
        vad = None
        speech_frames = 0
        interrupt_enabled = interruptible and self.interrupt_monitoring_enabled

        if interruptible and not interrupt_enabled:
            print("⚠️ Interruptible playback disabled (duplex audio unsupported)")

        try:
            print(f"🔊 Playing: {filepath}")

            wf = wave.open(filepath, 'rb')
            file_rate = wf.getframerate()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            total_frames = wf.getnframes()
            expected_duration = total_frames / max(file_rate, 1)
            max_playback_time = max(5.0, expected_duration * self.playback_timeout_factor)

            out_stream = self.audio.open(
                format=self.audio.get_format_from_width(sample_width),
                channels=channels,
                rate=file_rate,
                output=True,
                **self._output_kwargs(),
            )

            if interrupt_enabled:
                vad = webrtcvad.Vad(2)
                VAD_RATE = 16000
                VAD_FRAME_MS = 30
                VAD_FRAME_SAMPLES = int(VAD_RATE * VAD_FRAME_MS / 1000)
                INTERRUPT_THRESHOLD = 3

                mic_stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=VAD_RATE,
                    input=True,
                    frames_per_buffer=VAD_FRAME_SAMPLES,
                    **self._input_kwargs(),
                )

            playback_chunk_frames = max(512, min(self.chunk_size, 4096))
            data = wf.readframes(playback_chunk_frames)
            started_at = time.monotonic()
            last_progress_at = started_at

            while data:
                now = time.monotonic()
                if now - started_at > max_playback_time:
                    print("⚠️ Playback timeout reached; stopping audio")
                    interrupted = True
                    break
                if now - last_progress_at > self.playback_stall_timeout:
                    print("⚠️ Playback stalled; stopping audio")
                    interrupted = True
                    break

                if interrupt_enabled and mic_stream:
                    try:
                        available = mic_stream.get_read_available()
                        if available >= VAD_FRAME_SAMPLES:
                            mic_data = mic_stream.read(VAD_FRAME_SAMPLES, exception_on_overflow=False)
                            if vad.is_speech(mic_data, VAD_RATE):
                                speech_frames += 1
                                if speech_frames >= INTERRUPT_THRESHOLD:
                                    print("🛑 Interrupted by user speech")
                                    interrupted = True
                                    break
                            else:
                                speech_frames = 0
                    except Exception:
                        pass

                try:
                    out_stream.write(data, exception_on_underflow=False)
                except Exception:
                    out_stream.write(data)
                last_progress_at = time.monotonic()
                data = wf.readframes(playback_chunk_frames)

            if not interrupted:
                print("✅ Playback complete")

            return interrupted

        except KeyboardInterrupt:
            print("\n⏹️ Playback interrupted by keyboard")
            raise
        except Exception as e:
            print(f"Playback error: {e}")
            return False

        finally:
            if mic_stream:
                try:
                    mic_stream.stop_stream()
                    mic_stream.close()
                except Exception:
                    pass
            if out_stream:
                try:
                    out_stream.stop_stream()
                    out_stream.close()
                except Exception:
                    pass
            if wf:
                try:
                    wf.close()
                except Exception:
                    pass
    
    def transcribe(self, audio_path):
        """
        Convert speech to text using Whisper API
        
        Args:
            audio_path: Path to audio file
        
        Returns:
            Transcribed text or None
        """
        try:
            print("🎧 Transcribing audio...")
            
            with open(audio_path, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )
            
            text = transcript.text
            print(f"📝 Transcription: {text}")
            return text
            
        except Exception as e:
            print(f"Transcription error: {e}")
            return None
    
    def speak(self, text, filepath="data/response.mp3", interruptible=False):
        """
        Convert text to speech using OpenAI TTS and play it
        WITH talking animation on display.

        Args:
            text: Text to speak
            filepath: Temporary file for audio
            interruptible: If True, stop speaking if user starts talking

        Returns:
            True if speech was interrupted, False if completed normally
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            print(f"🗣️ Speaking: {text[:50]}...")

            # Start talking animation
            if self.display:
                self.display.start_talking()

            # Generate speech with PCM format (24000Hz)
            response = self.client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=text,
                response_format="pcm"
            )

            pcm_data = response.content

            # Resample from 24000Hz to configured playback sample rate
            resampled = audioop.ratecv(
                pcm_data,
                2,  # Sample width (16-bit = 2 bytes)
                1,  # Channels (mono)
                24000,  # Original rate
                self.sample_rate,  # Target rate
                None
            )[0]

            # Write WAV file
            wav_path = filepath.replace('.mp3', '.wav')
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(resampled)

            # Play it (with optional interrupt monitoring)
            interrupted = self.play(wav_path, interruptible=interruptible)

            # Stop talking animation
            if self.display:
                self.display.stop_talking()

            return interrupted

        except Exception as e:
            print(f"TTS error: {e}")
            # Make sure to stop talking animation on error
            if self.display:
                self.display.stop_talking()
            return False
    
    def close(self):
        """Cleanup audio resources"""
        self.audio.terminate()
        print("Audio closed")

# Test
if __name__ == "__main__":
    import yaml
    from display import Display
    
    # Load config
    with open("config/config.yaml", 'r') as f:
        config = yaml.safe_load(f)
    
    # Initialize display first
    display = Display(config)
    display.animate_boot()
    
    # Initialize audio with display
    audio = Audio(config, display=display)
    
    print("\n=== Audio Module Test ===\n")
    
    # Test 1: Record and playback
    print("Test 1: Record and playback")
    recording = audio.record(duration=3, filepath="data/test_audio.wav")
    if recording:
        time.sleep(0.5)
        audio.play(recording)
    
    time.sleep(1)
    
    # Test 2: Speech-to-text
    print("\nTest 2: Speech-to-text")
    print("Say something in 3 seconds...")
    recording = audio.record(duration=3, filepath="data/test_speech.wav")
    if recording:
        text = audio.transcribe(recording)
        print(f"You said: {text}")
    
    time.sleep(1)
    
    # Test 3: Text-to-speech with talking animation
    print("\nTest 3: Text-to-speech with talking animation")
    display.show_emotion("happy")
    audio.speak("Hello! I am Pi. I can now hear and speak! Watch my mouth move!")
    
    time.sleep(2)
    display.close()
    audio.close()
    print("\nAudio test complete!")
