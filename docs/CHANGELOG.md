# CHANGELOG — PiBot-Dora

Append-only. Newest entry at the top. Bugs and decisions are history and live here; open work
lives in PENDING; procedures live in RUNBOOKS. Superseding a decision takes a new dated entry
whose `**Decision:**` line says so and references the prior date — never edit or silently
revert an old entry.

---

## 2026-08-20 — Servo pack recharged and the calibrated head verified on hardware: level is level, up is up, and the ultrasonic sees what it aims at

The charger found the right battery this time: the load channel read 7.29V with servo power
on (up from 3.12V last night), the pi channel 8.06V. That cleared the pinned blocker and
unblocked the head verification, which passed on all counts.

**The watchable sweep** (`test/test_head.py`): the owner confirmed by eye that commanded
level looks level (the +30 trim from `data/head_trim.json` doing its job), that tilt +20
genuinely looks UP — the first time this head has looked above the horizon — that every move
reads as a smooth eased gesture at the default 80deg/s rather than a snap, and that the final
torque release lets the head go limp without a twitch.

**The aim survey** (`test/test_head_aim.py`): with no target it returned 0-1 echoes at every
tilt, same as yesterday — confirming those empty runs meant "nothing in range", not a fault.
With a box ~88cm ahead it returned 20/20 echoes at every tilt from -20 to +15deg, medians flat
at 87.4-88.6cm, degrading only at +20 (14/20, 90.5cm) where the beam starts clearing the top
of the box. **Decision:** the script's closing suggestion to apply a further -10deg trim is
rejected as noise — it picked a 2mm median difference between tilts with identical echo
counts; the flat distance profile across ±15deg is exactly what a correctly-levelled head
with the HC-SR04's wide beam should produce. The trim stays at +30 as calibrated yesterday.

Still owed from the original task: `./run.sh approach` end to end with the calibrated aim —
its levelling and torque-hold logging is verified but its stopping accuracy is not. That
remains in PENDING as its own item.

Plain-language summary: the motor battery is charged again, and the head fixes from
yesterday all check out on the real robot. The head now holds truly level, can look up for
the first time, and moves smoothly like a living thing rather than snapping between poses.
The distance sensor mounted on it measures a box in front accurately no matter which way the
head is tilted, which proves the aim correction is right. The one remaining check is letting
the robot walk up to an obstacle and stop at the right distance using the newly-corrected
sensor.

## 2026-08-20 — Head tilt calibrated by eye (level is servo +30, up is up), and the "collapsing battery" was real: the servo pack drained while the charger sat on the wrong one

Two results from the evening session, one good and one humbling.

**The calibration.** The owner watched `test/test_head_tilt_cal.py` sweep raw servo tilt from
-45 to +45 in 15deg steps and reported: position 6 (raw +30) looked level, counting up moved
the head up, and the first positions pressed against a mechanical down-stop near raw -30. That
is written to `data/head_trim.json` as sign +1, trim +30deg — so commanded tilt 0 now maps to
servo 120, physical level. From level the head can look down about 45deg and up at least
15deg. Every caller goes through this mapping (`set_head`, the `move_head` tool, startup and
approach levelling, the idle re-level), so "level" finally means level for the head-mounted
ultrasonic too. NOT yet verified: the confirmation run barely moved — see below — so watching
the calibrated head sit level and look up is still owed.

**The battery.** The ADC's load channel fell 6.65 → 5.41 → 4.47 → 3.71 → 3.12V across the
afternoon while the pi channel held 7.4V throughout. Working theory of the day was a broken
voltage-sense line ("the gate refuses on a phantom reading"), because the head demonstrably
moved at a reading of 3.24V. That theory was wrong, and the movement quality said so: strong
at 6.9-7.2V (morning motion graph), weak but visible at 3.24V (micro servos, head only), a
whine and barely a twitch at 3.12V. The load channel tracks the SERVO pack; the pi channel
tracks the Pi's pack — they are separate batteries, the robot ran the servo pack down over
the session's runs, and the recharge attempt almost certainly went onto the (already full) Pi
pack. **Decision:** the load-channel reading is trusted again; the "phantom sense line"
hypothesis from mid-session is retracted. Bypassing the gate (floor lowered, then 0) was done
at explicit owner instruction for head-only loads and is what produced the diagnostic voltage
trail — but the last two runs at ~3.2V drove servos that could only whine, which is exactly
what the gate exists to prevent. Charge the SERVO pack before any further motion.

Plain-language summary: the robot's head was aiming 30 degrees below level whenever the code
asked for "straight ahead", because the servo was assembled with its centre pointing down. The
owner watched a sweep and told us which position looked level, and that correction is now
saved — the head will hold truly level and can genuinely look up for the first time. Separately,
the battery warnings all afternoon were real: the robot has two batteries, the one that powers
the motors ran flat during testing, and the charger was likely connected to the other one,
which was already full. The motor battery needs charging before the robot moves again.

## 2026-08-20 — The head ramp made lifelike: eased S-curve motion at a speed, replacing the fixed six-step ramp the owner saw snap

