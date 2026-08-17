# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [PINNED 2026-08-16] **Battery pack is flat and needs charging before any further motion
  work.** Measured 6.94V at rest but 5.88–6.00V under servo load, against a 6.0V floor; a
  23-cycle turn sagged it to 5.00V against a 4.90V abort threshold, and it read 5.82V at rest
  afterwards. Every motion test so far has run with `PIBOT_BATTERY_FLOOR=5.0` at the owner's
  explicit instruction, which is a deliberate override of the safety gate and not a default.
  - Check state, **with servo power already on** — the unloaded reading is about a volt
    optimistic: `./venv/bin/python -c "from src.adc import ADC; print(ADC().read_battery_voltage())"`
  - Blocks: a complete 360° turn (needs ~47 gait cycles, roughly double what has been run),
    the full stance cycle without aborting, and any walking test.

- [PINNED 2026-08-17] **Camera produces no frames on Ubuntu, but works on Raspberry Pi OS on this
  same board. The hardware is fine — do NOT RMA the Pi.** This replaces the previous entry, which
  said the RP1 receiver was defective; see CHANGELOG 2026-08-17 for why that was wrong. The
  owner's recollection that the camera once worked was correct and is no longer an open question.
  - Check state: `rpicam-hello --list-cameras` enumerates both sensors; captures fail with
    `Camera frontend has timed out` and every CSI-2 counter reads zero.
  - Fails on both Ubuntu kernels (6.17.0-1021 and 7.0.0-1016), works on Raspberry Pi OS, so it is
    Ubuntu-specific packaging, not a kernel-version regression.
  - Everything checkable on the Ubuntu side looks correct: sensor endpoints (`data-lanes <1 2>`,
    297/450 MHz link frequencies), power rails enabling during capture (`cam0_reg` use 0→1),
    link rates programmed (437/900 Mbps), and a fully linked media pipeline with matching
    formats. The bug is below all of that.
  - Eliminated so far, each by direct test rather than argument (CHANGELOG 2026-08-17): the
    upstream CFE driver; **vendor libcamera 0.7.2 built from source** and installed to
    `/usr/local`; **the vendor CFE driver built from `raspberrypi/linux` `rpi-7.0.y`**, matching
    this kernel exactly. Also verified the device tree matches vendor sources property for
    property, including the `iommus` assignment. None of it changes the failure.
  - Do **not** re-run these: the DTB comparison, the driver override, the vendor libcamera build,
    the vendor CFE driver build, or removing the CSI `iommus` property. All are recorded as
    negative results (CHANGELOG 2026-08-17).
  - Treat the "zero packets" register evidence with suspicion — `CSI2_CH_DEBUG` and
    `CSI2_CH_FE_FRAME_ID` cover the direct `csi2 → csi2_chN` channels, but libcamera uses
    `csi2 → pisp-fe`, so they may read zero even on a working system.
  - **Decide the direction (blocks everything vision-related):** move the robot to Raspberry Pi
    OS, which is proven working on this board and is what `picamera2` is developed against, or
    stay on Ubuntu 26.04 with no camera. The remaining difference is the rest of the Ubuntu
    kernel — RP1 platform, clock, IOMMU and regulator code — which cannot be swapped piecemeal.
  - The node needs no change either way.
  - Blocks: vision-guided obstacle avoidance, the autonomous observation loop, and the
    responsiveness comparison that MASTERPLAN makes the definition of success.

- [PINNED 2026-08-17] **The rebuilt venv has never touched hardware — run the sensors graph
  before trusting it.** It is verified only at import level (CHANGELOG 2026-08-17): all 19
  third-party and all 16 project modules load under Python 3.14.4, but no graph has run and no
  device has responded. The robot electronics are unpowered — the I2C bus scans empty at every
  address and `ADC().read_battery_voltage()` raises `OSError: [Errno 121]`. Power the
  electronics, confirm `i2cdetect -y 1` shows 0x40, 0x41, 0x48 and 0x68, then `./run.sh sensors`.
  Watch particularly for `rpi_ws281x` (SPI LEDs) and `lgpio` under the new kernel, neither of
  which an import test can exercise.

- [TODO 2026-08-17] **Rewrite `requirements.txt` to match what is actually installed.** It is now
  actively misleading: a 90+ line inherited freeze pinning `anthropic`, `google-generativeai`,
  `groq`, `ollama`, Adafruit CircuitPython and `luma.oled`, none of which the code imports and
  none of which the rebuilt venv contains. The real set is `dora-rs` and `dora-rs-cli` at 0.5.0
  plus `openai`, `python-dotenv`, `pvporcupine`, `smbus2`, `webrtcvad`, `rpi-ws281x`,
  `audioop-lts` and `pydub`, with the rest coming from system packages via
  `--system-site-packages`. Left unchanged for now because replacing it is a scope decision, not
  a side effect of the rebuild.


- [TODO 2026-08-16] **Copy `.env` into this project.** The fork carried `src/`, `config/` and
  the calibration but not the secrets, so `OPENAI_API_KEY` and `PICOVOICE_ACCESS_KEY` are
  missing and the full graph cannot run. Sensor, motion, turn and stance graphs are unaffected.
  `cp /opt/pibot-hexapod/.env /opt/pibot-dora/.env && chmod 600 /opt/pibot-dora/.env`
  (RUNBOOKS §8).

- [TODO 2026-08-16] **Verify the full autonomous graph end to end.** Every node has been
  exercised except `audio`, `llm` and `brain`, which have never run against real hardware —
  the wake word, Whisper, GPT-4o, tool dispatch, TTS and barge-in path is entirely unproven.
  Needs `.env`, a charged pack and accepts API spend.

## Pinned for later

Deliberately deferred. Review when Active clears.

- [TODO 2026-08-16] **Decide where the stance work should live.** `nodes/stances.py` and the
  `set_stance` tool are in this project, so abandoning the experiment loses them. They are
  composition over the existing IK and would work equally well upstream, where both projects
  would benefit. MASTERPLAN says engine improvements belong upstream; stances sit on the line.

- [TODO 2026-08-16] **Measure the parallelism claim.** The central argument for the port is
  that audio, vision and the gait loop stop competing for one GIL. Nothing has measured it.
  Needs a working camera to be a fair test.

- [TODO 2026-08-16] **Stream camera frames as Arrow buffers instead of file paths.** Captures
  are written to JPEG and passed by path, which wastes dora's zero-copy shared memory. This is
  where the architecture would start paying for itself, and it is the prerequisite for a local
  detector node. Blocked on the camera.

- [TODO 2026-08-16] **Close the loop on turning using the IMU.** Rotation is currently
  open-loop: a measured ~7.8°/gait cycle, multiplied out. The MPU6050 is already owned by the
  `hardware` node and could report yaw, making `turn_to(degrees)` accurate and
  surface-independent.

- [TODO 2026-08-16] **Use stances in the gaits.** Foot spread now changes `body_points`, which
  `run_gait` deep-copies, so a wide stance already widens the walking gait. Nothing exercises
  that yet — walking in `brace` versus `narrow` is untested and is the obvious next movement
  experiment.

- [TODO 2026-08-16] **Restructure the brain as a behaviour tree (`py_trees`).** Carried over
  from upstream. The brain owns no hardware, so its logic can be replaced without touching any
  other node — this architecture makes it a contained change.

- [TODO 2026-08-16] **Remove `src/server.py`.** The Freenove TCP control path is a
  testing-only holdover with no node using it, kept only because the fork copied `src/` whole.
