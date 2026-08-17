# CHANGELOG — PiBot-Dora

Append-only. Newest entry at the top. Bugs and decisions are history and live here; open work
lives in PENDING; procedures live in RUNBOOKS. Superseding a decision takes a new dated entry
whose `**Decision:**` line says so and references the prior date — never edit or silently
revert an old entry.

---

## 2026-08-17 — Two different sensors on both CSI ports both time out, ruling out sensor and cable

Tested a second camera fitted alongside the IMX708: an OV5647 on CAM0 (`i2c@88000`, CFE
`/dev/media0`, capture node `/dev/video4`) and the existing IMX708 on CAM1 (`i2c@80000`, CFE
`/dev/media1`, capture node `/dev/video12`). Both enumerate correctly in
`rpicam-hello --list-cameras` with their full mode lists, and libcamera opens, configures and
starts each one and selects a sensor format without complaint — `1296x972-SGBRG10_1X10` for the
OV5647 and `2304x1296-SBGGR10_1X10` for the IMX708. Neither ever delivers a buffer. Both fail
identically one second in with `Dequeue timer of 1000000.00us has expired!` followed by
`Camera frontend has timed out!`, and no JPEG is written. Reproduced through `rpicam-still` and
again through `picamera2` on the project's own venv, where `capture_file()` does not merely
fail but blocks past a 40s deadline and had to be killed. No orphaned processes were left
behind afterwards.

**Fix:** none — this is a diagnosis, not a repair. Nothing in the tree changed.

The value of the result is what it eliminates. The previous conclusion rested on a single
sensor on a single port, which left a bad IMX708, a bad ribbon cable and a bad port all
equally consistent with the evidence. Two different sensor models, on two different ports,
through two independent CFE instances, failing at the same point in the same way, rules out
all three: a defect that follows neither the sensor nor the cable nor the port is in what they
share, which is the RP1 CSI-2 receive path.

**Decision:** the pinned PENDING item stays pinned but its wording is narrowed from "the board
needs replacing" to naming the shared receive path, because this evidence cannot separate the
RP1 silicon from the Ubuntu 25.10 kernel and libcamera stack driving it — `6.17.0-1021-raspi`
with `libcamera 0.5.0-1ubuntu4` and `rpicam-apps 1.7.0-1ubuntu3`, which is not the Raspberry Pi
OS combination this hardware is normally validated against. A common-mode software fault would
present exactly as observed. Booting Raspberry Pi OS from a spare card and attempting one
capture is a cheap test that discriminates the two and should be run before any board is
bought; it is recorded in PENDING. This supersedes nothing in the 2026-08-16 entry — the
symptom and the "not fixable in this project" conclusion are unchanged.

---

## 2026-08-16 — Forked to a fully standalone project with its own documentation structure

Copied `src/`, `config/`, `test/`, `point.txt`, `params.json` and `requirements.txt` out of
`/opt/pibot-hexapod`, installed the runtime dependencies into this project's own venv, and cut
the last link to the original. `nodes/common.py::bootstrap()` now derives the project root from
its own file location instead of pointing at the upstream checkout, so the project can be moved
or cloned without editing anything.

**Decision:** this project is now fully independent, superseding the shared-source arrangement
chosen earlier the same day. The original decision was that reusing `/opt/pibot-hexapod/src`
kept the drivers and the servo calibration single-sourced, with no second copy to drift. That
is still true and is the real cost being paid here: `point.txt` and the drivers are now copies,
and a fix on either side will not reach the other. The independence was chosen deliberately in
exchange — the experiment can now be evaluated, moved or discarded without touching the
original at all, and the original can be deleted without breaking this. RUNBOOKS §9 documents
how to compare and re-sync the forked files.

**Fix:** the first dependency install silently did nothing. The venv had a `.pth` file adding
the upstream venv's `site-packages`, so pip found every requirement already importable and
skipped all of them, reporting success. The failure only appeared when the `.pth` was removed
and `openai`, `pvporcupine`, `webrtcvad`, `dotenv`, `rpi_ws281x`, `smbus2` and `audioop` all
vanished at once. Reinstalled with the link already gone.