The owner watched `test/test_head.py` and reported the pan sweeps "very quick, not really
smooth" and wanted the movement "more living being like". The six-step ramp shipped earlier
today finished any move in ~120ms regardless of distance — fast enough to read as a snap.
`set_head()` now takes time proportional to travel at `PIBOT_HEAD_SPEED_DEG_S` (default
80deg/s, so a 40deg glance lasts ~0.5s and a nudge stays quick but never under a 0.15s floor)
and follows a smoothstep S-curve — accelerate, glide, settle — which is what makes motion read
as a gesture rather than an actuation. `PIBOT_HEAD_RAMP_STEPS` is gone; speed 0 restores the
old single-write jump. Verified offline: a 40deg sweep runs 482ms over 25 frames, monotonic,
edge frames moving 1deg where mid-flight moves 5deg. The same run confirmed on hardware that
both head servos move (pan sweeps, tilt nods — tilt's ±20deg is just visually small) and that
the aim survey saw 0-1 echoes at every tilt, i.e. no target was in range rather than a wrong
trim; re-run pending with a wall or box 0.5-1.5m ahead. Battery read 6.12V under light load —
near the 6.0V floor, charge before more motion work.

Plain-language summary: the robot's head used to flick to each position almost instantly,
which looked robotic. Now it turns the way an animal glances at something — starting gently,
sweeping, and settling — taking about half a second for a big turn. The sensor-aiming survey
from earlier came back empty because nothing was in front of the robot to see, so that check
still needs a box or wall placed ahead of it.

## 2026-08-20 — Head movement made deliberate: ramped moves, code-enforced levelling, and torque held while the approach aims through it

The head (camera pan/tilt on PCA9685 0x41 channels 0/1) carries the ultrasonic sensor, so head
tilt IS sensor aim — and a hand-tilted head is what faked the 8.3cm approach overshoot resolved
earlier today. Until now the head's pose was never a state the code owned: `move_head` slammed
the servos to the target in one full-speed write, torque auto-released 0.4s later, and nothing
ever commanded the head at startup, before an approach, or at idle — its position was whatever
hands, gravity, or gait vibration last left it. Four changes close that:

- **Ramped motion.** `set_head()` in `src/actions.py` interpolates from the last commanded
  position over `PIBOT_HEAD_RAMP_STEPS` steps (default 6) with `PIBOT_HEAD_RAMP_PAUSE_S`
  between them (default 0.02s, ~120ms for a full sweep); steps=1 restores the old jump. The
  first move after power-up still jumps — there is nothing to ramp from.
- **Levelling enforced in code.** The hardware node commands `pan=0, tilt=0` at startup (with
  the normal brief-hold-then-relax) and again at the start of every approach — there with
  `auto_relax=False`, holding torque for the whole approach so gait vibration cannot walk the
  sensor off aim, released in `finish_approach`. The manual "owner levelled the sensor" fix is
  now a state the software asserts.
- **Idle re-level.** `idle_stance_reset` now also returns a panned/tilted head to level when
  the idle interval expires, alongside the existing stance reset. A head whose position was
  never commanded (unknown) is left alone. The idlereset test graph gained a step that turns
  the head aside so this is watchable.
- **A latent release-token bug fixed on the way.** `Hardware.hardware_dict` built a fresh dict
  on every access, but `actions.py` stores the head auto-relax cancellation token in that dict
  — so a pending release could never be cancelled from this node, and a hold-torque head move
  could have its torque silently dropped by an earlier move's release thread. The dict is now
  persistent, and a `move_head` with `auto_relax=False` explicitly voids any pending release.

The auto-relax default itself is kept: it exists for a real scar (upstream 2026-03-01, the head
servo buzzing and heating when held). The approach is the one place torque is held, bounded by
the approach's own duration; everywhere else the discipline is positional — reassert level at
known moments rather than hold continuously.

**Verified offline, not yet on hardware.** A 13-check harness against a fake servo confirms:
ramp reaches the target monotonically, clamps hold (pan floors at hardware 50), auto-relax
fires and invalidates the write cache, a hold-torque move cancels a pending release, and
`release_head` drops both channels. The battery read 6.65V unloaded at session end — suspect,
since unloaded reads run about a volt optimistic (pre-flight scar) — so no motion graph was
run. Still to verify on hardware with a charged pack: `./run.sh motion` (watch the head sweep
smoothly instead of snapping), `./run.sh approach` (log shows "head levelled, torque held"),
`./run.sh idlereset` (head returns to level during step 1b).

Plain-language summary: the robot's head — the little pan/tilt mount holding the camera and the
distance sensor — used to snap to positions at full speed and then go limp, and nothing ever
put it back to a known position. Since the distance sensor points wherever the head points,
that's how the sensor ended up tilted at the floor and faked a walking bug earlier today. Now
the head moves smoothly, the software levels it when the robot starts, holds it firmly level
while walking up to obstacles, and straightens it out whenever the robot has been idle for a
while. The logic is fully tested in simulation; a quick check on the real robot is still owed
once the battery is charged.

## 2026-08-20 — The approach was never inaccurate: a tilted sensor faked the overshoot, and with the head levelled it stops dead on target

Resolves the approach overshoot (PENDING). The owner levelled the ultrasonic sensor — it had
been tilted down, which was making the approach abort with no distance readings (a downward
beam mostly hits the floor and returns no echo; see the earlier 2026-08-20 entry) — and re-ran
`./run.sh approach` with a tape measure. It saw the obstacle, walked to it, and **stopped
exactly 25cm away by tape** against a 25cm target.

The instrumentation added earlier today (previous entry) is what makes this conclusive rather
than anecdotal, and it disproved the working hypothesis:

    approach diagnostic: stop decided at 33.9cm, loop predicted the cycle in flight would reach
    23.9cm (lead 10.0cm at 10.0cm/cycle); settled at 24.2cm — the in-flight cycle actually
    carried 9.7cm (-0.3cm vs the lead predicted), final -0.8cm from the 25cm target

With the sensor level the loop got **101 distance readings** (versus 3 when tilted), the
per-cycle travel prediction was near-perfect (predicted 10.0cm for the in-flight cycle, the
robot actually moved 9.7cm), and it settled at 24.2cm by sensor / 25cm by tape — essentially
on target. The sensor's own reading agreeing with the tape to under a centimetre also confirms
the HC-SR04 reads true when it is aimed level.

**Decision:** do not change the approach lead calculation. The 8.3cm "overshoot" recorded on
2026-08-19/20 was an artifact of the tilted sensor reading long and sparse, not a control
error, and the hypothesis floated in the previous entry — that the final decelerating cycle
covers far less than the whole-approach average, so the lead over-predicts — is wrong: measured,
the in-flight cycle carried 9.7cm against a 10.0cm prediction. Acting on that hypothesis would
have detuned a loop that was already accurate. This is the case the "instrument before you
change a movement loop" rule exists for.

The three-point diagnostic line is kept: it cost nothing here and turned a suspected bug into a
measured non-bug in a single run, which is exactly what it is for if the approach is ever
suspected again.

Plain-language summary: the robot was thought to stop too far from obstacles. It turned out the
distance sensor was pointing slightly down, so it both struggled to see the obstacle at all and,
when it did, read the distance as longer than it really was — which looked like the robot
stopping short. Once the sensor was straightened, the robot walked up and stopped exactly 25cm
away, measured with a tape. The measurement tool added earlier confirmed the robot's own
stopping calculation was right all along, so nothing in the walking code needed changing — and
changing it, as had been considered, would have made a correct thing wrong.

## 2026-08-20 — Instrumented the approach stop to measure the in-flight cycle's real travel, before touching the accuracy

The approach overshoot (PENDING) had one hardware run of data — stopped 8.3cm short of a 25cm
target — and the same lesson the turn taught applies: the last two changes to a movement loop
were made on inference and one was wrong, so measure before changing. This adds instrumentation
only; the stop logic is untouched.

`turn_to` and the approach stop share a shape: both predict where the *in-flight* gait cycle
(the one that cannot be interrupted, and runs on after the stop is decided) will leave the
robot, and stop when that prediction reaches the target. The approach's prediction is
`lead_cm` — the travel it expects that final cycle to add — set to `travel_cm_per_cycle`, which
is averaged over the whole approach. `finish_approach` then blocks while the robot completes
that cycle and settles, exactly as `turn_to`'s finally block does.

The instrumentation snapshots the prediction at the stop decision and, once the robot has
halted, takes the settled distance from the readings that follow (last reading in a 1.5s window,
so any readings dora buffered mid-cycle age out). It logs one line: where the stop was decided,
what the loop predicted the in-flight cycle would reach, and where it actually settled — so the
in-flight cycle's *real* travel is a measured number against the predicted `lead_cm`. It changes
no motion: the stop fires exactly as before, and the settle window only observes the readings
that were already arriving. Verified under `PIBOT_NO_MOTION=1` that the added code paths run
without error and the graph still tears down cleanly; the diagnostic line itself needs a real
motion run to produce values.

Applied to the one existing run's numbers by hand, the framing it will print is telling: the
loop predicted the final cycle would carry 12.8cm (its whole-approach average), the robot
settled having moved only ~1.5cm after the decision, and it stopped 6.8cm short. The hypothesis
this is built to confirm or refute: the final cycle — a decelerating, stopping cycle — covers
far less ground than a full-stride cruising cycle, so using the whole-approach average as the
lead over-predicts it and stops the robot early. Deliberately not acted on yet — this needs a
few `./run.sh approach` runs with a tape measure against the physical robot (PENDING), both to
confirm the mechanism and to catch whether the sensor itself reads long or short, before the
lead calculation is changed.

Plain-language summary: the robot stops a bit too far from obstacles when it walks up to them.
Before changing anything — an earlier guess at a related problem had already gone the wrong way
— the code was fitted with a measurement that records, each time it stops, how far it *thought*
its last step would carry it versus how far it actually went. On the one run so far those
numbers are very different (it expected ~13cm and got ~1.5cm), which points at the last step
before stopping being much smaller than the average step the robot uses to judge it. That is now
a hypothesis with an instrument pointed at it, to be confirmed over a few runs with a tape
measure rather than fixed on a hunch.

## 2026-08-20 — Single-purpose graphs now stop themselves: the driver node broadcasts a shutdown so timer-driven device nodes end instead of hanging `dora run`

The approach graph left `dora run` hanging after the test finished (PENDING 2026-08-20): the
`approach_test` node exited "successfully", the robot was already back in neutral and relaxed,
but `hardware` and `ultrasonic` kept idling on their timer ticks and the CLI never returned,
needing `./stop.sh`. This turned out to be shared by every single-purpose graph, not just
approach.

**Root cause, established with a throwaway two-node dataflow rather than guessed.** A dora
node's event loop (`for event in node`) ends only when *all* its senders have dropped
(`Node.next()` returns None). A device node that owns a timer input — `hardware` in every
graph, `ultrasonic` in approach — has a sender that never drops, so it keeps running on ticks
after the driver node exits, and `dora run` waits for it forever. Confirmed directly: a
timer-driven worker ran on (ticks 4..61) after its driver exited, and only `--stop-after`
ended it. dora 0.5.0 exposes no node-side API to stop a dataflow (checked the `Node` surface
and the `dora run` options) and does not cascade a node's exit to its peers.

Device nodes *without* a timer are fine as they are: `led` in the motion graph (its only input
is `motion_test/emotion`) and `camera` in the camera graph (only `camera_test/capture`) drop
their sole sender when the driver exits and end on their own. So the camera graph never hung
and needs no change.

**Fix:** a reserved pseudo-tool `__shutdown__` (`common.SHUTDOWN_TOOL`, with
`common.send_shutdown()` / `common.is_shutdown()`). Each of the eleven driver test nodes
broadcasts it on the already-wired `tool_call` stream as the last thing it does; `hardware`
and `ultrasonic` end their event loop when they see it. It is not a real tool — absent from
`TOOL_OWNER`, dispatched by nobody — so the check sits ahead of the ownership test and the full
autonomous graph, where nothing sends it, is unaffected. No dataflow YAML was re-wired: every
graph's driver already broadcasts `tool_call` to its device nodes. `camera_test` is the one
driver left alone — it emits `capture`, not `tool_call`, and its graph does not hang.

**Verified on hardware (no servos moved).** Ran the approach graph under `PIBOT_NO_MOTION=1`,
which brings up the I2C/ADC and the full node event loops but never constructs `Control()`, so
nothing can move. `dora run` returned on its own in ~11s (exit 0) where it had hung before;
the log shows both `hardware` and `ultrasonic` logging "shutdown received — ending run" and all
three nodes finishing successfully, with no orphaned processes left. The shutdown is sent at
the end of `main()` on both the success and the abort path, and every driver's `main()` reaches
that point (no early returns bypass it), so the motion graphs get the same teardown.

Plain-language summary: after one of the robot's self-contained test routines finished, the
program behind it would not quit on its own — the parts that talk to the sensors and legs kept
ticking over in the background, so the command never returned to the prompt and had to be
killed by hand. The cause was pinned down with a tiny standalone test rather than guesswork:
those background parts run on a repeating timer that never stops by itself. The fix has the
test routine send a clear "we're done" signal when it finishes, which those parts now listen
for and shut down on. It was verified on the real Pi in a mode that powers everything up but
physically cannot move the robot, so it was safe to run: the program now exits cleanly by
itself. Every one of the eleven test routines got the same treatment; the camera test never
had the problem.

## 2026-08-20 — The turn overshoot was the body settling after the stop, not the planner; compensating for it moves smoothturn from FAIL to PASS

The turn overshoot open since 2026-08-19 (every `turn_to` landing ~4deg past target, both
directions, every size) is fixed. The previous two attempts at this loop were made on inference
and one made things worse, so this time the loop was instrumented before anything was changed.

**Diagnostic first.** `turn_to` now samples the heading at three points instead of one: what
the in-flight cycle was predicted to land at when the stop fired, where that cycle actually
finished, and where the robot settled. That splits the residual into a *prediction error* (the
cycle landing somewhere other than `per_unit` predicted) and a *post-stop settle* (rotation
after the stop decision — the foot-reset cycle, the body settling, the gyro integrating through
set-down). One instrumented `smoothturn` run (8 turns, +/-90/+/-180/+/-20) was decisive: the
post-stop settle was a consistent 2.5-3.8deg (mean 3.35), **always in the direction of the
turn**, on every one of the eight turns, while the prediction error was smaller and unbiased.
So the overshoot is the settle, and the cheapest of the three options in PENDING — reverting
`plan()` to truncating — would have chased the wrong term.

**Fix:** new `TURN_SETTLE_DEG` (default 3.35, `PIBOT_TURN_SETTLE_DEG` to override). `turn_to`
now aims the closed loop at `stop_target = target - settle` and lets the settle carry the robot
the rest of the way, rather than aiming at the true target and landing past it every time. The
compensation is skipped for turns smaller than `tol + settle`, where subtracting it would aim
inside the tolerance band and let the loop stop before the robot has meaningfully turned; those
keep aiming at the true target as before. The stop check, the endgame planner and the opening
cycle all reference `stop_target`.

**Verified on hardware.** The next `smoothturn` run went from the prior **OUT OF TOLERANCE**
(worst residual 7.9deg, verdict FAIL) to **PASS** (worst 4.2deg against the 5deg tolerance).
The turns that had been the worst offenders improved the most: B1 -180 went +5.1 -> +0.5, B2
+180 went -7.5 -> +1.0, C1 +20 went -7.2 -> -1.6. Seven of the eight turns ran (the run was
stopped by hand before the final -20 left turn; +20 and -180 both ran, so small and left-hand
turns are each covered).

**Known residual, left deliberately:** the +90 quarter-turns still land -2.6 to -4.2deg,
improved only modestly. The diagnostic shows why — for the quarter-turns the in-flight cycle's
prediction error is a consistent +2-3deg (the `per_unit` estimate reads slightly low at the
endgame), which the settle compensation does not touch. It is within tolerance but with less
margin than the larger turns. This is the smaller of the two causes named in the original
analysis, now isolated. Not chased here: it is inside tolerance, and tightening it risks the
hunting that an earlier version of this loop showed (CHANGELOG 2026-08-19).

The three-point diagnostic was kept in the code rather than removed — it prints one line per
turn reporting the settle and prediction error that turn actually produced, which is what any
future work on the quarter-turn residual will need, and it costs only a short wait during the
stop the robot is already performing.

Plain-language summary: when the robot was told to turn, say, 90 degrees, it always turned
about 94 — a steady 4-degree overshoot every time. Rather than guess at the cause again (two
earlier guesses had already been made, one of them wrong), the turn was first fitted with
instruments that measured exactly where the extra rotation came in. The answer was clear: the
robot keeps drifting a few degrees in the direction it was turning *after* it has decided to
stop, as its legs reset and its body settles. The fix simply tells it to stop that few degrees
early so the drift lands it on target. On the robot this turned a failing accuracy test into a
passing one. One kind of turn — the quarter-turn — still overshoots by three to four degrees
for a different, smaller reason, but it now stays inside the allowed tolerance, so it was left
alone rather than risk reintroducing an old wobble.

## 2026-08-20 — Approach re-verified on hardware: retreat works for the first time, forward accuracy is worse than hoped, and the graph doesn't exit on its own

Ran `./run.sh approach` twice, chasing the accuracy re-check PENDING had open since 2026-08-19.
No code changed this session — this was observation.

The first run aborted before any leg moved: `hardware` waited 5s for a distance reading from
`ultrasonic` and got none, logged `approach ABORTED: the distance sensor never reported, so
nothing moved`, and safely backed out to neutral stance with servos relaxed. Investigated by
running the sensor standalone, outside the dora graph entirely (`./bin/py
test/test_ultrasonic.py`): with the head level it read a rock-solid 49.0cm and then 82.5cm
across roughly 30 samples each, zero dropouts. The sensor and its mount are not the fault; an
earlier close-range reading (2.8-5.4cm) from the same tool was a red herring — the head was
tilted down and the owner was waving an arm in front of it at the time, not testing the
approach geometry.

The second run, same physical setup, worked end to end: 140 distance readings received, all 5
steps completed. Numbers: approach walked forward 5 cycles from 84.4cm and stopped at **33.3cm
against a 25cm target — 8.3cm over**, worse than the "couple of cm" the PENDING note was hoping
to confirm. Retreat then walked backward 2 cycles from 29.7cm and stopped at **47.5cm against a
50cm target — 2.5cm short** — and this is the first time the retreat path has ever executed on
hardware; both prior attempts had found the sensor already past the target distance with
nothing to retreat from.

Both runs surfaced a second, unrelated fault: after `approach_test` finished and logged "finished
successfully", `hardware` and `ultrasonic` kept idling on their timer ticks indefinitely and
`dora run` never returned control — `./stop.sh` was needed both times to clear it. The robot
itself was already safe in both cases (back in neutral, servos relaxed, before the hang), so
this is a graph-teardown bug in the dora invocation, not a safety issue, but the CLI does not
exit on its own after this graph.

Plain-language summary: the robot's obstacle-approach test was re-run to see how accurately it
stops near a wall. The first attempt found nothing at all — the distance sensor delivered no
readings — but testing the sensor on its own (not through the full robot programme) showed it
works fine, so that was a fluke of the test run rather than a broken sensor. The second attempt
worked properly: the robot walked up to the target and stopped about 8cm further out than
asked, then backed away and stopped about 2.5cm short of where it should have — the backing-off
manoeuvre had never actually been tested on the real robot before, and now it has. Separately,
after the test finishes the software behind it doesn't shut itself down properly and has to be
stopped by hand — worth fixing but not something that put the robot at risk.

## 2026-08-19 — A servo lead pulled out mid-testing; the legs check out and the robot stands again

A servo lead came out of its connector while the movement graphs were being run, leaving
several legs out of sync. The I2C bus was checked first and all four devices answered — both
PCA9685 drivers at 0x40 and 0x41, the ADC at 0x48, the IMU at 0x68 — so the driver boards were
never in question and the fault was mechanical at the connector. The pack read 6.76V under
load, above the floor, so it was safe to drive servos.

New `test/servo_recover.py`. It differs from the existing `test/diagnose_servos.py` in the
three ways that matter when a robot is already in an unknown state: it reads the battery with
the servo rail ON and refuses below the floor, because the unloaded reading is about a volt
optimistic; it relaxes first and waits, so leads can be re-seated and legs placed by hand
before anything is energised; and it drives one joint at a time rather than commanding all
thirty-two servos to a pose the instant it starts. Each of the eighteen leg joints gets a small
sweep and the operator says whether it moved — there is no position feedback on these servos,
so that is the only way the question can be answered.

**Decision:** `--stand` computes the standing pose by running the robot's own inverse
kinematics with the servo swapped for a recorder, rather than reimplementing it. The IK is the
one piece of this codebase that must not be duplicated, and a maintenance script is exactly
where a second copy would go unnoticed. Verified offline before it drove anything: 18 joints,
all inside servo travel, perfectly mirrored left to right.

**Fix:** the tool documents `./bin/py` rather than `./venv/bin/python`, and RUNBOOKS §1 now
explains why. The venv is untracked, so a git worktree — which is where this branch is being
tested — has none of its own, and the documented command failed outright. `run.sh` already fell
back to the main checkout's venv; `bin/py` is the equivalent for a plain script.

**Fix:** `--stand` ran the full joint-by-joint check before standing, because the flag was left
out of the condition that skips it.

Outcome: the joints were exercised, the robot reached the reference pose with its legs even,
and `--stand` put it back on its feet correctly. No servo was found dead, so the lead had
simply been re-seated.

Plain-language summary: a servo's plug came out while testing and some legs ended up at the
wrong angles. There is now a tool that checks the electrics first, refuses to move anything on
a low battery, lets you re-plug the lead with the motors switched off, then wiggles each of the
eighteen leg joints in turn so you can see which one is not answering. It finishes by standing
the robot back up. The robot is fine.

## 2026-08-19 — Movement programme verified on the robot, and both closed loops recentred on what the hardware measured

Second hardware pass, after the turn and approach fixes. Both graphs ran clean and the owner
confirmed both looked right. What the logs then showed was not a fault but a bias, in each
case the same one: a loop that converges reliably but consistently a little short.

`smoothturn` completed all 8 turns (11/11 steps) where the previous run aborted at the first,
with **no rocking at the end of any turn** — the hunting is gone. Worst residual 5.0deg against
a 5.0deg tolerance. But every single turn undershot: -4.9, -4.4, -5.0, -3.9, +2.5, -3.8, -1.0,
-2.7, accumulating to **-23.2deg over eight turns**. Four quarter-turns left the robot about
18deg short of where it started, which is inside spec per turn and visible as a heading error
by the end.

`approach` stopped at 29.7cm for a 25cm target — 4.7cm over — while reporting it had measured
**8.9cm per gait cycle**, where 17 cycles covering roughly 107cm makes the true figure about
6.2cm. Heading drift was **+0.6deg**, down from +10.2deg on the previous run, confirming the
one-second gyro bias calibration was the fix for that.

**Decision:** the turn planner rounds again rather than truncating. Truncation was introduced
earlier the same day to stop the hunting, on the reasoning that undershooting is safer than
overshooting into a reversal. With the stop rule now ending the turn as soon as it lands inside
tolerance, the hunting cannot recur from rounding alone — and truncation was the source of the
systematic undershoot. Measured against the range this robot actually occupies (2.6-3.3deg per
angle unit): a 90deg turn improves from -4.2deg to -0.9deg and a 180deg turn from -5.1deg to
-1.8deg, still with zero reversals anywhere in the sweep.

**Fix:** the opening cycle is planned for a third of the target rather than half. Rounding
reintroduced one overshoot — a 20deg turn on a robot rotating 5.0deg per unit against a 3.3
seed went 10deg past — because the first cycle is committed before any measurement exists and
half of a short target is already most of it. A third bounds that, costs nothing on a large
turn where it still saturates the steering angle, and brings every case in the sweep to within
tolerance: the 20deg turns now land within 1.1deg on every simulated robot.

**Fix:** the approach averages its travel-per-cycle over the whole approach instead of a
rolling two-cycle window. A short window still carries most of the sensor's noise, which is
what produced 8.9cm against a real 6.2cm and stopped the robot 4.7cm early. Total distance
over total cycles cannot be fooled that way and only steadies as the approach runs.

Also seen and worth recording: the approach's retreat leg reported "already 136.6cm away,
needed no movement" and moved nothing, which is the correct response — the sensor was reading
past the obstacle by then. The retreat path therefore remains unexercised on hardware. And the
pack reached 6.24V at its lowest during these runs, close enough to the 6.0V floor to be worth
charging before the next session.

Plain-language summary: the robot now turns smoothly and walks up to things and stops, and both
were confirmed on the real robot. Both were also landing slightly short every time — the turn
by about 4 degrees and the approach by about 5cm — because each was deliberately erring on the
cautious side. Now that neither can overshoot into a wobble, that caution has been removed, and
both aim at the target rather than just short of it.

## 2026-08-19 — The approach stopped 7cm short of every target, because its stop lead was measuring sensor noise

First hardware run of `approach`. It walked smoothly to the obstacle and back, but the numbers
were wrong in a consistent way: it stopped at 31.9cm for a 25cm target and at 42.9cm for a 50cm
target backing off — about 7cm short in both directions, and it reported allowing a 7.7cm lead
for the cycle in flight when a whole gait cycle only covers about 3.5cm.

**Fix:** the lead is now one cycle of travel, measured as distance covered divided by gait
cycles run, rather than derived from a sample-to-sample closing rate. The HC-SR04 is noisy
enough that a rate taken over the 200ms between readings is dominated by that noise, and the
lead inherited it — so the robot stopped early by roughly the size of the noise rather than by
the distance it was actually about to cover. Dividing distance by cycles averages the same
quantity over seconds instead of milliseconds, and is the same trick the turn uses to learn its
degrees-per-angle-unit. Simulated against a sensor with up to +/-2.5cm of noise, the stop lands
within 1.5cm of target in both directions, and the noise no longer moves it at all.

**Fix:** the approach calibrates gyro bias for a full second like `walk_straight`, not 0.6s.
The first run drifted 10.2deg of heading over 11 cycles where a straight walk holds 3.5deg, and
a hurried bias measurement is the most likely reason.

**Fix:** `test/servo_recover.py --stand` ran the whole joint-by-joint check first. The flag was
not included in the condition that skips it, so asking to stand up meant sitting through
eighteen sweeps. It now goes straight to the pose.

Plain-language summary: told to stop 25cm from a wall, the robot stopped at 32cm. It was
braking too early because it estimated its own speed from two distance readings taken a fifth
of a second apart, and the sensor's own jitter over that gap looked like speed. It now works
out how far it travels per step by watching over several steps, which the jitter averages out
of.

## 2026-08-19 — The continuous turn stopped dancing around its target, and a tool to find a pulled servo lead

First hardware run of the continuous turn. The owner reported it "turned 90 degrees very
quickly and smoothly (i liked it) and then sort of started dancing". The log shows exactly
that: the turn reached 89.0deg of a 90deg target — a 1.0deg residual — and then alternated its
steering angle -1, +1, -1 around the target until the stall guard aborted the run at step 1
of 8.

**Fix:** the turn now stops as soon as the cycle in flight is predicted to land inside
tolerance. It previously continued whenever one more cycle promised a closer landing, which
never terminates on real hardware: the smallest correction the gait can make is one angle
unit, about 2.7deg, which is the same size as the residual being chased — so each cycle
overshot and the next was planned to come back. That rule was added the same day to avoid a
simulated turn finishing 4.8deg short. It traded an invisible 4.8deg residual for a visible
oscillation, which was the wrong way round: tolerance is the contract, not a target to beat.

**Fix:** the planner truncates rather than rounds when sizing a cycle, so a cycle is more
likely to fall just short of the rotation remaining than to overshoot it. Overshoot has to be
undone by a cycle in the other direction, and alternating between the two is the hunting
itself.

**Fix:** the opening cycle is planned for only half the target. Until one cycle has been
measured, the degrees-per-angle-unit figure is a seed from another surface on another day, and
the first cycle is committed before any measurement exists to correct it — a seed 65% low
overshot a 20deg turn outright in simulation. Halving bounds that and costs nothing on a large
turn, where half the target still saturates the maximum steering angle.

**Fix:** a turn samples yaw every 0.08s rather than every 0.35s. The loop can only notice a
gait cycle boundary at its next sample, so every measurement of "how far did that cycle turn
us" was late by up to one interval and included that much of the *next* cycle's rotation. That
is a systematic over-estimate, not noise — 14% at the old interval — and an over-estimate makes
the turn believe it has further to go than it has, so it stops short. Worst simulated error
across a 2.5x range of robot responsiveness fell from 10.0deg to 5.0deg against a 5deg
tolerance, with zero reversals anywhere.

**Fix:** the stop no longer cancels a cycle that has not started. `gait_cycles` is incremented
at the end of a cycle, a moment before `condition_monitor` re-reads the queue for the next one;
stopping inside that window replaced the queued angle with the stop and the cycle never ran,
costing a simulated 90deg turn its final 10deg. The prediction now ignores a cycle until the
window has passed.

**Fix:** the stall guard misdiagnosed the hunting as "the gait is not turning the body". It
compared rotation against a fixed 1deg threshold, but a deliberate 1-unit trim near the target
only moves the body about 3deg, and two of those in a row look like a stall. It now compares
against the rotation the commanded angle *should* have produced.

New `test/turn_settles.py` — an offline regression check that drives the real `turn_to` against
a simulated robot and counts reversals, because "did it land accurately" was never the question
that mattered here; "did it stop" was.

New `test/servo_recover.py`, after a servo lead pulled out during testing and left some legs out
of sync. It reads the battery with the servo rail ON and refuses below the floor, relaxes and
waits so connectors can be re-seated by hand, then moves ONE JOINT AT A TIME so a dead or
mis-seated servo can be identified by watching, and finishes at the reference pose where every
leg should mirror its opposite number. It differs from the existing `test/diagnose_servos.py`
in exactly those respects: that one drives all 32 servos to the reference pose the instant it
starts, with no battery check, which is not what to do to a robot whose legs are already out of
sync.

Plain-language summary: the robot turned the right amount and then fidgeted back and forth
instead of stopping, because it kept trying to shave off the last degree with a correction that
was itself bigger than a degree. It now accepts "close enough" and stops. Separately, a servo
came unplugged during testing, so there is now a tool that checks each of the eighteen leg
joints one at a time and tells you which one is not responding.

## 2026-08-19 — Turning made continuous, obstacle approach closed on the ultrasonic, and the gait's foot placement, servo traffic and attitude axes all fixed

The rest of the movement programme, implemented and smoke-tested offline in one pass.
**Nothing in this entry has run on the robot yet** — the owner is testing it manually, and
every claim below is from simulation or from reading the code. The graphs to run are listed at
the end. Where a number appears it comes from a simulated robot, and says so.

### Turning is one continuous rotation instead of a series of lurches

Prioritised ahead of the rest at the owner's instruction, after they observed the stutter twice.
The cause was structural. `condition_monitor` treated any command without a stride as
single-shot — one `run_gait` call, then the queue was cleared — and a turn has no stride. So
`turn_to` had to drive the rotation from outside as a sequence of separate one-cycle commands,
each waiting for the queue to clear and pausing 0.4s to let the body settle before measuring.

**Fix:** the single-shot rule now applies only to the genuine stop-and-stand command, which
has neither stride nor angle. A turn stays queued, `run_gait` re-enters it cycle after cycle,
and `turn_to` was rewritten around that: one command, its steering angle re-trimmed as the
robot rotates. `walk`'s separate re-queue-per-cycle path for turns is gone with it.

**Decision:** the turn plans one cycle AHEAD and stops only when stopping beats stepping again.
Three separate failures in simulation forced this, all of them consequences of a gait cycle
being uninterruptible, and all of them producing a *worse* landing than the stuttering version:
planning from the current heading is always one cycle late, and overshot a 90deg target by
15.6deg; reconsidering only at cycle boundaries made a 20deg turn take four cycles and
oscillate; and stopping at the first moment that merely fell inside tolerance halted a 90deg
turn 4.8deg short when the cycle already planned would have landed 1.2deg short. The rotation
per angle unit is also now averaged over the whole turn rather than taken per cycle, because
this loop notices a cycle boundary up to a sampling interval late and that jitter alone read
3.6 on a simulated robot really doing 3.3. Swept across a 3.7x range of robot responsiveness,
with the gyro sign both known and unlearned, worst error is 4.5deg against a 5deg tolerance.

**Fix:** the seed for degrees-per-angle-unit drops from 4.5 to 3.3, the figure two `turn_to`
runs measured on 2026-08-19. The old value over-predicted every cycle by about a third, which
is why turns consistently stopped short.

### The robot walks up to things and stops

New `approach` tool: walk until a given distance from whatever is in front, closed on the
ultrasonic sensor. This is the first time two device nodes have closed a control loop together
— the ultrasonic node owns the sensor and publishes distance, the hardware node owns the legs
and decides when to stop — which is the architecture's central claim being used rather than
asserted. `dataflow.yml` gains one wire for it.

**Decision:** it is a state machine driven by the node's event loop, not a blocking loop like
`turn_to`. The gait runs on its own thread, so blocking would not have kept the robot walking;
it would only have stopped the node receiving the distance messages the loop is closed on.

**Decision:** the robot never moves before it has seen a reading. Walking first and looking
afterwards would commit it to a gait cycle it cannot take back. If the target is already
satisfied it reports that and moves nothing at all; if readings stop arriving it stops rather
than continuing blind; and a cycle cap bounds it either way.

**Fix:** readings are median-filtered over three samples, because the HC-SR04 occasionally
returns a wild value and the stop decision reads it. The stop is also issued early by the
distance still to be covered in the cycle in flight, since that cycle cannot be interrupted —
signed, so it leads correctly whether closing on an obstacle or backing away from one.

### Stance changes are ramped

A stance change was one `set_leg_angles()`, so the body dropped or the feet snapped outward as
fast as eighteen servos could slew. It is now split into four smaller moves, each reach-checked
before it is applied — interpolating between two reachable poses does not by itself guarantee
the path between them stays inside the 90..248mm window, and `set_leg_angles()` fails silently
when it does not. Verified across all 56 ordered pairs of stances.

### The gait places its feet instead of dropping them

Measuring before changing corrected the original research. There is **no** per-step teleport:
the swing leg's commanded height is continuous across cycle boundaries, because the previous
cycle leaves it in the air. Two real problems were there instead.

**Fix:** foot height is computed from the phase rather than accumulated frame by frame, and
follows an eased profile instead of a constant rate. The old constant-rate ramp stepped
vertical velocity from 0 to ~430mm/s and back to 0 at each phase boundary — an impulsive
acceleration at exactly the two moments that matter, lift-off and touchdown, which is what
slaps a foot into the floor. Same lift, same timing, same endpoints; only the shape between
them changes. Simulated touchdown speed falls by 48-78% depending on gait speed.

**Fix:** the 40mm jump when setting off is gone. Starting from a stand every foot is on the
ground, and the old code commanded one tripod straight to full lift height in a single 10ms
frame — a lurch once per walk. It now eases up over the opening phase. The mirror image, a
tripod dropping 40mm when a walk stops, is fixed the same way by `set_feet_down`.

**Fix:** computing height from the phase also removes an accumulation artifact. Phase
boundaries fall on fractions of the frame count but frames are whole numbers, so the old
per-frame increments over- or under-shot the lift by a few percent and left a stance foot
commanded up to ~3mm below the resting plane. `PIBOT_GAIT_GROUND_PRESSURE_MM` exists to put
that back deliberately if the stance legs turn out to want it.

`nodes/stances.py` mirrors this arithmetic for its offline reach check and was re-synced —
exactly the drift that module's own docstring warns about. The height profile it now *calls*
rather than copies, so that half cannot drift again. The profile moved to a new
`src/gait_profile.py` for that: it is a pure function of the phase and imports nothing, which
keeps `stances.py` free of the driver stack. Importing `control` directly, as a first attempt
did, would have dragged `lgpio` and the I2C drivers into a module whose whole purpose is to
answer reach questions offline.

### The gait stops writing servos that have not moved

The gait wrote all 18 leg servos every frame at ~100 frames a second, and most frames do not
change most joints: leg angles are whole degrees and a frame often moves a joint by less than
one. Those writes cost real time on a bus the gyro sampler is also using — and that contention
is what tore the gyro reads fixed earlier today. `Servo` now skips a write when the channel
already holds the value being asked for, which is safe precisely because a PCA9685 channel
holds its value: not writing is what leaves the servo where it is. Simulated saving is 20-56%
of all bus writes, most at slow speeds where per-frame movement is smallest.

**Fix:** every path that drives a channel behind `set_servo_angle`'s back now invalidates the
cache — relaxing one head servo, relaxing everything, cutting the power rail. Without that the
next command to a relaxed channel would be skipped as redundant and the servo would stay limp.

**Fix:** gait frames are scheduled against an absolute deadline rather than sleeping a fixed
10ms after each frame's work. The period is now what it says it is whenever the work fits
inside it, a frame that finishes early gives its time back instead of extending the cycle, and
lateness is never carried forward and compounded. Frames that overrun are counted rather than
hidden.

### Roll and pitch tilted the wrong axes

**Fix:** `calculate_posture_balance` built its X rotation from the pitch argument and its Y
rotation from the roll argument. Confirmed by computing the commanded foot heights: `roll`
moved the nose and tail legs and `pitch` moved the two side legs. So `set_attitude(roll=10)`
tilted the robot nose-down, and `lean_forward` — a stance whose description says nose-down —
leaned it sideways.

**Fix:** `imu6050` unpacked `update_imu_state()` as roll-first when it returns pitch first.
This is why the balance loop was never visibly broken: two swaps that cancelled. Both are
corrected together, since fixing either alone would have broken balancing.

**Fix:** roll and pitch shared one `Incremental_PID`. That object carries `last_error` and an
integral accumulator, so each axis computed its derivative against the other axis's previous
error and the two shared one integrator. They now have one controller each.

### To verify on hardware

`./run.sh smoothturn` (turning), `./run.sh approach` (obstacle approach — needs an obstacle
about 1-1.5m ahead), `./run.sh attitude` (roll/pitch axes — watch the robot, not the log),
`./run.sh idlereset` (stance reset), and re-run `./run.sh straightwalk` and `./run.sh odometry`,
which exercise the gait and servo changes. The attitude and turn graphs are the two whose
verdict only a person watching can give.

Plain-language summary: the robot now turns in one smooth movement instead of a series of
jerks; it can walk up to a wall and stop at a chosen distance by watching with its distance
sensor as it goes; it eases into a crouch rather than dropping into one; it places its feet
down instead of slapping them, and no longer lurches when setting off or thumps when stopping;
it stops sending instructions to legs that are already where they should be, which frees up the
wire its balance sensor shares; and "lean left" now leans left, which it did not before —
that instruction and "lean forward" were wired to each other's directions.

## 2026-08-19 — A walk now runs the gait cycles it was asked for, and the gyro stopped inventing rotations that drove the robot into a real spin

Two pieces of work that turned out to be one story: making distance honest exposed a
measurement bug that had been quietly corrupting the heading control shipped earlier the same
day.

### Cycles are counted, not timed

`walk` used to queue a gait command, sleep for `steps` x an *estimated* cycle duration, then
queue a stop. The estimate counts `run_gait`'s 10ms-per-frame sleep and ignores the 18 servo
writes each frame also spends on the I2C bus. Measured on hardware, it is wrong by a factor of
**3.2 at every speed**: speed 3 estimates 1.18s and takes 3.87s, speed 6 estimates 0.79s and
takes 2.54s, speed 9 estimates 0.40s and takes 1.20s. So every walk was running roughly a
third of the cycles it claimed, and the shortfall varied with speed — the same command
travelled different distances at different speeds.

**Fix:** `Control` now counts what it does. `gait_cycles` counts completed cycles and
`last_cycle_s` records how long the last one took; both are written only by `run_gait`, and the
zero-stride "stop and stand" form is deliberately not counted so the number stays a count of
cycles *travelled*. `walk` and `walk_straight` wait on that counter instead of on a stopwatch.
Verified on hardware: 6/6, 6/6, 6/6, 18/18 and turns of 1, 3 and 4 cycles, all exact.

**Fix:** `steps` now works for turns at all. A turn has x=0,y=0, which is the *single-shot*
branch in `condition_monitor` — one cycle, then the queue is cleared — and nothing re-queued
it, so every turn command ran exactly one cycle no matter what was asked. This is the
mechanism behind the 2026-08-18 note that 23 commanded cycles produced about 5 real ones.

**Decision:** the stop is queued during the *final* cycle rather than after it. `run_gait`
only re-reads the queue between cycles, so a stop queued after the Nth cycle completed let an
N+1th start, and every walk overshot by a full stride.

### The gyro was inventing rotation, and the heading controller believed it

The odometry run surfaced a robot that walked an L-shaped path while being told to walk
backwards in a straight line. The log showed the heading estimate jumping **+79deg in one
second** — impossible for a walking hexapod — after which the controller held maximum steering
correction for **25 seconds** and rotated the robot through roughly 170deg. The controller was
working correctly; it was fed a measurement that was not.

**Fix:** the gyro read is now a single atomic I2C transaction. `mpu6050.read_i2c_word` reads
the high byte and the low byte as two *separate* transactions, and the device updates its
registers at 1kHz underneath, so the word can tear across a sample boundary — taking the high
byte from one sample and the low byte from the next. Torn across the sign bit that yields a
near-full-scale value. A gait cycle issues ~1800 servo writes a second on the same bus, which
makes the window between those two reads both wide and frequent. `YawTracker._read_dps` now
does a two-byte block read, which is one transaction and cannot tear. (The driver's own method
is left alone; it is used by `get_gyro_data` at rates where this does not bite.)

**Fix:** the integrator no longer multiplies one instantaneous rate sample by an arbitrarily
long stall. A read delayed 300ms by bus contention used to contribute up to 75deg of phantom
yaw from a single sample; a stalled sample is now credited with one normal sample's worth of
time. Samples above 150deg/s are rejected outright as saturation or corruption, since the
robot never rotates that fast. Replaying one identical sample stream, the old integrator
accumulated **-283.8deg** of rotation that never happened where the new one accumulates
**+0.1deg**. On hardware the same 18-cycle backward walk went from a worst excursion of
169.5deg to 7.5deg, with 0 bad samples out of 10,701.

**Fix:** every `YawTracker` now reports its own health — samples taken, samples rejected, read
errors, stalled reads clamped, worst sampling gap — and every walk result carries that line.
A measurement subsystem that cannot say how much it threw away is not observable.

### The heading-hold experiment had been measuring nothing

**Fix:** `walk_straight`'s `gain` argument was dropped in tool dispatch — the test node sent
it, `hardware_node` never read it. So the "uncorrected" baseline legs ran *corrected*, and the
2026-08-19 heading-hold entry's comparison was invalid; both runs had honestly reported
"NO BETTER" because both legs were identical. With the argument passed through, the real
numbers are **14.9deg of drift uncorrected against 3.5deg corrected** over 6 cycles each way,
a 4.3x improvement. The test node now refuses a baseline leg that does not come back marked
"measured uncorrected", so an experiment cannot silently measure nothing again.

**Fix:** the steering loop logged only when the correction changed, so a walk holding one
correction went silent — the 25-second window in which the runaway happened produced no log
output at all. It now emits a heartbeat every 3 seconds regardless.

New files: `nodes/odometry_test_node.py` and `dataflow-odometry.yml` (`./run.sh odometry`),
which walks the same cycle count at three speeds with pauses for marking the floor, on the
principle that the distance question should be settleable with a ruler and no trust in any
number the robot reports.

Plain-language summary: the robot was told to take six steps and took two, because it was
counting with a stopwatch that ran three times too fast rather than counting the steps
themselves. It now counts steps. While testing that, we caught something worse: the sensor
that tells the robot which way it is facing was occasionally returning nonsense, because its
reading is assembled from two separate messages and the robot's own leg movements were
crowding the wire between them. The robot believed one of those nonsense readings, concluded
it had spun most of the way round, and turned hard to "correct" — which is why it walked an L
shape across the floor. The reading is now fetched in one piece, and obviously impossible
values are thrown away rather than believed.

## 2026-08-19 — Walking now holds its heading on the gyro, and the robot visibly stops wandering off course

Added `walk_straight`, a closed-loop version of `walk` that measures the robot's rotation
while the gait is running and trims it out by folding a small steering angle into the next
cycle. Verified on hardware by the owner, who reported noticeably less fragmentation in
forward and backward movement.

The mechanism rests on a property of the gait engine found by reading it rather than by
changing it: a `CMD_MOVE` carrying a non-zero stride is **continuous** in
`Control.condition_monitor` — the command queue is not cleared, so `run_gait` is re-entered
cycle after cycle and re-reads `command_queue` each time. Re-queueing the same command with a
different `angle` therefore steers the very next cycle, with no stop, no pose change and no
edit to `src/control.py`. This is the same "compose on the existing engine" approach the
stance and closed-loop turn work used.

**Decision:** the steering controller is PI, not P, and that was not the first design. Pure
proportional steering was simulated across every plausible drift (0.5-5deg per cycle) and
steering response (2-7deg per angle unit) and settled at a *constant* 21-38deg error rather
than converging. The reason is that the gait's bias enters the loop as a rate disturbance at
the same point as the control, so proportional control needs standing error to generate the
standing correction that cancels it — the textbook `drift / (gain x response)` residual. An
integral term supplies that correction on its own. Both gains were then swept against the
simulated plant and set to 0.12 with an integral ratio of 0.40, which holds the worst settled
error to about 6deg across the whole range where open-loop would reach 80deg. The reasoning
lives in the `HeadingHold` docstring, because a future reader would otherwise reasonably see
the integrator as an unnecessary complication on a plant that already looks like an integrator.

**Fix:** the gyro's sign convention is now learned once and remembered in
`data/gyro_sense.json` instead of being rediscovered per command. Nothing in the drivers says
which way the MPU6050 is mounted, so "does yaw read positive when the robot turns right" is an
empirical fact about this particular robot; `turn_to` already measured it and threw it away
every run. Both commands now record it, `PIBOT_GYRO_YAW_SIGN` overrides it, and a corrupt or
missing file reads as "unknown" rather than crashing. When the sign is unknown `walk_straight`
learns it from a deliberate one-unit steer, and if that probe fails to move the gyro within 6
seconds it gives up, straightens the steer and finishes the walk open-loop with a warning —
rather than holding a correction forever on the strength of a measurement that never arrived.

New files: `nodes/heading.py` (the `YawTracker` moved out of `hardware_node.py` so turning and
walking share one instrument, plus the sign persistence and the controller),
`nodes/straight_walk_test_node.py` and `dataflow-straightwalk.yml` (`./run.sh straightwalk`).
The test graph walks the same distance twice per round — once with the steering gain forced to
zero, once at the tuned gain — so the before-and-after comparison runs through the identical
code path with the identical instrumentation and exactly one variable changed, and prints a
verdict line. It opens with two small `turn_to` probes, which both establish the gyro sign and
prove the IMU responds before any heading number from it is trusted.

**Fix:** `run.sh` now falls back to the main checkout's venv when the directory it is run from
has none. The venv is untracked, so a git worktree — which is how this branch was developed —
previously could not run any graph at all.

Plain-language summary: the robot used to wander off course when told to walk forward, because
it moves by pushing all six legs back together and they never grip quite equally. It had no
idea this was happening. Now it watches its own rotation with the spinning-motion sensor it
already carried, notices when it has turned away from the line it was told to follow, and
steers gently back — the same way you would correct a supermarket trolley that pulls to one
side. It also now remembers which way that sensor counts, instead of working it out from
scratch every single time.

## 2026-08-19 — Deleted src/server.py, the unused Freenove TCP control holdover, and trimmed the comments that named it

Removed `src/server.py` (9 KB), the Freenove video/command TCP server carried over wholesale
when the fork copied `src/`. No node, dataflow graph, or script imported or launched it: a
repo-wide search for `import server` / `src.server` / `server.py` found only three descriptive
comments, not a single real use. In the dataflow architecture the control path is the
`tool_call` message stream, not a TCP socket, so this file had no role here.

**Fix:** the three comments in `src/servo.py` and `src/control.py` that named `server.py` as a
caller of the `_ServoPowerAdapter` `.on()/.off()` interface now name only `actions.py`, which is
the sole remaining caller. The adapter itself is unchanged. `py_compile` passes on both edited
files, and no reference to `server.py` remains anywhere in the tree.

Plain-language summary: the robot's code included an old network-control program, inherited from
the original robot, that let a PC drive it over a TCP connection. This project controls the
robot a different way — small programs passing messages — so that file was never used. We
deleted it and tidied up the three code comments that mentioned it by name.

## 2026-08-19 — Copied .env from PiBot-Hexapod into /opt/pibot-dora, so the full autonomous graph now has its API keys

Copied the secrets file the fork never carried: `cp /opt/pibot-hexapod/.env /opt/pibot-dora/.env
&& chmod 600 /opt/pibot-dora/.env` (RUNBOOKS §8). The two original projects share this
filesystem, so this was a local copy.

**Fix:** `OPENAI_API_KEY` and `PICOVOICE_ACCESS_KEY` are now present at the project root, which
unblocks the full graph's dependency on them (GPT-4o/Whisper/TTS and the Porcupine wake word).
Verified functionally, not just by file existence: `find_dotenv()` from the project root
resolves `/opt/pibot-dora/.env` and both keys load non-empty (164 and 56 chars). Values were
never printed. The file is `0600` and `.env` is gitignored (`.gitignore:17`), so no secret is
tracked — consistent with the no-credential-values rule.

The inherited file also carries `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` and `GROQ_API_KEY`, left
in place and harmless: no code in this project reads them (the old kitchen-sink providers are
gone, same clean-up as the requirements rewrite above). The location is `/opt/pibot-dora/.env`,
the runtime root that `bin/py`, the venv and `PIBOT_HOME` all default to; `nodes/common.py`
`bootstrap()` chdirs there before any node calls `load_dotenv()`.

**Still not done:** this only supplies the keys. The full autonomous graph remains unverified
end to end — `audio`, `llm`, `brain` and `buzzer` have still never run against real hardware
(separate PENDING item), and that needs a charged pack and accepts API spend.

Plain-language summary: the robot's brain needs passwords to reach OpenAI (for speech and
thinking) and Picovoice (for the "Hey Pi Bot" wake word). Those were left behind when this
project split off from the old one. We copied them across from the old project on the same
machine and confirmed the software can read them. The talking-and-listening part of the robot
can now start — though nobody has yet run it against the real hardware to see it work.

