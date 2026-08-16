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

- [PINNED 2026-08-16] **Camera produces no frames — the CSI-2 physical link carries no data.
  Software has been exhaustively eliminated; replace the ribbon cable first, then the camera
  module, then the board.** The IMX708 enumerates and libcamera opens, configures and starts
  it, then every capture times out with `Camera frontend has timed out`.
  - Check state: `rpicam-hello --list-cameras` enumerates the sensor; `./run.sh camera`
    fails all three attempts in 8s each and disables the camera.
  - The node needs no change when working hardware arrives.
  - Blocks: vision-guided obstacle avoidance, the autonomous observation loop, and the
    responsiveness comparison that MASTERPLAN makes the definition of success.
  - **Do not re-investigate in software.** The 2026-08-16 CHANGELOG entry records the full
    elimination, including the decisive test: the sensor's own internal colour-bar generator,
    which synthesises data on-chip with the lens and exposure path irrelevant, also delivers
    zero bytes. Re-running libcamera, swapping drivers or editing `config.txt` cannot fix
    this and the evidence for that is already recorded.

- [TODO 2026-08-16] **Copy `.env` into this project.** The fork carried `src/`, `config/` and
  the calibration but not the secrets, so `OPENAI_API_KEY` and `PICOVOICE_ACCESS_KEY` are
  missing and the full graph cannot run. Sensor, motion, turn and stance graphs are unaffected.
  `cp /opt/pibot-hexapod/.env /opt/pibot-dora/.env && chmod 600 /opt/pibot-dora/.env`
  (RUNBOOKS §8).

- [TODO 2026-08-16] **Verify the full autonomous graph end to end.** Four of the eight nodes
  have never run against real hardware: `audio`, `llm`, `brain` and `buzzer`. The wake word,
  Whisper, GPT-4o, tool dispatch, TTS and barge-in path is entirely unproven. `buzzer` is the
  easy one to miss — it appears only in `dataflow.yml` and in no single-purpose test graph, so
  unlike the others it is not covered even indirectly. Note also that every test graph
  substitutes a test node for the brain, so no graph has yet exercised `brain` talking to
  anything, and the behaviour state machine that replaced the `while True` loop in
  `src/main.py` has never executed. Needs `.env`, a charged pack and accepts API spend.

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
