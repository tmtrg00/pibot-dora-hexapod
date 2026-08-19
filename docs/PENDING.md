# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [IN-FLIGHT 2026-08-19] **Hardware-verify the movement programme.** Items 1 and 2 are
  verified on the robot (CHANGELOG 2026-08-19). Everything since — continuous turning, the
  ultrasonic approach, ramped stances, the eased gait trajectory, servo write caching and
  frame scheduling, and the roll/pitch axis fix — is implemented and smoke-tested offline
  only, against simulated hardware. The owner is running the graphs manually. To verify:
  `./run.sh smoothturn`, `./run.sh approach` (needs an obstacle about 1-1.5m ahead),
  `./run.sh attitude`, `./run.sh idlereset`, and re-run `./run.sh straightwalk` and
  `./run.sh odometry` since both exercise the changed gait. The attitude and turn graphs are
  the two whose verdict only a person watching can give: roll must tip the robot side to side
  and pitch nose up and down, and each turn must be one continuous rotation.

  First hardware pass done 2026-08-19: the turn, attitude and approach graphs all behaved, and
  the turn's hunting was found and fixed from that run (CHANGELOG). Still to confirm on the
  robot: the turn fix itself (`./run.sh smoothturn` now runs all 8 turns instead of aborting at
  the first), and `./run.sh idlereset`, which the owner ran but could not tell what it did —
  use `PIBOT_IDLE_STANCE_RESET_S=8 PIBOT_IDLE_WATCH_S=25` and watch for the robot standing back
  up on its own about 8s into step 1b.

- [TODO 2026-08-19] **A servo lead pulled out during testing; confirm the legs are all sound.**
  Happened while running the movement graphs on 2026-08-19. The I2C bus was checked immediately
  afterwards and all four devices answer (0x40, 0x41, 0x48, 0x68), so the driver boards are
  fine and the fault is mechanical at the connector — plus, possibly, a servo horn that slipped
  on its spline, which no amount of software can correct. `test/servo_recover.py` walks each of
  the eighteen leg joints one at a time to find a joint that does not move, and finishes at the
  reference pose where every leg should mirror its opposite number. Until that has been run and
  come back clean, treat any odd movement as a mechanical fault before suspecting the gait
  changes made the same day.

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