## 2026-08-19 — Rewrote requirements.txt to the 11 packages the code actually imports, dropping the 90-line inherited freeze

Replaced the inherited `requirements.txt` — a 90-line freeze carried over from PiBot-Hexapod
that pinned `anthropic`, `google-generativeai`, `groq`, `ollama`, Adafruit CircuitPython,
`luma.oled`, `opencv-python`, `RPi.GPIO`, `gpiozero` and more, none of which this code imports
and none of which the rebuilt Python 3.14 venv contains — with the real dependency set.

**Fix:** the file now lists exactly the pip-installed packages that code in `nodes/` and `src/`
imports, pinned to the versions in the venv (verified with `pip install --dry-run`, all
"already satisfied", no conflicts): `dora-rs==0.5.0`, `dora-rs-cli==0.5.0`, `pyarrow==25.0.1`,
`openai==3.1.0`, `pvporcupine==4.0.3`, `webrtcvad==2.0.10`, `pydub==0.25.1`,
`audioop-lts==0.2.2`, `smbus2==0.6.1`, `rpi-ws281x==5.0.0`, `python-dotenv==1.2.3`.

**Decision: system-site-packages dependencies are documented in a comment, not pinned.** The
venv is built with `--system-site-packages`, so `numpy`, `pyaudio`, `lgpio`, `spidev`, `smbus`,
`yaml` (PyYAML), `libcamera` and `picamera2` are supplied by apt and resolve to
`/usr/lib/python3/dist-packages`. Pinning them in `requirements.txt` would fight the OS packages
they shadow, so the header lists each with its apt package name instead. This is the deliberate
arrangement the rebuild established (CHANGELOG 2026-08-17), now made legible.

