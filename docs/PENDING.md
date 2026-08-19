# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

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
