# CHANGELOG — PiBot-Dora

Append-only. Newest entry at the top. Bugs and decisions are history and live here; open work
lives in PENDING; procedures live in RUNBOOKS. Superseding a decision takes a new dated entry
whose `**Decision:**` line says so and references the prior date — never edit or silently
revert an old entry.

---

## 2026-08-16 — Eliminated software as the cause of the camera failure, down to the sensor's own test pattern

Investigated the camera fault outside the dora graph entirely, on the explicit premise that it
was a software problem to be fixed in software. It is not. The premise was tested to
destruction and the elimination is recorded here so nobody spends another session on it.

What works, and it is a lot: `rpicam-hello --list-cameras` enumerates the IMX708 with all
three modes; the kernel reads `camera module ID 0x0301` over I²C; the `rp1-cfe` driver binds,
registers eight video nodes and finds the subdevice; the media graph is complete and correct
(`imx708 → csi2 → pisp-fe → fe_image0`, every link `ENABLED`, formats propagating as
`SBGGR10_1X10/2304x1296`); and with dynamic debug enabled the driver is seen to configure the
CSI-2 block for 2 data lanes at 900 Mbps, then log `Starting sensor streaming`, which returns
successfully in 124 ms with no I²C error. One second later the dequeue timer expires.

**Fix:** none available. Every layer that software controls was eliminated by test, not by
argument:

- Not the ISP. Rerouting the media graph to bypass the PiSP front-end entirely and capturing
  raw from `rp1-cfe-csi2_ch0` with `v4l2-ctl` produced a zero-byte file.
- Not the sensor mode. All three modes — 1536x864, 2304x1296, 4608x2592 — fail identically.
- Not driver state. Unloading and re-probing `rp1_cfe_downstream`, `imx708` and `dw9807_vcm`
  re-registered everything cleanly and changed nothing.
- Not permissions. The account is in `video` (gid 44) with verified read/write on every
  `/dev/video*` and `/dev/media*` node, and the failure occurs at buffer dequeue, long after
  the `open()` a permission fault would have blocked.
- Not the imaging path. **The decisive test:** the IMX708's internal colour-bar generator
  (`test_pattern=1` on the sensor subdev) synthesises data on-chip and transmits it over
  CSI-2 with the lens, exposure and gain path irrelevant. It also delivered zero bytes.

The D-PHY reports no CRC, ECC, lane or overflow errors at any point — not corrupt data, but
total silence. A marginal cable usually produces errors; silence means no valid high-speed
transitions are reaching the receiver at all.

**Decision:** the camera fault is hardware and this supersedes the framing in the previous
PENDING entry, which asserted a defective RP1 CSI-2 receive path and a board replacement. That
conclusion was stronger than its evidence. What is proven is narrower and more useful: the
control plane (I²C) works end to end and the data plane (the CSI-2 differential pairs) carries
nothing. Those share one ribbon cable but different conductors, and the high-speed pairs are
far more sensitive to a partially-seated or flexed connector — a real risk here, since the
camera rides a pan/tilt head that flexes the cable in service. Replacement order is therefore
**cable, then camera module, then board**, cheapest and likeliest first. The test-pattern
result narrows it to the sensor's MIPI transmitter, the cable, or the RP1 receiver, and cannot
distinguish between those three from software.

One software avenue remains untested and was deliberately not taken: kernel 6.17.0-1003-raspi
is still installed and staged in `/boot/firmware/old/` as `vmlinuz.bak-1003`, so a regression
in Ubuntu's 6.17.0-1021 `rp1-cfe` backport could in principle be ruled out by booting it. That
requires editing `config.txt` and rebooting a headless robot, which risks an unbootable machine
needing physical card access, and it must not be done with the servo rail energised. Given the
test-pattern result, the expected value is low. It is recorded as an option, not a plan.

**Noted in passing:** Ubuntu ships two CFE drivers — `rp1-cfe-downstream.ko` (bound here, via
DT compatible `raspberrypi,rp1-cfe`) and the upstream `rp1-cfe.ko` (compatible
`raspberrypi,rp1-cfe-upstream`). Switching would need a DT overlay, and upstream's multi-stream
handling is less complete, so it is not a fix. The downstream driver does emit a kernel WARNING
at `v4l2-subdev.c:462` in `call_s_stream` on the stop path when a capture is killed; that is
stream-state bookkeeping noise on teardown, not the cause.

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