Method: enumerated every `import` in `nodes/` and `src/`, resolved each module to its
`__file__`, and split them by whether they live in the venv or in the system dist-packages.
`cv2` (opencv) and `scipy` were in the old freeze but are imported nowhere, so they are gone.
`audioop-lts` earns its place twice over — code imports `audioop` directly and `pydub` needs it,
and stdlib `audioop` was removed in Python 3.13, so on 3.14 the backfill is mandatory.

Plain-language summary: the project's list of required software libraries was inherited from the
old robot and had grown badly out of date — it named a dozen AI and hardware libraries the code
never uses, while the venv it was supposed to describe had been rebuilt from scratch with a
different, much smaller set. Anyone reading it to understand or rebuild the environment would
have been misled. It now names precisely the eleven libraries the code installs itself, each
pinned to the version in use, with a note listing the eight more that come from the operating
system.

## 2026-08-19 — Closed the camera investigation as failed: this camera works on neither Ubuntu nor Raspberry Pi OS, so we will try a different camera type

Closing the camera out of PENDING and stopping the investigation. Per the owner (2026-08-19),
this camera **does not work on either Raspberry Pi OS or Ubuntu**, and the plan going forward is
to try a **different type of camera** in the future rather than continue debugging this one.

**Decision: this supersedes the 2026-08-17 finding "The camera works on Raspberry Pi OS: the
board is fine, the RMA conclusion was wrong, and the fault is in Ubuntu's camera stack."** That
entry rested on the camera capturing successfully under Raspberry Pi OS on this board, which was
the load-bearing fact behind framing the fault as Ubuntu-specific packaging. The owner reports
the camera does not in fact work under Raspberry Pi OS either, which removes that anchor: the
OS-swap direction the PENDING entry offered no longer has a proven-good target, so it is off the
table. The 2026-08-17 entry itself is left intact and unedited, as the append-only rule requires
— the trail "we believed Pi OS worked, then found it did not" is the useful record.

