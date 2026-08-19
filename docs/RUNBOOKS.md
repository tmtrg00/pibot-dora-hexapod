# RUNBOOKS — PiBot-Dora

Procedures, tools and credentials. Everything needed for only one kind of work lives here
rather than in `AGENTS.md`, which loads every session. Open work belongs in PENDING; history
belongs in CHANGELOG.

Contents: §1 running graphs · §2 how nodes are launched · §3 battery · §4 stances ·
§5 movement · §6 camera · §7 GPIO and I2C · §8 credentials · §9 the fork · §10 troubleshooting

---

## §1 Running graphs

All graphs are launched through `./run.sh`, which cleans up orphaned nodes on entry and exit.

```bash
./run.sh sensors      # telemetry + LED. NO motion, no mic, no API spend. Start here.
./run.sh stance       # cycle named stances. Moves legs; does not travel.
./run.sh motion       # scripted pose sequence. Moves legs; does not travel.
./run.sh attitude     # roll/pitch axis check. Tilts on the spot; does not travel.
./run.sh idlereset    # stance returns to neutral once idle. Does not travel.
./run.sh turn         # rotate in place. LOCOMOTION — the robot travels.
./run.sh smoothturn   # turn_to through 90/180/20°, back to start. LOCOMOTION.
./run.sh straightwalk # gyro heading hold vs an uncorrected baseline. LOCOMOTION.
./run.sh odometry     # same cycles at three speeds. LOCOMOTION.
./run.sh crabwalk     # sideways right then left. LOCOMOTION.
./run.sh stancewalk   # walk in each stance. LOCOMOTION.
./run.sh approach     # walk up to an obstacle and stop. LOCOMOTION.
./run.sh camera       # capture attempts only.
./run.sh              # full autonomous graph. Needs .env; makes API calls.
./run.sh <file.yml>   # any other dataflow
```

Ctrl+C stops cleanly. After an unclean exit:

```bash
./stop.sh
```

Node logs are streamed to the terminal and also written per run to `out/<dataflow-id>/`:

```bash
ls -1t out/ | head -3
grep -E "\[INFO\]|\[ERROR\]" out/<id>/log_<node>.txt
```

That directory is how to review a run someone else did — it survives the terminal.

---

## §2 How nodes are launched

Every node in every dataflow has `path: bin/py` with the script in `args`. This is not
decoration. dora resolves a node whose `args` ends in `.py` using the **system** python and
ignores whatever interpreter `path` names, which misses this project's venv entirely. Setting
`PATH` or `VIRTUAL_ENV` does not change it; this was tested. Naming `bin/py` as the `path`
makes dora exec it as a plain binary, and it execs the venv interpreter in turn.

Consequence: `dora version` reports `Python dora-rs version: not found`, because the CLI probes
the system python. Harmless and expected.

A new node must:

1. `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` before importing `common`
2. call `common.bootstrap()` before importing anything from `src`
3. **end with `if __name__ == "__main__": main()`** — omitting it makes the node exit 0 without
   connecting, which dora reports as "exit status Success" while failing the whole graph

---

## §3 Battery — the binding pre-flight

```bash
./venv/bin/python -c "from src.adc import ADC; print(ADC().read_battery_voltage())"
```

Returns `(load_V, pi_V)`. **Read it with servo power already on.** The unloaded reading is about
a volt optimistic and that gap is the entire risk: measured 2026-08-16, a pack reading 6.94V at
rest read 5.88–6.00V under load.

Floor is **6.0V**. Below it the servos brown out mid-lift — the stand-up ankle phase pulls peak
current across all six legs at once — and the robot drops onto its own legs; deep-discharging
the 2S pack can kill it.

The `hardware` node enforces this itself. It refuses motion tools below the floor, refuses when
the reading is stale or unavailable, refuses when the gait thread has died, and refuses to
construct `Control()` at all — which matters because instantiating it drives the legs to the
standing pose as a side effect. Relaxing servos is always permitted since it reduces draw.

Overrides, both deliberate:

```bash
PIBOT_BATTERY_FLOOR=5.0 ./run.sh motion   # lower the floor (bench / regulated supply)
PIBOT_NO_MOTION=1 ./run.sh <graph>        # ADC only; Control() never constructed
```

Observed sag under load, for calibration: posing dips to ~5.06V briefly; sustained gait cycles
hold ~5.0–5.5V and recover ~0.5V between segments.

---

## §4 Stances

```bash
./venv/bin/python nodes/stances.py                    # validate all poses, no hardware needed
./run.sh stance                                       # cycle every stance
PIBOT_STANCE_ONLY=wide ./run.sh stance                # just one
PIBOT_STANCE_HOLD=5 ./run.sh stance                   # hold each 5s
```

