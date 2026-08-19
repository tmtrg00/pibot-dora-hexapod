# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [IN-FLIGHT 2026-08-19] **Movement improvement programme, items 3-7.** Items 1 (gyro
  heading-hold walking) and 2 (counted gait cycles) have shipped and are verified on
  hardware — CHANGELOG 2026-08-19. The remaining five, in the order agreed: (3) closed-loop approach on the ultrasonic
  ("walk forward until 20cm from the obstacle"); (4) ramp stance transitions instead of
  jumping height and footprint in one command; (5) smooth the swing-leg trajectory in
  `run_gait`, which currently assigns full 40mm lift in a single 10ms frame; (6) cache servo
  writes and schedule gait frames on absolute time; (7) fix the two balance-path bugs (roll
  and pitch share one PID instance; `update_imu_state` returns pitch-first but
  `imu6050` unpacks roll-first). Items 5-7 touch `src/`, which MASTERPLAN puts upstream of
  this project — the owner approved that scope explicitly on 2026-08-19.

- [TODO 2026-08-19] **Turning is visibly fragmented — make `turn_to` continuous.** Owner
  observation after the item-1 run: forward and backward walking is noticeably smoother, but
  turns still stutter. The cause is structural rather than a tuning problem. `turn_to` queues
  each gait cycle **single-shot**, sleeps 0.4s to let the body settle, measures the yaw delta
  and only then plans the next cycle, so the robot turns in visible discrete lurches. It
  cannot simply be made continuous the way heading-hold walking was: a turn has `x=0, y=0`,
  which is exactly the branch in `Control.condition_monitor` that clears the command queue
  after one `run_gait` call, whereas a non-zero stride leaves it queued and re-enters the
  gait. Two candidate fixes, neither yet chosen: make `condition_monitor` treat a non-zero
  `angle` as continuous the same way it treats a non-zero stride (a `src/control.py` change,
  and the honest one), or give the turn a 1mm stride so it takes the continuous branch (node
  side only, no upstream change, but it smuggles a small translation into what is supposed to
  be a rotation in place). Not covered by items 2-7: item 5 smooths the motion *within* a
  cycle, which will help, but the pauses *between* cycles are this separate problem.

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