**Decision: do not switch the Pi to Raspberry Pi OS for the camera.** The sole reason to move
was that Pi OS was believed to capture on this hardware; with that no longer true, there is no
camera reason to leave Ubuntu 26.04.

The earlier hardware-fault retraction (2026-08-17, "do not buy a cable, a camera module or a
board") is **not** reversed by this. That retraction was about not replacing individual links in
*this* camera's chain on the strength of the zero-packet evidence; the path forward here is a
**different camera type** altogether, which is a new component choice, not an RMA of the current
one. No purchase of a replacement cable/module/board for the existing camera is implied.

**What stays true:** the control plane works end to end on Ubuntu while the data plane carries
no frames; the media pipeline is complete and correctly formatted; and userspace, the CFE
driver, vendor libcamera, the DTB and the CSI IOMMU binding are all eliminated as causes
(CHANGELOG 2026-08-17). None of that work is wasted — it bounds where the next camera has to
behave differently.

**Diagnostic pointer if the replacement camera also fails.** A separate branch
(`tmtrg/pi-os-camera-issue`, since discarded) tested Raspberry Pi OS 64 Lite properly and reached
a sharper reading of the same "fails on both OSes" fact: a Pi OS retest runs the *vendor's own*
streaming-start sequence, validated daily on millions of boards, and it produces the same
silence — zero packets, zero errors, and a zero-byte capture from the IMX708's on-chip colour-bar
generator. That supplies the premise the zero-counter evidence was always missing: with
known-good software commanding the sensor, "nothing arrives at the receiver" can no longer be
explained by a transmitter that was never started, which points the fault at the Pi 5's **RP1
CSI-2 receive path** — the one part shared across every eliminated sensor, cable and port. We are
**not** acting on that now (the plan is a different camera, not a board RMA), but if the new
camera also captures nothing on this board, the RP1 receive path is the first thing to suspect,
and the identical failure on the vendor OS is the warranty evidence.

**Impact — still blocked, now deferred rather than active:** vision-guided obstacle avoidance,
the autonomous observation loop, streaming camera frames as Arrow buffers, and the
responsiveness comparison that MASTERPLAN makes the definition of success all remain blocked,
now waiting on a different camera rather than on debugging this one. The `camera` node needs no
change. Moved the corresponding PENDING items into the deferred section, gated on new hardware.

Plain-language summary: we've given up on getting the current Pi Camera to produce pictures — it
refuses to work under both operating systems we tried, so it isn't an Ubuntu quirk after all.
We're not buying more parts for this camera; instead we'll try a different kind of camera later.
Everything that needed the robot to see — avoiding obstacles, looking around on its own, and the
head-to-head speed test that was meant to prove the whole project worthwhile — is on hold until
that new camera arrives.

---

## 2026-08-18 — Crab walking verified: the owner ran `./run.sh crabwalk` themselves and confirmed it works

The lateral gait went from request to verified in one pass: the graph walks sideways right
then left back to its start using the gait engine's x=±35 stride (a true lateral gait, not a
turn), with the same offline frame-by-frame reach validation as the other locomotion graphs —
which rejects `narrow` sideways (79.8mm minimum reach against the 90mm hard limit, its third
consistent rejection across forward, turning was fine, and lateral) and clears every other
stance. The owner ran the test themselves and confirmed the robot crab-walks; no node
processes were left behind. Options recorded in the node header: stance, cycles, rounds,
speed (`PIBOT_CRABWALK_*`).

With this, every locomotion mode the gait engine offers is exercised through the dora graph
and verified on hardware in a single day: forward/backward walking (in two footprints),
closed-loop turning to ±0.5°, and now lateral crab walking.

---

## 2026-08-18 — The full 360° turn: 15 gait cycles, 0.5° residual, and the flat-battery-era milestone finally cleared

`PIBOT_TURN_CLOSED_LOOP=1 PIBOT_TURN_DEGREES=360 ./run.sh turn`, owner watching and
confirming. Fourteen cycles at angle 8 (remarkably consistent: −24.3° to −26.1° each), then
one fine-trim cycle at angle 2 (−5.1°) to land at −359.5° integrated — **residual 0.5° against
a 5° tolerance, 47 seconds of motion**. The battery never dropped below 6.47V under sustained
locomotion, comfortably above the 6.0V floor with no override — the same manoeuvre that on
2026-08-16 sagged the flat pack to 5.00V and aborted at 23 cycles.

This closes the "complete 360° turn" milestone the flat-battery blocker named, and it is a
better version than the one planned then: the open-loop plan needed a measured deg-per-cycle
figure and dead reckoning over ~47 commanded cycles; the closed-loop turn needed no
calibration constant at all and used 15 real cycles. The adaptive estimate settled at
2.9°/unit (~23°/cycle at angle 8) — drifting down from 3.3 earlier in the evening as the pack
and surface vary, which is exactly the variation closing the loop exists to absorb.

---

## 2026-08-18 — Stance-aware walking verified on hardware; the run also caught and fixed a stance-transition bug its own test had been hiding

`./run.sh stancewalk` ran live twice, owner watching, and the footprint-carries-into-the-gait
mechanism is confirmed: the robot walked 3 cycles forward and 3 back in `neutral`, then in
`wide` (spread 1.12, leg reach 174mm), returning roughly to its start each time. The `narrow`
skip fired exactly as designed — the offline frame-by-frame gait check rejected it live with
"mid-gait leg reach 88.7mm below 98.0mm" before any servo moved. Battery bottomed at 6.35V
under walking load, above the 6.0V floor; the ADC fix held throughout. The owner confirmed
both runs looked right.

**Fix: the geometry drift check refused any stance change away from a spread stance.**
`stances.verify_against()` compared live `Control.body_points` against the stock
BASE_FOOTPRINT — but after `wide` is applied, body_points legitimately holds the 1.12-scaled
values, so the return to `neutral` was refused as "drift" and the first run ended with the
robot still in wide. The hardware node now remembers the footprint it last applied and the
drift check compares against that. Verified live in a second run: wide → walk → **neutral
re-applied cleanly** (reach back to 147mm stock), 5/5 steps ok.

