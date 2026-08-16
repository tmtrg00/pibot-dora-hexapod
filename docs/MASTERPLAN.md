# MASTERPLAN — PiBot-Dora

Read-only. Edit only on a fundamental root-level change, and only after explicit human
approval. Everything else belongs in PENDING (open work), CHANGELOG (history) or RUNBOOKS
(procedures).

## Mission

Run the PiBot-Hexapod robot as a **dora-rs dataflow graph** — a set of small, single-purpose
processes that share no memory and communicate only by message — and find out whether that
architecture makes the robot more capable, more debuggable and safer than the single-process
version it was forked from.

This is an experiment with a verdict to reach, not a rewrite to finish. It is allowed to fail.
If it does, `/opt/pibot-hexapod` is untouched and still works.

## Why this exists

The original is one Python process of ~5,900 lines with threads added wherever concurrency was
needed: a wake-word thread, the gait thread, per-command movement threads, an LED animation
thread. Three problems follow from that shape, and they are the problems this project exists to
attack:

1. **Silent failure.** A daemon thread that dies takes its subsystem with it and reports
   nothing. The `condition_monitor` thread swallowed the NumPy 2.0 `np.mat` breakage and every
   attitude and balance command stopped working, surfacing weeks later as a timeout.
2. **Contention.** Whisper uploads, TTS playback, camera capture and the gait loop all compete
   for one GIL on a four-core machine.
3. **Coupled failure.** A blocking call anywhere — a camera that never returns a frame, a
   network hang — stalls everything, including the legs.

## Scope

**In scope**

- The dataflow graph: node boundaries, message contracts, the behaviour state machine.
- Safety and observability that the architecture makes possible: the battery gate enforced in
  code, capture deadlines, per-node health reporting, verified movement.
- Movement capability built on the existing IK and gait engine — named stances, gait
  composition, closed-loop turning.
- Single-purpose test graphs. These are the test suite; there is no other one.

**Out of scope**

- Rewriting the inverse kinematics, the gait generator or the sensor drivers. They work. They
  are reused as-is, and improving them is worth doing *upstream* where both projects benefit.
- ROS 2. dora's ROS 2 bridge exists; using it would import the complexity this avoids.
- Distributed operation across machines. dora supports it; one Pi is the target.
- Replacing `/opt/pibot-hexapod`. That decision comes after a verdict, not before.

## Architecture commitments

These are the load-bearing decisions. Changing one is a MASTERPLAN-level change.

1. **The brain owns no hardware.** It decides; everything physical happens by message. It can
   therefore never block on a servo, a socket or a microphone.
2. **One owner per device.** Every piece of hardware is owned by exactly one node. Devices that
   cannot tolerate concurrent access are fused into one node rather than split for tidiness —
   which is why servos, IMU and battery ADC share the `hardware` node (one I2C bus) and why the
   mic and speaker share the `audio` node (one exclusive device).
3. **Every wait has a deadline.** A node that dies must not leave a peer waiting forever. A
   tool that never answers is recorded as a timeout and reported, never silently skipped.
4. **Safety is enforced in code, not by convention.** The battery floor, capture deadlines and
   leg-reach validation are checks the software performs, not rules a human remembers.
5. **Messages are debuggable.** Arrow arrays carrying one JSON string. Slower than a binary
   schema and worth it at these rates; revisit only for camera frames.

## Relationship to PiBot-Hexapod

Forked 2026-08-16. This project is now **fully standalone**: `src/`, `config/`, `point.txt` and
`params.json` live here, and it runs with `/opt/pibot-hexapod` deleted.

The cost of that independence is duplication: the drivers and the servo calibration are copies
and will drift. That is accepted deliberately — see the CHANGELOG entry for 2026-08-16 — and
RUNBOOKS §9 documents how to compare and re-sync them.

**`/opt/pibot-hexapod` is never modified by this project.** It is the fallback.

## Definition of success

The experiment succeeds if, with a charged battery and working camera, the robot is
**noticeably more responsive with vision and voice running together** than the original, and if
the classes of silent failure listed above are demonstrably gone.

It fails if the added complexity — eight log streams, cross-process debugging, orphaned
processes — costs more than the parallelism and observability are worth. That is a legitimate
outcome and should be recorded as a decision, not avoided.
