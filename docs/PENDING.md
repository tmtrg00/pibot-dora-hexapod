# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [IN-FLIGHT 2026-08-20] **Approach stops short of its target; instrumented, now needs
  tape-measured runs.** One hardware run stopped 8.3cm short of a 25cm target (settled ~31.8cm),
  worse than the 4.7cm the travel-per-cycle averaging change was meant to fix. Retreat was
  tighter — 2.5cm short of a 50cm target. The approach stop is now instrumented (CHANGELOG
  2026-08-20): each stop logs an `approach diagnostic:` line giving the decision distance, the
  predicted in-flight-cycle landing, and the settled distance, so the final cycle's real travel
  is measured against the predicted `lead_cm`. The instrumentation is verified to run and not
  disturb motion, but has not produced values yet — that needs `./run.sh approach` on hardware.

  Get this before changing the lead calculation: (1) a few runs with a **tape measure** against
  the physical robot, since all distances so far are the ultrasonic grading its own homework and
  the sensor could read long or short; (2) the `approach diagnostic:` numbers from those runs.
  Working hypothesis to confirm or refute (do not act on it yet): the final cycle is a
  decelerating, stopping cycle that covers far less than a full-stride cruising cycle, so the
  whole-approach average over-predicts it as the lead and stops the robot early — by hand on the
  one existing run, predicted lead 12.8cm vs ~1.5cm actually travelled after the decision. Only
  the forward approach is affected; the retreat leg lands within a couple of cm.

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