**Fix: a refused stance reported as success.** That first failing run summarised "8/8 steps
ok" around the refusal, a two-part reporting hole: the hardware node set the `refused` flag
only for battery-gate refusals (apply_stance failures lived only in the text), and the test
nodes' fallback text match looked for "FAILED"/"rejected" but not "refused". `apply_stance`
now returns (ok, text) with the flag set from it, and both stance test nodes match "refused"
too. The lesson is the project's founding one, resurfacing in miniature: a failure that only
exists as prose in a success-shaped message is a silent failure.

---

## 2026-08-18 — Closed-loop turning verified on hardware: 90° reached with a 1.5° residual in four gait cycles

Second live run of `PIBOT_TURN_CLOSED_LOOP=1 PIBOT_TURN_DEGREES=90 ./run.sh turn`, with both
fixes from the first run in place. The robot turned 90° to the right and stopped; the owner
confirmed the result. The per-cycle log tells the whole story: three cycles at angle 8
(measured −23.5°, −24.4°, −25.2° of integrated yaw), then the planner scaled the last cycle
down to angle 5 for the remaining 16.9° (measured −15.5°), finishing at −88.5° integrated —
**residual 1.5° against a 5° tolerance, four gait cycles, ~14 s of motion, no oscillation and
no overshoot**. Servos relaxed cleanly at the end of the graph.

What this verifies beyond the headline: the ADC stable-read fix held (a forced battery read
before every cycle, none of them hung; the pack read 6.59–7.29 V under load, above the 6.0 V
floor, incidentally the first loaded reading since the pack was charged); the gyro sign
convention was learned correctly on the first cycle (right turn reads negative on this
mounting, `sense=-1`); and the adaptive per-angle-unit estimate pulled the 4.5°/unit seed down
to a measured 3.3°/unit (~26°/cycle at angle 8 on this floor) and planned correctly with it.

Two observations for later, neither blocking: the calibration warned "robot was not still"
(15.9 deg/s gyro spread) because the servos had just driven to standing and were still
jittering — a short settle before calibrating would quiet it; and the closed-loop turn graph
does not terminate on its own (the hardware node ticks forever after the turn node exits), so
the run ends by Ctrl+C, which the owner did. Neither affected the result.

---

## 2026-08-18 — First live turn_to run: the robot turned, then oscillated and froze — two bugs found, both fixed, neither yet re-verified

The first hardware run of closed-loop turning (`PIBOT_TURN_CLOSED_LOOP=1 PIBOT_TURN_DEGREES=90
./run.sh turn`, battery 6.59V unloaded) rotated the robot to roughly the target and then kept
stepping back and forth across it without terminating; the run produced no result within its
240s budget, survived SIGINT, and had to be SIGKILLed, leaving the servos energised until they
were relaxed manually (`Servo().relax()` plus the power-disable pin). Node logs were lost with
the killed processes' stdout buffers — the per-segment logging added afterwards exists because
of exactly that blindness.

**Fix: the unbounded ADC stable-read loop.** `ADC._read_stable_byte()` in the forked driver
spun in `while True` until two consecutive I2C reads returned byte-identical values. Under
servo load the pack reading ripples continuously (6.53–7.18V across the three telemetry lines
before the turn), so identical consecutive reads can simply never arrive — and turn_to
force-reads the battery between segments, right when the servos are holding pose. Now capped
at 50 read pairs, returning the latest byte after that (1 LSB ≈ 59mV, ample for a battery
gate). This was a latent upstream bug: the same call sits on the telemetry tick path of every
graph, and it is precisely the silent-unbounded-loop class the MASTERPLAN names as the reason
this project exists — found in our own fork, not the thread it was looking for.

**Fix: the 36° turn quantum.** In `condition_monitor`, a turn command (x=0, y=0) takes the
single-shot branch: one `run_gait` cycle, queue cleared — `walk()`'s `steps` argument does not
multiply it. One cycle at angle=8 rotates the body ~36°, so turn_to's walk()-based segments
could never settle into a ±5° tolerance: every correction overshot by ~36° in the other
direction, which is the oscillation observed. This also reinterprets the 2026-08-16
measurement: "23 cycles → 180° → 7.8°/cycle" counted *commanded* cycles; the robot really ran
~5 single-shot cycles at ~36° each. turn_to now bypasses `walk()` and queues `CMD_MOVE`
directly with the angle scaled 1..8 to the remaining error (~4.5°..36° per cycle, adaptive
per-unit estimate), making the tolerance actually reachable, and logs every cycle: commanded
angle, measured rotation, integrated yaw, battery.

Neither fix has run on hardware yet; the turn_to PENDING item stays IN-FLIGHT with a re-run as
its exit condition.

---

## 2026-08-18 — Closed-loop turning and stance-aware walking implemented; the offline gait check already caught `narrow` as unwalkable

Two of the three deferred movement items are now code, awaiting hardware verification (both
carried as IN-FLIGHT in PENDING). No servo has moved under this code yet.

**Closed-loop turning.** The `hardware` node gained a `turn_to` tool (`nodes/hardware_node.py`):
a `YawTracker` thread samples the MPU6050 z-gyro raw register (one I2C word per sample, ~200Hz,
instead of the seven transactions `get_gyro_data()` costs — the sampler shares the bus with
~1800 servo writes/s mid-gait), calibrates its bias over 1s standing still, and integrates yaw
while short `walk(turn_*)` segments run. After each segment the plan is recomputed from
measured rotation: the deg/cycle estimate starts at the 7.8 measured on 2026-08-16 and blends
40% toward each segment's measurement, the gyro's sign convention is learned from the first
segment rather than assumed, overshoot turns back the other way, and two consecutive segments
under 1°/cycle abort the turn as "the gait is not rotating the body". Battery is force-read
between segments against the same floor gate as every motion tool. `turn_to` is registered in
`TOOL_OWNER`/`MOTION_TOOLS`, exposed to the LLM via `turn_tool_schema()` (15 tools now), and
`turn_node.py` gained `PIBOT_TURN_CLOSED_LOOP=1`, which replaces the segment scheduling with a
single `turn_to` call and a 300s step budget.

**Stances in gaits.** `nodes/stances.py` gained `simulate_gait_reach()` /
`validate_for_gait()`, a frame-by-frame offline replay of `run_gait`'s tripod arithmetic (same
phase structure, same 4×/8× factors, same 40mm lift) that returns worst-case leg reach over a
full cycle in a given stance and direction. The static check passes all 8 stances; the gait
check found that **walking forward in `narrow` (spread 0.90) undershoots the reach window —
88.7mm against the 90mm hard limit** — which on the robot would be silent per-frame no-ops from
`set_leg_angles()`, i.e. a stuttering dragged-foot gait with no error anywhere. Turning in
narrow is fine (116.2mm min); `wide` and `brace` walk with margin (139.0mm min, 204.3mm max).
A new `stance_walk_test_node.py` + `dataflow-stancewalk.yml` (`./run.sh stancewalk`) walks
forward/backward in narrow, neutral and wide, pre-validating each pair offline and skipping
rejects with the reason logged — `narrow` will demonstrate the skip path.

**Decision: the stance work stays in this project; the PENDING item asking where it should
live (2026-08-16) is closed.** Upstreaming is not available to this project by its own binding
rule — `/opt/pibot-hexapod` is never written — and the stance code is now more entangled here,
not less: `set_stance` is served by the hardware node, the schema lives in `nodes/common.py`,
and the new gait validation exists precisely because stances interact with this project's
walking tests. The module remains deliberately dora-free (pure math + stdlib), so if the
experiment is abandoned the owner can copy `nodes/stances.py` upstream as one file and expose
it through `src/actions.py` there. Revisit only if the MASTERPLAN verdict retires this project.

**Fix:** none — new capability, no defect corrected. The `narrow` finding is a defect *avoided*.

---

## 2026-08-18 — The flat-battery blocker is cleared at the owner's instruction; the pack has been charged

**Decision: the [PINNED 2026-08-16] flat-battery entry is removed from PENDING at the owner's
explicit instruction.** The pack has been charged since the 2026-08-16 measurements (6.94V
rest / 5.88–6.00V loaded then; 7.18–7.76V on the load rail during the 2026-08-18 sensors run,
unloaded). No reading under servo load has been taken yet — the first motion run will provide
one, and the `hardware` node's 6.0V floor gate remains enforced in code either way, so a pack
that sags under load still refuses motion on its own. The deliberate
`PIBOT_BATTERY_FLOOR=5.0` overrides used during the 2026-08-16 tests are no longer in effect;
motion runs from here use the default floor.

---

## 2026-08-18 — The sensors graph ran clean on the rebuilt venv: the Python 3.14 stack is verified against real hardware

