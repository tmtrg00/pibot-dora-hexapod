# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [TODO 2026-08-19] **`turn_to` overshoots its target by about 4deg, every time.** Measured over
  two hardware runs, both directions, every turn size: +90 asked gave 93.1 / 94.7 / 93.7 / 95.8,
  +/-180 gave 184.8 and 185.3, +/-20 gave 23.7 and 25.2. Turning itself is smooth and the
  hunting is gone — this is accuracy, not behaviour, and it is not a blocker. The cause is not
  established: the robot rotates further after the stop decision than the cycle in flight should
  deliver, which could be the per-angle-unit estimate reading low, the body settling once the
  gait stops, or the gyro integrating through the set-down. Get that data before changing
  anything — the last two changes to this loop were made on inference and one was wrong
  (CHANGELOG 2026-08-19, the correction entry).

  Three options, in increasing cost: revert `plan()` from rounding to truncating, which restores
  the better 5.0deg worst case for a one-line change; add a deliberate stop margin of about 4deg,
  which should centre it but is a constant fitted to two runs; or plan the endgame in smaller
  cycles so the final one carries less rotation and its prediction error is proportionally
  smaller, which costs two or three extra cycles per turn.

- [TODO 2026-08-19] **Re-check approach accuracy after the travel-per-cycle change.** The
  approach was changed to average its travel-per-cycle over the whole approach rather than a
  two-cycle window, after the first run stopped 4.7cm over target while reporting 8.9cm per cycle
  where the true figure was about 6.2. That change is simulated only — `./run.sh approach` has
  not run since. It should stop within a couple of cm of target, measured with a tape.

- [SHELVED 2026-08-19] **The approach's retreat path has never run on hardware.** Both attempts
  ended with the sensor reading past the obstacle ("already 136.6cm away, needed no movement"),
  which is the correct response but exercises nothing. Needs a run that ends close enough to an
  obstacle for the backward leg to have somewhere to go.

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
