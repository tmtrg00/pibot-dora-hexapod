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

- [PINNED 2026-08-16] **Camera produces no frames — the fault is in the Pi 5 RP1 CSI-2 receive
  path, not in any sensor or cable.** Sensors enumerate and libcamera opens, configures and
  starts them, then every capture times out with `Camera frontend has timed out`. Not fixable
  in this project. An OV5647 on CAM0 and the IMX708 on CAM1 fail identically (CHANGELOG
  2026-08-17), which eliminates the sensor, the ribbon and the individual port.
  - Check state: `rpicam-hello --list-cameras` enumerates both sensors; `./run.sh camera`
    fails all three attempts in 8s each and disables the camera.
  - Confirmed at register level on this board (CHANGELOG 2026-08-17): every RP1 CSI-2 counter
    reads zero on both CSI blocks with both cameras armed — zero packets *and* zero discards,
    so nothing electrical reaches the data lanes. A kernel regression is eliminated by upstream's
    direct A/B across 6.17.0-1003 and 6.17.0-1021 (failure byte-identical), and three ribbons,
    two cameras, both ports and a checklist reseat were eliminated by swap.
  - **Answer this before buying a board:** the owner recalls the camera working previously,
    which contradicts the upstream record that it "has never delivered a frame on this robot".
    Establish whether that memory is of a different Pi or a bench test before assembly. If a
    frame ever arrived on *this* board, the RMA reasoning needs revisiting.
  - Software is now fully eliminated (CHANGELOG 2026-08-17): the failure is byte-identical on
    Ubuntu 26.04 with kernel 7.0.0-1016 and libcamera 0.7.0, counters still all zero. Combined
    with three ribbons, two sensors, both ports and a reseat, every variable changeable without
    buying hardware has been changed.
  - **Next action is the RMA.** A Raspberry Pi OS boot from a spare SD card remains available as
    warranty paperwork if the vendor asks for it; it is no longer diagnosis.
  - **Resolve first if possible:** the owner recalls the camera working previously, which no
    record supports — upstream states it has never delivered a frame on this robot. If that
    memory is of *this* board, something physical changed and the RMA reasoning needs revisiting.
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

- [TODO 2026-08-17] **Delete `/opt/pibot-dora/venv-python3.13-broken` and
  `/opt/pibot-backup-preupgrade-2026-08-17` once the sensors graph passes.** Together they hold
  the pre-upgrade venv, a 290 MB boot-partition tarball, `/etc`, the package selections and the
  displaced `old/` boot slot. Kept deliberately until the new stack is proven on hardware.

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