Verified standalone: with the upstream venv link removed, no module resolves to
`/opt/pibot-hexapod`, all robot drivers import from `/opt/pibot-dora/src/`, and the sensors
graph runs on hardware — four processes, battery telemetry at 5.82V/7.59V over I2C, LED strip
responding, HC-SR04 initialised, no leaked processes. `picamera2` and `libcamera` resolve to
the system packages, which is independent of the original either way.

Added the three-document structure: `AGENTS.md` (with `CLAUDE.md` and `GEMINI.md` symlinks),
`docs/MASTERPLAN.md`, `docs/PENDING.md`, this file and `docs/RUNBOOKS.md`, carrying over the
session ritual, the three-document rule and the writing style, with pre-flight checks rewritten
around the scars this project actually earned.

**Not carried over:** `.env`. Secrets were left for the owner to copy deliberately, so the full
graph cannot run until they do — see PENDING.

---

## 2026-08-16 — Added named stances with offline leg-reach validation

Added a `set_stance` tool with eight poses combining ride height, foot spread and tilt:
`neutral`, `crouch`, `tall`, `wide`, `narrow`, `brace`, `alert`, `lean_forward`. Height and
tilt were already reachable piecemeal through `set_position` and `set_attitude`, but foot
spread was not exposed at all and there was no concept of a named pose. Spread is the
significant one: `run_gait` deep-copies `Control.body_points` as the base for every step, so
widening the footprint widens the walking gait too, not just the standing pose.

**Decision:** the stance work lives in this project and `src/actions.py` is left unmodified.
The tool schema is appended to the upstream `TOOLS` list at runtime in the `llm` node, and the
`hardware` node serves `set_stance` itself rather than routing through the upstream dispatcher,
because it manipulates `body_points` directly. Whether this belongs upstream instead is open —
see PENDING.

**Fix:** an out-of-range stance is a silent no-op. `Control.set_leg_angles()` calls
`check_point_validity()` first and, if any leg would reach outside 90–248 mm, prints a line and
returns without moving anything — no exception, no return value. So `nodes/stances.py` mirrors
the reach maths and validates every pose offline, `verify_against()` cross-checks that mirror
against a live `Control` so it cannot drift unnoticed, and `apply_stance()` confirms the
robot's own validity check after moving and reverts the footprint if nothing happened.

Found while reading `calculate_posture_balance`: **spread and tilt cannot be combined.**
Attitude commands rebuild foot positions from a hardcoded footprint rather than from
`body_points`, so applying a tilt silently discards the spread and would leave the robot tilted
at stock width while believing it was wide. `validate()` now rejects that combination outright,
which is why no shipped stance has both.

All eight stances reach 124–174 mm against a 98–240 mm usable window, holding an 8 mm margin
off the hard limits for calibration error and servo slop. Verified on hardware: `wide` applied
and returned to neutral correctly.

**Fix:** `stance_test_node.py` defined `main()` but never called it, so the process started,
defined a function and exited 0 without connecting to dora. The daemon reported it as finishing
*successfully* and then failed the `hardware` node with a cascading error, which reads like a
dora or hardware fault rather than two missing lines. Checked every other node for the same
omission; this was the only one.

---

## 2026-08-16 — Bounded camera captures with a deadline instead of blocking forever

Testing the camera through dora found a defect in the camera node, not only in the hardware.
Both failures are properties of the upstream driver and would bite equally on a healthy board
with a marginal cable.

**Fix:** `capture()` can block indefinitely, sitting in C waiting on a frame that never
arrives, so no Python-level care inside the call helps. Captures now run on a worker thread
with a deadline and the node answers `ok: false` when it passes. The stuck thread cannot be
killed from Python, so it is abandoned and the camera is marked dead after three consecutive
timeouts, bounding the damage to a few leaked threads rather than one per request forever.

**Fix:** `initialize()` reports success on a camera that cannot deliver a frame — libcamera
opens, configures and starts the sensor happily, and the frontend timeout only surfaces later
and asynchronously on the first dequeue. The node no longer treats a successful open as a
health signal.

Verified on hardware. Before: three capture requests each hung past a 45s budget with no reply,
wedging the node. After: each failed in 8.0s with a reason, and the camera disabled itself
after the third. The board fault is unchanged — the sensor still enumerates as an IMX708 and
libcamera still logs `Camera frontend has timed out` — but a dead camera can no longer stall
the autonomy loop. In the full graph the old behaviour was worse than a stalled capture: since
`initialize()` reported success, the brain would have kept scheduling observations every 60
seconds, wedging the node each time, forever.

