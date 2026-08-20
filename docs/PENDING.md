# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [PINNED 2026-08-20] **Charge the SERVO battery pack — the charger likely went on the Pi's
  pack.** The robot has two batteries: the ADC load channel tracks the servo pack (ran down to
  ~3.1V over the day's testing, servos whine and barely move), the pi channel the Pi's pack
  (held 7.4V all day, did not need charging). Nothing that moves can run or be verified until
  the servo pack reads ~7V or better on the load channel. (CHANGELOG 2026-08-20.)

- [TODO 2026-08-20] **Verify the calibrated, lifelike head on hardware.** Blocked on the
  servo-pack charge above. `test/test_head.py` should show the head sitting truly level
  (servo 120 via `data/head_trim.json`), sweeping with the eased profile, and looking UP for
  the first time on tilt +20. Then re-run the aim survey `test/test_head_aim.py` with a wall
  or box 0.5-1.5m ahead — it returned 0-1 echoes at every tilt when nothing was in range —
  and finally `./run.sh approach` end to end, whose head-levelling and torque-hold log lines
  are already verified but whose accuracy with the calibrated aim is not.

- [TODO 2026-08-16] **Verify the full autonomous graph end to end.** Four of the eight nodes
  have never run against real hardware: `audio`, `llm`, `brain` and `buzzer`. The wake word,
  Whisper, GPT-4o, tool dispatch, TTS and barge-in path is entirely unproven. `buzzer` is the
  easy one to miss — it appears only in `dataflow.yml` and in no single-purpose test graph, so
  unlike the others it is not covered even indirectly. Note also that every test graph
  substitutes a test node for the brain, so no graph has yet exercised `brain` talking to
  anything, and the behaviour state machine that replaced the `while True` loop in
  `src/main.py` has never executed. `.env` is now in place and the pack has been charged
  (CHANGELOG 2026-08-18); this still needs a charged pack at run time and accepts API spend.

## Pinned for later

Deliberately deferred. Review when Active clears.

- [SHELVED 2026-08-19] **Measure the parallelism claim.** The central argument for the port is
  that audio, vision and the gait loop stop competing for one GIL. Nothing has measured it.
  Needs a working camera to be a fair test — blocked on the new camera (CHANGELOG 2026-08-19).

- [SHELVED 2026-08-19] **Stream camera frames as Arrow buffers instead of file paths.** Captures
  are written to JPEG and passed by path, which wastes dora's zero-copy shared memory. This is
  where the architecture would start paying for itself, and it is the prerequisite for a local
  detector node. Blocked on the new camera (CHANGELOG 2026-08-19).

- [TODO 2026-08-16] **Restructure the brain as a behaviour tree (`py_trees`).** Carried over
  from upstream. The brain owns no hardware, so its logic can be replaced without touching any
  other node — this architecture makes it a contained change.