First hardware run since the venv rebuild (CHANGELOG 2026-08-17, "Rebuilt the venv on Python
3.14"). With the robot electronics powered, `i2cdetect -y 1` showed all four expected devices
(0x40, 0x41 PCA9685; 0x48 ADS7830; 0x68 MPU6050), and `./run.sh sensors` ran for 45 seconds
under a SIGINT timeout with all four nodes (`hardware`, `ultrasonic`, `led`, `probe`) coming
up, exchanging telemetry, and exiting with status Success. No orphaned node processes
afterwards.

What this exercised that the import test could not: `rpi_ws281x` drove the SPI LED strip
("LED strip ready", emotions cycling on schedule) and `lgpio` ran the HC-SR04 (plausible
distances of 20.1–40.9 cm) — both under kernel 7.0.0-1016-raspi. Battery telemetry flowed
every 2 s: load rail 7.18–7.76 V, Pi rail a steady 8.06–8.12 V, with no servo load
(`PIBOT_NO_MOTION=1`). The graph ran from `/opt/pibot-dora` itself — worktrees carry no venv,
so hardware runs always launch from the main checkout.

The pack has evidently been charged since the flat-battery measurements of 2026-08-16
(these readings are ~1.2 V above the 5.88–6.00 V loaded readings then), but today's numbers
are unloaded and the pinned battery entry requires a reading under servo load before the
motion blockers clear, so that entry stands.

**Fix:** none needed — everything worked first try.

---

## 2026-08-17 — Merged the parallel camera investigation and retracted its hardware verdict: do not buy a cable, a camera module or a board

Merged `origin/main`, which carried a parallel investigation from 2026-08-16 that this branch
had not seen. Both histories are preserved intact and in date order; nothing was edited or
reverted.

**Decision: this supersedes the decision in the 2026-08-16 entry "Eliminated software as the
cause of the camera failure, down to the sensor's own test pattern".** That entry concluded the
fault is hardware and set a replacement order of **cable, then camera module, then board**.
**That order should not be acted on. Do not buy any of the three.** The camera captures
successfully on Raspberry Pi OS on this same board, with these same cables and both sensors
(this file, 2026-08-17), so no component in the chain is faulty.

**Why its decisive test did not decide.** The strongest evidence in that entry was the IMX708's
internal colour-bar generator (`test_pattern=1`), which synthesises frames on-chip and
transmits them over CSI-2 with the lens, exposure and gain path irrelevant — and which also
delivered zero bytes. The inference drawn was that the fault must lie in the sensor's MIPI
transmitter, the cable, or the RP1 receiver, since software had been removed from the picture.
The gap is that the test pattern still requires the sensor to be *streaming*: the on-chip
generator replaces the image source, not the transmitter, and not the software that commands
the transmitter on. If the Ubuntu stack fails somewhere in the streaming-start sequence, the
test pattern produces nothing for exactly the same reason a real scene does. The test isolates
the imaging path, which it genuinely did; it does not isolate software, which is what it was
credited with.

The same correction applies to the D-PHY error counters reported there, and to this branch's
own zero-packet reading — silence rather than corruption was read as proof that no valid
high-speed transitions arrive, when it is equally consistent with a transmitter never being
started. Two independent sessions reached the same wrong conclusion from the same class of
evidence, which is worth remembering: **absence of error counts is not evidence of a dead
receiver.**

What that entry got right and still stands: the control plane works end to end while the data
plane carries nothing; the media graph is complete and correctly formatted; the ISP, sensor
mode, driver state and permissions are all eliminated; and its narrower framing that the
earlier "defective RP1 receive path" claim was "stronger than its evidence" was the correct
instinct, applied one step short of far enough.

Of the two software avenues it left open, the kernel regression test has since been overtaken —
this branch crossed 6.17.0-1021 to 7.0.0-1016 and a full distro upgrade with no change. The
`imx708` overlay `link-frequency` variants (447/450/453 MHz) remain untried, but they exist to
rescue a marginal link, and Raspberry Pi OS works on this hardware at the default frequency, so
there is no marginal link to rescue.

---

## 2026-08-17 — Cleaned up the camera investigation: vendor libcamera removed, build trees and pre-upgrade backups deleted

Reverted the system to stock after the investigation closed, freeing about 1 GB.

`ninja uninstall` removed the vendor libcamera but reported six failures — the IPA modules are
produced by a custom signing script it does not track, leaving
`/usr/local/lib/aarch64-linux-gnu/libcamera/ipa/*.so.sign` and the Python bindings behind. Those
were removed by hand. `/usr/local` is now free of libcamera artefacts and
`ldd $(which rpicam-still)` resolves to the distro `libcamera0.7 0.7.0-1ubuntu2` again.

Deleted: `~/camera-build` (96 MB of libcamera, rpicam-apps and vendor kernel source),
`/opt/pibot-dora/venv-python3.13-broken` (408 MB), `/opt/pibot-backup-preupgrade-2026-08-17`
(472 MB) and the now-redundant `bcm2712-rpi-5-b.dtb.bak-iommu` from the boot partition.

**Kept deliberately:** the small text manifests from the pre-upgrade backup, moved to
`/opt/pibot-preupgrade-manifests` (56 KB) — `venv-freeze.txt`, `dpkg-selections.txt` and the
original `config.txt`, `cmdline.txt` and `autoboot.txt`. The bulk archives had little recovery
value once 26.04 was running, but the sensors graph still has not been run against hardware, so
the record of what was installed beforehand is worth its 56 KB until it has.

Verified after cleanup, not merely assumed: all sixteen venv imports resolve under Python
3.14.4, the project modules import, both boot slots read `good`, and both cameras still
enumerate. The stock DTB is restored on disk and applies at the next reboot; the running kernel
still has the IOMMU-stripped tree from the last test.

---

## 2026-08-17 — Removing the IOMMU from the CSI nodes changes nothing either; every reachable variable on Ubuntu is now exhausted

Tested the last configuration-level difference available: the `iommus` property on the CSI
nodes. The theory was that the receiver might be working correctly while DMA wrote frames to
unmapped IOVAs, which would produce exactly the dequeue timeout observed and would also explain
why replacing libcamera and the CFE driver changed nothing.

A `/delete-property/` overlay does not work for this — `dtc` compiles it to an empty
`__overlay__`, so the property survives. The DTB was therefore edited directly: decompiled,
the two `iommus = <0x40>` lines belonging to `csi@110000` and `csi@128000` removed by line
number after mapping every occurrence to its enclosing node, and recompiled. The seven other
`iommus` properties (DSI, VEC, DPI, codec, `pisp_be`, HVS) were left untouched; the resulting
blob was 78623 bytes against 78655, a 32-byte delta consistent with two properties and nothing
else.

Verified applied after reboot: `iommus` absent from both CSI nodes in `/proc/device-tree`, zero
`csi.*iommu` lines in `dmesg` where there were previously several, and both sensors still
enumerating. **Capture fails identically.** The stock DTB was restored from
`bcm2712-rpi-5-b.dtb.bak-iommu` and verified byte-identical; it takes effect on the next reboot.

**Decision: stop debugging the camera on Ubuntu.** The complete list of things eliminated by
direct test, on this board, is now: three ribbon cables, two sensor models, both CSI ports in
both orientations, a checklist reseat, the robot's entire electrical environment, a kernel A/B
within 6.17, a kernel major version bump to 7.0, a full distro release upgrade, the upstream CFE
driver, vendor libcamera 0.7.2 built from source, the vendor CFE driver built from the matching
`rpi-7.0.y` branch, a device tree verified property-for-property against vendor sources, and now
the CSI IOMMU binding. The camera works on Raspberry Pi OS on this same hardware.

Whatever the difference is, it lies in the parts of the Ubuntu kernel that cannot be swapped
piecemeal — the RP1 platform, clock and regulator code around the CFE — or in firmware. The
route to a working camera is Raspberry Pi OS, and that is now a project-direction decision
rather than a debugging one.

---

## 2026-08-17 — Vendor libcamera and the vendor CFE driver both built and installed, and the camera still fails: userspace and the CFE driver are eliminated

Followed a guide suggesting the Raspberry Pi forks be built from source rather than using the
distro packages, on the hypothesis that Ubuntu's **upstream** libcamera never fully commands the
sensor into streaming. That hypothesis was wrong, but testing it eliminated two large suspects.

**Confirmed first that Ubuntu ships upstream libcamera, not the Pi fork** — `libcamera0.7
0.7.0-1ubuntu2`, `Homepage: https://libcamera.org/`, maintained by Ubuntu Developers. Raspberry
Pi OS ships `github.com/raspberrypi/libcamera`. The IPA tuning data is present either way (62
JSONs under `/usr/share/libcamera/ipa/rpi/pisp`), so only handler code differed.

- **Built and installed the vendor libcamera 0.7.2** from `raspberrypi/libcamera` with
  `-Dpipelines=rpi/vc4,rpi/pisp -Dipas=rpi/vc4,rpi/pisp`, into `/usr/local` so the distro
  packages in `/usr/lib` stay intact. 273/273 objects, no errors. `ldconfig` prefers it and
  `ldd $(which rpicam-still)` confirms it loads from `/usr/local`. **Capture still times out.**
  The vendor library is verifiably the one running — the error moved from
  `pipeline_base.cpp:1357` to `:1372`.
- **Built and loaded the vendor CFE kernel driver** from `raspberrypi/linux` branch `rpi-7.0.y`,
  which matches this kernel's version exactly. It compiles cleanly out-of-tree against the
  installed 7.0.0-1016 headers, loads, binds both CSI devices, and registers the underscore node
  names libcamera expects. **Capture still times out.**

Worth recording: the vendor tree ships both `rp1_cfe` and `rp1-cfe` directories, the same pair
Ubuntu packages, and the vendor Makefile produces the identically-named
`rp1-cfe-downstream.ko`. But the `srcversion` values differ — `DD16F0DB007FD8A39FEEDF7` for the
vendor build against `15A9735CA80BF1C997A8620` for Ubuntu's — so Ubuntu is not shipping the
vendor source unmodified. That difference did not turn out to matter, since the pure vendor
build fails identically.

**Correction to a claim relied on all day.** The "zero packets and zero discards" reading came
from `CSI2_CH_DEBUG(n)` and `CSI2_CH_FE_FRAME_ID(n)`, which belong to the direct
`csi2 → csi2_chN` capture channels. libcamera routes `csi2 → pisp-fe` instead, so those
registers may legitimately read zero on a *working* system and may never have been measuring
what they were taken to measure. They were treated as decisive evidence of an absent MIPI
signal, and that interpretation is not safe. The observation that no frames arrive stands; the
inference about *where* the data stops does not.

**Decision: stop attributing this to any single replaceable component.** With vendor libcamera
and the vendor CFE driver both in place and the failure unchanged, the difference from a working
Raspberry Pi OS install lies in the rest of the Ubuntu kernel — the RP1 platform, clock, IOMMU
and regulator code around the CFE — or in firmware. None of that is swappable piecemeal. The
practical route to a working camera is Raspberry Pi OS.

The vendor libcamera remains installed in `/usr/local` and takes precedence over the distro
package. It is harmless and easily removed, but it means `rpicam-*` now runs vendor code.

---

## 2026-08-17 — Forcing the upstream RP1 CFE driver is a dead end; the fault is in Ubuntu's downstream driver or its DTB

Tried to route around the Ubuntu camera fault by swapping the kernel's two CFE drivers. Both
register a platform driver named `rp1-cfe`, so they cannot co-load; the downstream one was
unbound and removed, `rp1_cfe` (upstream) loaded, and the devices force-bound with
`driver_override` since the DTB carries the downstream compatible string.

**It probed cleanly** — all sixteen video nodes registered across both blocks, and both sensors
re-attached (`ov5647 10-0036` on `/dev/media2`, `imx708` on `/dev/media3`). So the upstream
driver is functional against this device tree. It is nonetheless unusable here, for two
independent reasons:

- **libcamera cannot see it.** `rpicam-still` reports `no cameras available`. The upstream driver
  names its entities `rp1-cfe-csi2-ch0` where the downstream one uses `rp1-cfe-csi2_ch0`, and
  Ubuntu's libcamera 0.7.0 pipeline handler matches the downstream topology. Forcing the generic
  handler with `LIBCAMERA_PIPELINES_MATCH_LIST=simple` does not help either — it also finds no
  cameras, since it expects a direct sensor-to-video-node path and cannot drive the PiSP front
  end. The only Raspberry Pi handlers built into this libcamera are `rpi/pisp` and `rpi/vc4`,
  both of which target the downstream topology, so the two halves cannot be mixed.
- **Raw V4L2 capture cannot start either.** With formats matched end to end
  (`SGBRG10_1X10/1296x972` on sensor pad0, `csi2` pad0 and pad1) and the link enabled,
  `VIDIOC_STREAMON` returns `EPIPE` and the kernel logs `Failed to start media pipeline: -32`.
  The entity reports `0 routes` and `media-ctl --set-routing` returns `EOPNOTSUPP`, so the
  multiplexed-stream setup this driver expects cannot be configured with the shipped tooling.

**Decision: stop trying to fix this from configuration.** The remaining difference between the
working Raspberry Pi OS install and the broken Ubuntu one is Ubuntu's backport of the downstream
CFE driver, or the DTB it is paired with. Neither is reachable from `config.txt`, a module
parameter or a driver override. The next diagnostic step that would actually discriminate is a
side-by-side of the `csi@110000` node and stack versions taken from the working Raspberry Pi OS
card; the next *fix* is either the vendor DTB booted through the tryboot slot, or moving the
robot to Raspberry Pi OS.

The system is left with the upstream driver bound; a reboot restores the shipped downstream
driver, since nothing was made persistent.

---

## 2026-08-17 — The camera works on Raspberry Pi OS: the board is fine, the RMA conclusion was wrong, and the fault is in Ubuntu's camera stack

The owner booted Raspberry Pi OS on this Pi and the camera captured. **This supersedes every
conclusion reached earlier today and on 2026-08-16 that the RP1 CSI-2 receiver is defective and
the board needs replacing. Do not RMA the Pi.** The hardware is good — sensors, ribbons, ports
and receiver alike.

**The reasoning error, stated plainly so it is not repeated.** The whole hardware case rested on
the CSI-2 counters reading zero packets *and* zero discards, argued as "a marginal connection
corrupts and increments discards, so silence means the receiver is dead." The premise is sound;
the conclusion does not follow. Zero counters prove only that **no data arrived**. That is
equally consistent with the receiver never being armed, or the sensor never being commanded onto
the high-speed lanes — a transmitter that never transmits and a receiver that cannot receive
produce identical register state. Upstream's OV5647 `0x0100` read-back (one register, one
sensor) was treated as closing that gap and does not. The counters were the strongest-looking
evidence in the investigation and they were load-bearing for a claim they could not support.

Diagnosis so far on the Ubuntu side, all of which looks *correct*, which is what makes this
awkward:

- **Sensor endpoints are right.** `data-lanes = <1 2>`, `clock-lanes = <0>`,
  `clock-noncontinuous`, `link-frequencies` 297 MHz (OV5647) and 450 MHz (IMX708).
- **Power sequencing works.** Sampled during an active capture, not at idle: `cam0_reg` goes
  `use 0 → 1` with `10-0036-avdd` enabled, and `cam1_reg` `1 → 2` with `11-001a-vana1` enabled.
  The analog rails do come up. An earlier idle sample showing `use 0` was misleading and nearly
  became a false lead.
- **Link rates are programmed correctly** — `dmesg` reports "Using a link rate of 437 Mbps" on
  `1f00110000.csi` and "900 Mbps" on `1f00128000.csi`.
- **The media pipeline is fully linked with matching formats** end to end:
  sensor → `csi2` → `pisp-fe` `[ENABLED]` → `rp1-cfe-fe_image0` `[ENABLED]`.
- **CSI node register maps are internally consistent** between both blocks (`+0x0000`, `+0x4000`,
  `+0x10000`, `+0x14000` from each base).

One structural finding worth keeping: this kernel ships **two** CFE drivers —
`rp1_cfe/rp1-cfe-downstream.ko` (loaded, binds `raspberrypi,rp1-cfe`) and
`rp1-cfe/rp1-cfe.ko` (never loaded, binds `raspberrypi,rp1-cfe-upstream`). Only a downstream
DTB is shipped, so selecting the other driver is not a configuration change — it needs a device
tree that does not exist on this system.

The failure occurs on **both** Ubuntu kernels (6.17.0-1021 and 7.0.0-1016) and not on Raspberry
Pi OS, so it is Ubuntu-specific rather than a kernel-version regression. The 26.04 upgrade was
therefore not wasted — it eliminated kernel version as the variable and sharpened the fault to
Ubuntu's packaging of the camera stack — but the "software is eliminated" conclusion it was
used to justify was wrong, and wrong in the direction of the more expensive decision.

**Decision:** the RMA is cancelled. The remaining choice is between running the robot on
Raspberry Pi OS, which is proven working on this hardware and is what `picamera2` is developed
against, and staying on Ubuntu 26.04 with a non-functional camera pending an upstream fix.
Tracked in PENDING; not decided here because it is a project-direction call.

---

## 2026-08-17 — Camera still dead with the Pi detached from the robot, eliminating the robot's power and wiring

Retested both cameras with the Pi unplugged from the robot entirely — no servo rail, no PCA9685
boards, no IMU, no ADC, nothing on the I2C bus, and the Pi drawing from its own supply. This was
the last shared-environment variable left: a noisy 6V servo rail or ground loop through the
robot chassis is a plausible way to disturb high-speed MIPI signalling while leaving low-speed
I2C intact, and it had never been tested in isolation.

It changes nothing. Both sensors enumerate on kernel 7.0.0-1016 and libcamera 0.7.0, both
captures fail with `Dequeue timer of 1000000.00us has expired!` and `Camera frontend has timed
out!`, no JPEG is produced, and every `CSI2_DISCARDS_*`, `CSI2_CH_DEBUG(n)` and
`CSI2_CH_FE_FRAME_ID(n)` register on both CSI blocks reads `0x00000000` with a capture armed.