---

## 2026-08-16 — Calibrated the turn rate at about 7.8° per gait cycle

Measured on hardware: 23 gait cycles of `walk(turn_right)` at speed 6 produced roughly 180° of
observed body rotation. The `angle` argument the gait engine receives — 8 for a `turn_*`
direction — therefore maps about 1:1 to degrees of rotation per cycle. Reading `run_gait`
alone this was ambiguous by a factor of two, because the stance phase accumulates twice the
per-step leg displacement; the measurement settles it, and the doubling does not reach body
rotation. A full 360° needs about 47 cycles, not the 23 a 16°/cycle estimate implied.

Added `dataflow-turn.yml` and `turn_node.py`, which segment the rotation into short bursts with
a battery reading between each and abort to a stand-and-relax tail rather than pressing on into
a brownout mid-stride.

Battery under sustained gait load, which is the useful number this produced: the 23-cycle run
sagged to 5.00V against a 4.90V abort threshold, recovering to 5.53V between segments and 5.94V
at rest afterwards, down from 6.94V at rest beforehand.

---

## 2026-08-16 — Verified motion end to end through the dora graph

Ran the pose sequence with the floor lowered to 5.0V at the owner's explicit instruction, the
pack having measured 5.88–6.00V under load against the standing 6.0V floor. All ten steps
executed with none refused, and the movement was confirmed visually rather than inferred from
the absence of an error: stand, roll and pitch attitude changes, head pan/tilt, and relax.

This closed the last unverified link in the port. A tool call demonstrably leaves one process,
crosses into the `hardware` node, runs the inverse kinematics and gait engine, moves the
servos, and returns a result the caller reports — so the reused drivers behave identically
under the process split.

The pack sagged to 5.06V during `Control()` construction, which drives all six legs to the
standing pose at once. It held without browning out, so the 6.0V floor is conservative for
posing, but sustained walking current is a different load again.

---

## 2026-08-16 — Ported PiBot-Hexapod to a dora-rs dataflow graph

Split the single ~5,900-line Python process into eight dora nodes that share nothing and
communicate only by message: `brain`, `audio`, `llm`, `hardware`, `camera`, `led`,
`ultrasonic`, `buzzer`. The `while True` loop in `src/main.py` became an explicit state machine
in `brain_node.py`; the drivers were reused unchanged.

**Decision:** node boundaries follow which device tolerates sharing, not tidiness. Servos, IMU
and battery ADC stay fused in one `hardware` node because they share one I2C bus that the GIL
used to serialise for free, and these drivers issue multi-step write-then-read transactions
that interleave badly. Mic and speaker stay fused in one `audio` node because the mic is
exclusive and splitting it would mean arbitrating an exclusive device over IPC for no gain.
The `brain` owns no hardware at all, so it can never block on a servo, a socket or a
microphone. This is the honest limit of what the split buys on this hardware: the sensor layer
is one wire and cannot be parallelised.

**Decision:** the battery pre-flight rule is enforced in code rather than by convention. The
`hardware` node owns the ADC, so it reads the pack before constructing `Control()` — which
drives the legs to the standing pose merely by being instantiated — and refuses below 6.0V, as
it refuses every motion tool on a stale or low reading, or when the gait thread has died.
Relaxing servos is always allowed since it reduces current draw.

**Fix:** dora ignores the interpreter named in a node's `path` and spawns `.py` nodes with the
system python, missing the venv entirely; setting `PATH` or `VIRTUAL_ENV` does not help.
`bin/py` is named as each node's `path` so dora execs it as a plain binary, and it execs the
venv interpreter in turn.

**Fix:** a killed dora CLI orphans its node processes, which keep holding the I2C bus,
gpiochip0, the mic and energised servos, so the next run fails with `GPIO busy` —
indistinguishable from a hardware fault. `run.sh` cleans up on entry and exit; `stop.sh` does
it on demand.

Verified: dora 0.5.0 spawns nodes in the venv and delivers messages between them; battery
telemetry crosses a process boundary over I2C; the LED strip responds to `emotion` messages
from another process; all 13 LLM tools route to exactly one owning node; the full 8-node graph
is accepted and every edge resolves. Renamed inputs deliver correctly at runtime even though
dora's graph renderer omits them.