Eight stances: `neutral`, `crouch`, `tall`, `wide`, `narrow`, `brace`, `alert`, `lean_forward`.
A stance combines foot spread, ride height and tilt.

**Adding one** — edit `STANCES` in `nodes/stances.py`, then run the validator above before
going near the robot. Two constraints it enforces:

- Leg reach must stay inside 90–248 mm, with an 8 mm margin held back. Outside that,
  `set_leg_angles()` silently does nothing.
- **Spread and tilt cannot be combined.** Attitude commands rebuild foot positions from a
  hardcoded footprint, discarding any spread. The validator rejects the combination.

`BASE_FOOTPRINT` and `LEG_TRANSFORMS` in `stances.py` mirror `src/control.py`.
`verify_against()` cross-checks them against a live `Control` at runtime, so if the geometry is
ever edited in one place the stance will refuse rather than move wrongly.

---

## §5 Movement

```bash
./run.sh motion                          # stand, roll, pitch, head, relax
PIBOT_MOTION_WALK=1 ./run.sh motion      # adds a short walk — the robot travels
./run.sh turn                            # rotate, default target 360°
PIBOT_TURN_DEGREES=90 ./run.sh turn      # a specific rotation
PIBOT_TURN_CYCLES=5 ./run.sh turn        # calibration mode: run N cycles, measure the result
```

`walk(turn_*)` is the open-loop primitive; `turn_to` is the accurate one, closed on the gyro,
and runs the whole rotation as ONE continuous gait command whose steering angle is re-trimmed
as the robot turns. It measures its own degrees-per-angle-unit as it goes (seeded at 3.3,
measured 2026-08-19) so the surface, the stance and the battery stop mattering.

```bash
./run.sh smoothturn                          # 90/180/20° turns, back to the start heading
PIBOT_SMOOTHTURN_TOLERANCE=3 ./run.sh smoothturn
PIBOT_TURN_CLOSED_LOOP=1 PIBOT_TURN_DEGREES=90 ./run.sh turn    # a single turn_to
```

Turning is locomotion, not posing: full gait cycles draw sustained current. Every movement
graph aborts to a stand-and-relax tail below its `*_ABORT_V` (default 4.9V) rather than
collapsing mid-stride.

**Which way the gyro counts is measured once and remembered** in `data/gyro_sense.json`
(untracked). `turn_to` and `walk_straight` both learn it the first time they move and save it;
`PIBOT_GYRO_YAW_SIGN=+1` or `-1` overrides it. Delete the file to force a re-measure — do that
if the IMU is ever remounted, because every heading number depends on it.

```bash
cat data/gyro_sense.json          # {"yaw_sign": -1.0, "learned_from": "turn_to", ...}
rm data/gyro_sense.json           # re-learn on the next turn or straight walk
```

### Walking in a straight line

`walk_straight` holds heading on the gyro; `walk` does not. Measured 2026-08-19 over 6 cycles
each way: 14.9° of drift uncorrected against 3.5° corrected.

```bash
./run.sh straightwalk                        # corrected vs uncorrected, prints a verdict
PIBOT_HEADING_GAIN=0.2 ./run.sh straightwalk # firmer steering
```

### Walking a measured distance

Distance comes from counting gait cycles, not from timing them — `Control.gait_cycles`. The
old timing estimate was wrong by 3.2x at every speed, so a walk ran about a third of the
cycles it claimed. `./run.sh odometry` walks the same cycle count at three speeds; the
distances should be equal.

### Walking up to something

`approach` walks until a given distance from whatever is in front, watching the ultrasonic
sensor. It needs the ultrasonic node in the graph — the hardware node subscribes to its
`distance` output. It refuses to move before it has seen a reading, and stops if readings stop.

```bash
./run.sh approach                             # obstacle 1-1.5m ahead
PIBOT_APPROACH_STOP_CM=30 ./run.sh approach
```

---

## §6 Camera

```bash
./run.sh camera                          # three capture attempts
libcamera-hello --list-cameras           # does the sensor enumerate at all?
```

The camera is currently non-functional — see PENDING. Distinguish the two failure modes:

- **Sensor absent** — `list-cameras` shows nothing. Cable or connector.
- **Frames absent** — sensor enumerates, libcamera opens and starts it, then
  `Camera frontend has timed out` on the first dequeue. This is the current state, and it is
  the defective RP1 CSI-2 receive path.

A successful `initialize()` means nothing; only a completed capture proves the camera works.
The node fails each capture after `PIBOT_CAPTURE_TIMEOUT` (default 8s) and marks itself dead
after `PIBOT_CAPTURE_MAX_TIMEOUTS` (default 3), because each timeout abandons a thread stuck in
libcamera that cannot be killed from Python.

---

## §7 GPIO and I2C