**Decision: the RMA case is now complete and no further diagnosis is worth doing.** The full
elimination list across this project and upstream is three ribbon cables, two sensor models on
both CSI ports, a checklist reseat, a kernel A/B within 6.17, a kernel major version bump to
7.0, libcamera 0.5 → 0.7, rpicam-apps 1.7 → 1.11, a distro release upgrade, and now the robot's
entire electrical environment. The fault has not moved once. A Raspberry Pi OS boot remains
available purely as vendor paperwork.

---

## 2026-08-17 — Rebuilt the venv on Python 3.14, replacing the kitchen-sink requirements with the dependencies the code actually imports

The 26.04 upgrade moved the interpreter to 3.14.4 while the venv's site-packages stayed at
`lib/python3.13`, so every pip-installed package vanished. Rebuilt it. The old tree was
**moved, not deleted**, to `/opt/pibot-dora/venv-python3.13-broken`.

**Decision: install what the code imports, not what `requirements.txt` lists.** That file is an
inherited kitchen-sink freeze of 90+ pins carrying `anthropic`, `google-generativeai`, `groq`,
`ollama`, the Adafruit CircuitPython stack and `luma.oled` — none of which any module in
`nodes/` or `src/` imports. Reinstalling it wholesale on 3.14 would have meant source builds of
`scipy`, `opencv-python` and `RPi.GPIO` for packages the robot never loads. The actual import
surface, taken from the source rather than the freeze, is 19 third-party modules, and over half
are already provided by 26.04 system packages and reached through `--system-site-packages`:
`numpy` 2.3.5, `yaml`, `pyaudio`, `spidev`, `PIL`, `cv2`, `lgpio`, `libcamera`, `picamera2`.

Pip installed only the remainder. `dora-rs` and `dora-rs-cli` were pinned to **0.5.0** because
the node API is version-coupled; everything else was left unpinned so pip could pick builds
that exist for 3.14 rather than forcing pins produced for 3.13. `dora_rs` ships a `cp37-abi3`
wheel and installed unchanged. Nothing needed compiling — `webrtcvad` and `rpi_ws281x` both
resolved to wheels — and no build errors occurred.

Versions that moved as a result, worth knowing before debugging anything: **openai 2.15.0 →
3.1.0** (a major bump), `pvporcupine` 4.0.1 → 4.0.3, `python-dotenv` 1.2.1 → 1.2.3, `smbus2`
0.6.0 → 0.6.1, `numpy` 2.2.6 → 2.3.5 (now the system package). The openai major was checked
rather than assumed: the project only touches `OpenAI(api_key=...)`,
`client.chat.completions.create`, `audio.transcriptions.create` and `audio.speech.create`, and
all four are present and unchanged in 3.x.

**Verified:** all 19 third-party imports resolve, and all 16 project modules
(`src.control`, `src.actions`, `src.camera`, `src.voice`, `src.llm_handler`, `nodes.common`,
`nodes.stances` and the rest) import cleanly under 3.14.4.

**Not verified, and deliberately not claimed:** nothing was exercised against live hardware. The
I2C bus scans completely empty — no device at 0x40, 0x41, 0x48 or 0x68 — because the robot
electronics are unpowered. The bus itself is healthy (`/dev/i2c-1` present, `i2c_brcmstb`
loaded), so this is not upgrade damage, but `ADC().read_battery_voltage()` raises
`OSError: [Errno 121] Remote I/O error` and no graph has been run. The sensors graph is the
outstanding check; it is in PENDING.

---

## 2026-08-17 — Camera fails identically on Ubuntu 26.04 with kernel 7.0 and libcamera 0.7, closing the software question for good

Rebooted onto the upgraded stack and reran the camera tests. The result is the predicted one,
and the prediction being right is worth less than the measurement now existing.

Verified the test was actually valid before trusting it: `uname -r` reports
**7.0.0-1016-raspi**, the tryboot promotion completed (`current/state` = `good`, `new/` rotated
into `old/`, both `piboot-try` units inactive having done their work), and the release is
26.04 LTS. This is the check that matters, because a promotion failure would have left the old
kernel running and produced a meaningless "retest".

Both sensors still enumerate perfectly on libcamera 0.7.0 / rpicam-apps 1.11.1, with full mode
lists — the OV5647 on CAM0 now reporting 640x480 at 62.50 fps where 0.5.0 said 58.92, so the
new stack is demonstrably doing its own thing and not a cached repeat. Then both fail exactly
as before: `Dequeue timer of 1000000.00us has expired!`, `Camera frontend has timed out!`, no
JPEG, on `/dev/video4` and `/dev/video12` respectively.

The register counters are unchanged from the 6.17 baseline — every `CSI2_DISCARDS_*`,
`CSI2_CH_DEBUG(n)` and `CSI2_CH_FE_FRAME_ID(n)` reads `0x00000000` on both CSI blocks with
both cameras armed. Zero packets and zero discards, again.

**Decision: the software stack is eliminated, and the RMA is the remaining action.** What has
now been crossed is a full kernel major version (6.17 → 7.0), a libcamera minor (0.5 → 0.7),
rpicam-apps 1.7 → 1.11 and a distro release, on top of upstream's earlier elimination of three
ribbons, two sensor models, both CSI ports, a checklist reseat and a kernel A/B. Every variable
that can be changed without buying hardware has been changed. The fault does not move.

This also supersedes the caution recorded earlier today that the Ubuntu stack could be a
common-mode cause — it was a fair hypothesis on the evidence available at the time and it has
now been tested directly rather than argued away, which is why the upgrade was worth doing even
though the outcome did not change. The remaining Raspberry Pi OS boot is warranty paperwork,
not diagnosis, and is optional if the vendor does not ask for it.

**Still open:** the owner's recollection that the camera once worked, which no record supports.
It stays in PENDING because it is the only evidence pointing away from a hardware fault, and
because if it refers to *this* board then something physical changed and that would matter.

---

## 2026-08-17 — Upgraded the Pi to Ubuntu 26.04 LTS to eliminate the camera software stack, and found why the kernel had been frozen

Upgraded 25.10 "questing" to 26.04 LTS "resolute" at the owner's explicit instruction, to rule
out the software stack in the camera investigation rather than argue it away. 25.10 had also
gone end of life, so the box was unpatched regardless.

`do-release-upgrade` refused to start at first, reporting only "Please install all available
updates for your release before upgrading" — **186 pending security updates** had to be
installed first. That refusal is quiet and easy to misread as a broken command; the real error
only appears when the tool is run with stdin attached to something it can inspect.

**Fix (likely the long-standing one):** the A/B boot rotation was blocked by a stale directory.
Promotion runs `mv /boot/firmware/new /boot/firmware/old`, which silently moves `new` *inside*
`old/` as `old/new` when `old/` already exists, instead of rotating the slots. `old/` did
exist, holding a kernel byte-identical to `current/`. It was moved off the partition to free
space for the upgrade, which incidentally cleared the blockage. This is a credible root cause
for the frozen-kernel behaviour recorded upstream on 2026-08-16, where apt's kernel upgrades
never reached `/boot/firmware/current/` and the Pi booted a months-old snapshot.

Versions crossed, which is a far larger change than the 6.17.0-1003 → 1021 A/B upstream already
tested: kernel 6.17.0-1021 → **7.0.0-1016-raspi**, libcamera 0.5.0 → **0.7.0**, rpicam-apps
1.7.0 → **1.11.1**, picamera2 0.3.23, Python 3.13.7 → **3.14.4**. The boot tooling changed too:
`flash-kernel` was removed in favour of `flash-kernel-piboot` plus `piboot-try`, whose
`piboot-try-reboot` and `piboot-try-validate` units automate the tryboot-and-promote cycle.
The next restart is therefore a **double** reboot by design.

**Correction to an earlier claim in this session:** `archive.ubuntu.com` was flagged as wrong
for arm64 when the upgrader rewrote the sources away from `ports.ubuntu.com`. That was checked
before acting and is false — `archive.ubuntu.com` returns HTTP 200 for
`dists/resolute/main/binary-arm64/Packages.gz`. The archives have been consolidated. No change
was made and nothing was broken.

**Not yet verified, and the whole point of the exercise:** the camera has not been retested. The
Pi is still running the old 6.17.0-1021 kernel because the reboot has not happened. The
prediction on record is that it fails identically — zero MIPI packets and zero discards is a
statement about electrical activity, not software — but the test is the test.

**Known breakage:** the project venv is dead. Its site-packages is `lib/python3.13` while the
interpreter is now 3.14.4, so `openai`, `dotenv` and `smbus2` no longer import (`yaml` and
`numpy` still resolve only via `--system-site-packages`). Rebuild from `requirements.txt`, not
from the `venv-freeze.txt` backup, which is polluted with system packages. Expect friction on
the `scipy`, `opencv-python`, `numpy` and `RPi.GPIO` pins under 3.14.

Pre-upgrade backups are in `/opt/pibot-backup-preupgrade-2026-08-17`: a 290 MB tarball of the
whole boot partition, `/etc`, `dpkg --get-selections`, the venv freeze, and the original
`config.txt` / `cmdline.txt` / `autoboot.txt`, plus the displaced `old/` slot.

---

## 2026-08-17 — Register-level check confirms zero MIPI packets on both CSI blocks, retiring the software hypothesis

Followed up the dual-port test by reading the RP1 CSI-2 receiver counters directly, with a
capture armed, for both cameras against both CSI blocks
(`/sys/kernel/debug/rp1-cfe:1f00110000.csi/csi2_regs` and `…:1f00128000.csi/…`). Every counter
reads `0x00000000` in all four combinations: `CSI2_DISCARDS_OVERFLOW`, `_INACTIVE`,
`_UNMATCHED` and `_LEN_LIMIT`, plus `CSI2_CH_DEBUG(0..3)` and `CSI2_CH_FE_FRAME_ID(0..3)`.
Nothing arrives on the high-speed data lanes at all, while I2C over the same ribbon works
perfectly — both sensors enumerate with complete mode lists.

Zero *discards* alongside zero packets is the informative part. A skewed or partially seated
ribbon that mates some data-lane contacts intermittently produces corruption, and the discard
counters exist precisely to record it; they would climb. Reading zero across every counter
means no electrical activity whatsoever on the lanes, not degraded activity.

**Decision:** this supersedes the "cannot separate defective silicon from the Ubuntu 25.10
camera stack" line in the entry below, written earlier the same day before the upstream record
was consulted. The upstream project ran the discriminating test already
(`/opt/pibot-hexapod/docs/CHANGELOG.md`, 2026-08-16): the Pi was found booting a frozen
6.17.0-1003 kernel snapshot, was upgraded to 6.17.0-1021 along with DTBs, overlays, bootloader
EEPROM and linux-firmware, and **the camera failure was byte-identical before and after**. A
kernel-version regression is therefore eliminated by direct A/B on this hardware, not by
argument. Both kernels remain installed here (`/boot/vmlinuz-6.17.0-1003-raspi` and `-1021`),
so the test is repeatable if ever doubted. Upstream also eliminated three ribbons, two cameras,
both ports and a checklist reseat by direct swap.

**Open question, and the only evidence pointing the other way:** the owner recalls the camera
working at some point in the past, which the upstream record contradicts outright — it states
the camera "has never delivered a frame on this robot". Worth pinning down whether the memory
is of a different Pi or a pre-assembly bench test, because if a frame ever arrived on *this*
board then something physical changed and the RMA reasoning needs revisiting. Tracked in
PENDING.

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

Two software avenues remain untested. Both need a reboot of a headless robot, which must not be
done with the servo rail energised, so neither was taken unilaterally. Given the test-pattern
result the expected value of both is low; they are recorded as options, not a plan, and are the
*only* software steps left worth taking.

1. **Kernel regression test.** 6.17.0-1003-raspi is still installed, and
   `/boot/firmware/old/vmlinuz.bak-1003` was confirmed by string inspection to be that kernel.
   Ubuntu's `rp1-cfe` is a non-standard backport that does emit a kernel WARNING (below), so a
   regression in 6.17.0-1021 is not absurd. `flash-kernel` makes the switch reversible:
   `sudo flash-kernel --force 6.17.0-1003-raspi`, reboot, test, and revert with
   `sudo flash-kernel --force 6.17.0-1021-raspi`.
2. **Overlay link parameters.** The `imx708` overlay accepts `link-frequency` of 450000000
   (default), 447000000 or 453000000, plus `media-controller=off` and `vcm=off`. These exist
   for interference mitigation and would be set with `camera_auto_detect=0` and an explicit
   `dtoverlay=imx708,link-frequency=<hz>`. A 0.7% frequency shift plausibly rescues a
   marginal link; it does not explain a link delivering nothing at all.

Also eliminated: the driver's `track_csi2_errors=1` module parameter was enabled and a capture
run under it reported **no CSI-2 errors of any kind** — confirming silence rather than
corruption. The only other module parameters are `imx708`'s `qbc_adjust` (Bayer line
correction, cosmetic) and a debug flag; none affect lane count or timing. `apt` offers no newer
kernel, libcamera, rpicam or raspi-firmware package, so there is no update to apply.

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
