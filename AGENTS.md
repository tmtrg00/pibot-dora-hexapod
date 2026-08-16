# AGENTS.md — PiBot-Dora

This file loads on every session, so it is a context budget. Keep it under 300 lines;
anything needed for only one kind of work goes in `docs/RUNBOOKS.md` instead. `CLAUDE.md`
and `GEMINI.md` are symlinks to this file — one file, no copies to drift.

## Session ritual

**At start:** read `docs/MASTERPLAN.md` and `docs/PENDING.md` in full. Do NOT open
`docs/CHANGELOG.md` with the Read tool — it grows without bound; skim it with
`head -60 docs/CHANGELOG.md` and `grep -n '^## ' docs/CHANGELOG.md | head`. Check the working
tree (`git status --short`, `git log -5 --oneline`, `git branch --show-current`). Do not
re-litigate or revert a documented decision without explicit approval.

**At end:** document from the diff, not from memory of the conversation. Append a dated entry
to the **top** of `docs/CHANGELOG.md` (with `**Fix:**` / `**Decision:**` lines where they
apply); prune the shipped work out of `docs/PENDING.md` **in the same commit** — moved, not
copied. Update `docs/MASTERPLAN.md` only on an approved scope change. Update `docs/RUNBOOKS.md`
if a procedure, tool, or credential changed. Then commit and push. If a hook fails, fix the
cause — never `--no-verify`.

## The three-document rule

1. **A fact never lives in two files.** When work ships it moves from PENDING to CHANGELOG in
   the same commit. Two copies means one is stale with no way to tell which.
2. **PENDING is work-in-progress only.** If it reads like a history of what got done, the
   system has been violated.
3. **Banned filenames** — never create these; each is a place for a fact to hide from the file
   that should own it: `BUGS.md`, `DECISIONS.md`, `JOURNAL.md`, `TODO.md`, `NOTES.md`,
   `ARCHITECTURE.md`, `USER_GUIDE.md`, `IDEAS.md`. Bugs and decisions are history → CHANGELOG.
   Procedures, tools, credentials → RUNBOOKS. Open work → PENDING.
4. **PENDING tags**, all dated with when the tag was applied (the date never updates):
   `[TODO date]` not started · `[IN-FLIGHT date]` started, not verified · `[PINNED date]`
   blocker that must surface in every briefing · `[SHELVED date]` waiting on an external
   trigger. Banned: `[DONE]`, `[SUPERSEDED]`, `[ROLLED BACK]` — finished things live in
   CHANGELOG.
5. **Superseding a decision** takes a new dated CHANGELOG entry whose `**Decision:**` line says
   so and references the prior date. Never edit or silently revert the old entry — the trail
   "we chose A, then chose B because A did X" is the most valuable thing in the file.

## Rules

- `docs/MASTERPLAN.md` is read-only: edit only on a fundamental root-level change, and only
  after explicit human approval.
- Never modify `README.md` unless asked.
- Never delete files without explicit instruction.
- CHANGELOG is append-only; PENDING and CHANGELOG change in pairs when work ships.
- No credential values in any tracked file — names, paths, permissions, and verification
  commands only (see RUNBOOKS §8).
- **Never modify `/opt/pibot-hexapod`.** It is a separate project and the fallback if this
  experiment is abandoned. Read it freely; write to it never.
- When in doubt about scope or direction, re-read `docs/MASTERPLAN.md` before asking.

## Pre-flight checks (binding)

These are constraints on the next action, not advice. Each line is a rule with a scar
attached — it earned its place from a real incident, with the cost of ignoring it. Start
nearly empty; add only from real incidents, never invented best practice.

- **Read the battery before any command that drives servos; refuse below 6.0V.**
  `./venv/bin/python -c "from src.adc import ADC; print(ADC().read_battery_voltage())"`
  returns `(load_V, pi_V)` and takes a second. **Read it with servo power already on** — the
  unloaded reading is roughly a volt optimistic and that gap is the whole risk. Measured
  2026-08-16: 6.94V unloaded read 5.88–6.00V under load. The `hardware` node enforces this
  floor in code, including refusing to construct `Control()` at all, since instantiating it
  drives the legs to standing as a side effect. (RUNBOOKS §3.)
- **After a killed run, check for orphaned nodes before blaming hardware.** If the dora CLI is
  killed rather than exiting cleanly, its node processes survive and keep holding the I2C bus,
  gpiochip0, the mic and energised servos. The next run then fails with `GPIO busy` or an I2C
  error that looks exactly like a fault and is not one. `./stop.sh` clears it
  (CHANGELOG 2026-08-16).
- **Absence of an error is not success — a movement command can silently do nothing.**
  `Control.set_leg_angles()` calls `check_point_validity()` and, if any leg would reach outside
  90–248 mm, prints a line and returns without moving. No exception, no return value. Verify a
  pose took effect rather than assuming (CHANGELOG 2026-08-16).
- **A node that exits before connecting to dora reports "exit status Success" while failing the
  whole graph.** The usual cause is a script that defines `main()` and never calls it. Check
  the entry point before debugging dora (CHANGELOG 2026-08-16).

## Operating limits

- Stop and ask before anything hard to reverse or physically risky: driving motors on a
  suspect battery, any action that could make the robot fall, deleting files.