**Use `lgpio` only.** Never `gpiozero` or `RPi.GPIO` on the Pi 5 — they do not drive the RP1
correctly.

I2C devices, all on one bus: PCA9685 servo drivers at `0x40`/`0x41`, MPU6050 IMU at `0x68`,
ADS7830 ADC at `0x48`. That shared bus is why the `hardware` node owns all three and cannot be
split further.

```bash
i2cdetect -y 1                                    # which devices respond
sudo lsof /dev/gpiochip0                          # who holds the GPIO
```

`GPIO busy` almost always means orphaned nodes from a killed run, not a fault — see §10.

---

## §8 Credentials

Secrets live in `.env` at the project root, git-ignored, mode `600`. **Never** put a credential
value in a tracked file — names, paths and verification commands only.

Required:

- `OPENAI_API_KEY` — GPT-4o, Whisper, TTS. Needed by the `llm` and `audio` nodes.
- `PICOVOICE_ACCESS_KEY` — the "Hey Pi Bot" wake word.

The fork did not carry `.env` across. To seed it from the original project:

```bash
cp /opt/pibot-hexapod/.env /opt/pibot-dora/.env && chmod 600 /opt/pibot-dora/.env
```

Verify without printing values:

```bash
./venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
for k in ('OPENAI_API_KEY','PICOVOICE_ACCESS_KEY'):
    v=os.getenv(k); print(f'{k}: {\"set, len \"+str(len(v)) if v else \"MISSING\"}')"
```

Only the full graph needs these. Sensors, motion, turn, stance and camera graphs run without.

---

## §9 The fork, and re-syncing it

This project was forked from `/opt/pibot-hexapod` on 2026-08-16 and is fully standalone:
`src/`, `config/`, `point.txt` and `params.json` are **copies**. It runs with the original
deleted.

The accepted cost is drift. `point.txt` is servo calibration and matters most — recalibrating
one project does not fix the other.

Compare:

```bash
diff -rq /opt/pibot-hexapod/src /opt/pibot-dora/src
diff /opt/pibot-hexapod/point.txt /opt/pibot-dora/point.txt
diff /opt/pibot-hexapod/config/config.yaml /opt/pibot-dora/config/config.yaml
```

The `diff -rq` above now reports `Only in /opt/pibot-hexapod/src: server.py` — that is
**expected, not drift to reconcile**. `src/server.py` (the Freenove TCP control path) was
deleted here on 2026-08-19 because no node uses it; the dataflow control path is the `tool_call`
message stream, not a TCP socket (CHANGELOG 2026-08-19). Do **not** copy it back to "fix" the
diff. It still exists upstream because `/opt/pibot-hexapod` is never modified by this project.

Pull a specific upstream fix across deliberately, then record it in CHANGELOG:

```bash
cp /opt/pibot-hexapod/src/control.py /opt/pibot-dora/src/control.py
```

Note that `nodes/stances.py` mirrors geometry constants from `src/control.py`; after any
`control.py` change, re-run `./venv/bin/python nodes/stances.py`.

**Never write to `/opt/pibot-hexapod`.** It is the fallback if this experiment is abandoned.

---

## §10 Troubleshooting

**`GPIO busy`, or an I2C error, right after a previous run.**
Orphaned nodes. A killed dora CLI leaves them holding the hardware.

```bash
ps aux | grep "pibot-dora/venv/bin/python nodes/"
./stop.sh
```

**A node "exited with exit status Success" but the dataflow failed.**
The node quit before connecting to dora. Almost always a missing
`if __name__ == "__main__": main()`. Check that before suspecting dora.

**A movement command returns OK but nothing moved.**
`set_leg_angles()` declines silently when a leg is out of reach. Validate the pose
(`nodes/stances.py`) and check the log for `This coordinate point is out of the active range`.

**Motion commands are all refused.**
Read the refusal text — it states the measured voltage and the floor. Either the pack is low
(§3), the gait thread has died (`health` output), or `PIBOT_NO_MOTION` is set.

**A module is missing after changing the venv.**
This project's venv must not depend on the upstream one. Check nothing resolves there:

```bash
./venv/bin/python -c "
import importlib
for m in ['openai','pvporcupine','pyaudio','webrtcvad','yaml','dotenv','numpy','spidev','rpi_ws281x','smbus2','dora']:
    p = getattr(importlib.import_module(m),'__file__','') or ''
    print(('UPSTREAM ' if '/opt/pibot-hexapod/' in p else 'ok       ')+m)"
```

Note that `pip install` reports success for a package it can already import, so if a `.pth`
ever re-links the upstream venv, installs will silently no-op.

**Everything looks fine but the robot does nothing.**
Confirm the graph is actually running and the nodes are the ones you edited:

```bash
ps aux | grep "nodes/"
ls -1t out/ | head -1
```
