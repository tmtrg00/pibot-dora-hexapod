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
  - **[IN-FLIGHT 2026-08-17] Retest on the 26.04 stack — this is the immediate next action.**
    The Pi was upgraded to Ubuntu 26.04 LTS (CHANGELOG 2026-08-17) but has **not been rebooted**,
    so it still runs the old 6.17.0-1021 kernel. After the double reboot completes, rerun
    `rpicam-still` on both cameras and re-read the CSI counters, and compare against the
    all-zeros baseline. Verify `uname -r` reports 7.0.0-1016-raspi first — otherwise the test
    proves nothing.
  - **Then the last test:** boot Raspberry Pi OS from a spare SD card and attempt one
    `rpicam-still`. This is now for warranty evidence rather than diagnosis.
  - The node needs no change either way.
  - Blocks: vision-guided obstacle avoidance, the autonomous observation loop, and the
    responsiveness comparison that MASTERPLAN makes the definition of success.

- [PINNED 2026-08-17] **The project venv is broken by the Python 3.13 → 3.14 upgrade and must be
  rebuilt before any graph runs.** Site-packages is still `venv/lib/python3.13` while the
  interpreter is 3.14.4, so `openai`, `dotenv` and `smbus2` fail to import; `yaml` and `numpy`
  resolve only because of `--system-site-packages`. Rebuild from `requirements.txt` (the
  `venv-freeze.txt` backup is polluted with system packages), keeping `--system-site-packages`
  for `lgpio`, `libcamera` and `picamera2`. Expect the `scipy`, `opencv-python`, `numpy` and
  `RPi.GPIO` pins to need attention on 3.14 — prefer the system packages where they exist.
  Blocks every `./run.sh` graph.

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