- For a long or blockable task, propose a resumable approach and checkpoint as you go rather
  than running to the end silently.
- "Delivered" means verified. A hardware action is done when a sensor or observation confirms
  it — not when the command returned without error (see pre-flight above). Say plainly what was
  verified and what was only attempted.

## Writing style

Applies to chat replies, CHANGELOG entries, and PR bodies. Use clear, nuanced language — not
one-liners — describing what was done, why, and how, in enough detail to be useful later.
CHANGELOG headlines are sentences a future `grep` can hit, not labels. Keep numbers with
units. End a completed task or session with a short plain-language overview for a non-technical
reader, after the technical summary, not instead of it.

## Project Summary

PiBot-Dora runs the PiBot-Hexapod robot as a **dora-rs dataflow graph**: eight cooperating
processes that share nothing and communicate only by message, instead of one Python process
with threads bolted on. Same robot, same hardware, same inverse kinematics and gaits — a
different spine.

It is a **parallel experiment**, forked from `/opt/pibot-hexapod` on 2026-08-16 and now fully
standalone. That original still runs unchanged and is the fallback. Full mission and scope:
`docs/MASTERPLAN.md`. What the port buys and costs: `docs/DESIGN.md`.

## Stack

- **Python 3.13** on Raspberry Pi 5 (8GB), Ubuntu 25.10 — venv uses `--system-site-packages`
  for `lgpio` and `libcamera`.
- **dora-rs 0.5.0** — dataflow runtime, Rust core with a Python node API. Messages are Arrow
  arrays carrying one JSON string (`nodes/common.py`).
- **OpenAI API** — GPT-4o (LLM + vision), Whisper (STT), TTS.
- **Picovoice Porcupine** — "Hey Pi Bot" wake word.
- **PyAudio + WebRTC VAD** — mic input with voice-activity detection.
- **2x PCA9685 (I2C 0x40/0x41)** — 32-channel PWM servo drivers.
- **MPU6050 (I2C 0x68)** — 6-axis IMU with Kalman + Madgwick AHRS.
- **WS2812B** — 7 RGB LEDs over SPI. **HC-SR04** — ultrasonic on GPIO 27/22.
- **ADS7830 (I2C 0x48)** — 8-channel ADC for battery voltage.
- **picamera2** — Pi Camera over CSI. Currently non-functional; see PENDING.
- **SQLite** — long-term memory. **PyYAML + python-dotenv** — config and secrets.
- **GPIO via `lgpio` only** — never gpiozero/RPi.GPIO on the Pi 5 (RUNBOOKS §7).

## Key Directories

```
nodes/               One file per dora node; each owns its hardware exclusively
  common.py          bootstrap, message encoding, tool routing, tool-call shim
  brain_node.py      behaviour + conversation state machine (owns NO hardware)
  audio_node.py      mic + speaker: wake word, VAD, Whisper, TTS
  llm_node.py        OpenAI calls + SQLite long-term memory + initiative policy
  hardware_node.py   I2C bus: servos, IMU, ADC, gait thread, battery gate, stances
  camera_node.py     picamera2 / CSI, with capture deadlines
  led_node.py        WS2812B over SPI
  ultrasonic_node.py HC-SR04      buzzer_node.py     GPIO buzzer
  stances.py         named poses + offline leg-reach validation
  *_test_node.py     harnesses used by the single-purpose test graphs
src/                 Robot drivers, forked from PiBot-Hexapod (see RUNBOOKS §9)
  control.py         IK engine, gait generation, condition_monitor thread
  actions.py         LLM tool schemas + hardware dispatch (13 tools; +1 here)
  servo.py imu.py led.py ultrasonic.py buzzer.py adc.py camera.py audio.py
  voice.py llm_handler.py memory_db.py led_display.py
config/config.yaml   All configuration      point.txt      Servo calibration
params.json          PCB/Pi version         data/          Runtime data (untracked)
dataflow*.yml        The graphs — see Commands
bin/py               Node launcher; forces the venv interpreter (RUNBOOKS §2)
```

## Commands

```bash
./run.sh sensors    # telemetry + LED, NO motion, no mic, no API spend — start here
./run.sh stance     # cycle the named stances (moves legs, does not travel)
./run.sh motion     # scripted pose sequence (moves legs, does not travel)
./run.sh turn       # rotate in place (LOCOMOTION — travels)
./run.sh camera     # capture attempts only
./run.sh            # full autonomous graph, the equivalent of the original main.py
./stop.sh           # kill all nodes, release I2C / GPIO / mic / servos
```

Graphs that move the robot need servo power and a charged pack. No automated test framework;
the single-purpose graphs above are the test suite. Setup, troubleshooting, hardware checks and
credential handling live in `docs/RUNBOOKS.md`.

## Environment Variables

Names only; full catalogue and credential handling in RUNBOOKS §8.

- `OPENAI_API_KEY` — required; GPT-4o, Whisper, TTS.
- `PICOVOICE_ACCESS_KEY` — required for wake word.
- `PIBOT_BATTERY_FLOOR` — servo safety floor in volts, default 6.0. Lowering it is deliberate.
- `PIBOT_NO_MOTION` — bring up the ADC but never construct `Control()`; nothing can move.
- `PIBOT_HOME` — override the project root. Defaults to this directory.
- `PIBOT_DB_PATH` — SQLite path (default `data/pibot.db`).
