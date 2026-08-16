# PiBot-Hexapod on dora-rs

An experimental port of [PiBot-Hexapod](/opt/pibot-hexapod) to
[dora-rs](https://dora-rs.ai): the same robot, but split from one Python
process into eight cooperating processes wired together by a dataflow graph.

This project is a **parallel experiment**, not a replacement. It does not
modify `/opt/pibot-hexapod` in any way. If you decide the dora version is not
worth it, you stop running it and nothing needs undoing — see
[Swapping back](#swapping-back).

Why this exists, what it actually buys, and what it costs:
[docs/DESIGN.md](docs/DESIGN.md).

---

## Quick start

Read the battery first — the same pre-flight rule as upstream applies, and the
hardware node enforces it in code:

```bash
cd /opt/pibot-hexapod && ./venv/bin/python -c "from src.adc import ADC; print(ADC().read_battery_voltage())"
```

Sensors and LEDs only. No motion, no microphone, no API spend. Start here:

```bash
/opt/pibot-dora/run.sh sensors
```

The full autonomous graph — the dora equivalent of `python src/main.py`.
Needs servo power on, the battery above 6.0V, and it will make OpenAI calls:

```bash
/opt/pibot-dora/run.sh
```

Stop cleanly with Ctrl+C. After any *unclean* exit (killed terminal, dropped
ssh, `timeout`), release the hardware:

```bash
/opt/pibot-dora/stop.sh
```

---

## How it is laid out

```
/opt/pibot-dora/
  dataflow.yml           full autonomous graph (8 nodes)
  dataflow-sensors.yml   safe subset: hardware telemetry + ultrasonic + LED + probe
  run.sh                 launcher (cleans up orphans on entry and exit)
  stop.sh                kill all nodes, release I2C / GPIO / mic / servos
  bin/py                 node launcher; forces the venv interpreter (see below)
  venv/                  dora-rs + pyarrow, layered over the upstream venv
  nodes/
    common.py            bootstrap, message encoding, tool routing, tool-call shim
    brain_node.py        behaviour + conversation state machine (no hardware)
    audio_node.py        mic, speaker, wake word, VAD, Whisper, TTS
    llm_node.py          OpenAI calls + SQLite long-term memory
    hardware_node.py     I2C bus: servos, IMU, ADC + the gait thread
    camera_node.py       picamera2 / CSI
    led_node.py          WS2812B over SPI
    ultrasonic_node.py   HC-SR04
    buzzer_node.py       GPIO buzzer
    probe_node.py        test harness used by dataflow-sensors.yml
  docs/DESIGN.md         what changed, what it buys, what it does not
```

**No driver code is duplicated.** Every node imports the real
`src/control.py`, `src/audio.py`, `src/actions.py` and so on from
`/opt/pibot-hexapod`. `nodes/common.py::bootstrap()` puts that project on
`sys.path` and `chdir`s into it, so every relative path baked into the upstream
code (`config/config.yaml`, `point.txt`, `data/`, `.env`) resolves unchanged.

That means the two projects share **one** servo calibration, one config, one
`.env`, one conversation history and one memory database. Fix a driver bug
upstream and this project gets it for free; there is no second copy to drift.

Point elsewhere with `PIBOT_HOME=/path/to/fork ./run.sh` if you ever want them
to diverge.

---

## The graph

```
                    ┌──────────┐
        wake ──────▶│          │──── speak ──────▶┌───────┐
   user_text ──────▶│          │◀── speech_done ──│ audio │  mic + speaker
                    │          │                  └───┬───┘  wake word, Whisper, TTS
 llm_response ─────▶│  brain   │                      │ speech_state
                    │          │──── llm_request ─▶┌──┴──┐  │
     battery ──────▶│  (no     │◀── llm_response ──│ llm │  │  OpenAI + memory DB
      health ──────▶│  hardware│                   └─────┘  │
    distance ──────▶│  at all) │                            ▼
       image ──────▶│          │──── emotion ──────▶┌─────┐
                    │          │                    │ led │  WS2812B / SPI
                    │          │──── capture ──────▶┌────────┐
                    │          │                    │ camera │
                    └────┬─────┘                    └────────┘
                         │
                    tool_call  (broadcast; each node runs only what it owns)
                         ├──────▶ hardware    walk, stand, attitude, head, battery
                         ├──────▶ led         set_led
                         ├──────▶ ultrasonic  get_distance
                         └──────▶ buzzer      buzz
```

Tool *results* come back on one stream per node (`tool_result_hardware`,
`tool_result_led`, …) because dora maps each input id to exactly one source.
The brain treats them all identically.

---

## Things worth knowing

**`bin/py` is not decoration.** dora spawns a node whose `args` ends in `.py`
using the *system* python and ignores whatever interpreter `path` names — which
would miss this project's venv entirely. `bin/py` is named as the node `path`
so dora execs it as a plain binary, and it in turn execs the venv interpreter.
Setting `PATH` or `VIRTUAL_ENV` does not work; this was tested.

**Orphaned nodes are the failure mode to know.** If the dora CLI is killed
rather than exiting cleanly, the daemon dies but node processes survive and
keep holding the I2C bus, gpiochip0, the microphone and any energised servos.
The next run then fails with `GPIO busy` or an I2C error, which looks exactly
like a hardware fault and is not one. `run.sh` cleans up on entry and exit;
`stop.sh` does it on demand. Check with:

```bash
ps aux | grep "pibot-dora/venv/bin/python nodes/"
```

**The battery gate is enforced in code, not by convention.** The hardware node
owns the ADC, so it reads the pack *before* constructing `Control()` — which
drives the legs to the standing pose merely by being instantiated — and
refuses below 6.0V. It also refuses every motion tool below the floor, and
refuses when the gait thread has died. Relaxing servos is always allowed, since
that reduces current draw. Override for bench work on a regulated supply with
`PIBOT_BATTERY_FLOOR=5.0`, deliberately.

**`PIBOT_NO_MOTION=1`** brings up the ADC for telemetry but never constructs
`Control()`, so nothing can move. `dataflow-sensors.yml` sets it.

---

## Swapping back

There is nothing to undo. The upstream project is untouched:

```bash
cd /opt/pibot-hexapod && source venv/bin/activate && python src/main.py
```

To remove the experiment entirely, stop it and delete the directory:

```bash
/opt/pibot-dora/stop.sh && sudo rm -rf /opt/pibot-dora
```

The only shared state is what both versions read and write by design: the
conversation history and the memory DB under `/opt/pibot-hexapod/data/`. A
conversation had with the dora version is still there for the original, and
vice versa.

---

## Status

Verified on this Pi:

- dora 0.5.0 runs, spawns nodes in the venv, and delivers messages between them
- four-process graph with battery telemetry crossing a process boundary over I2C
- LED strip initialises and responds to `emotion` messages from another process
- HC-SR04 initialises; clean startup and shutdown with no leaked processes
- all 13 LLM tools route to exactly one owning node, with no gaps or orphans
- the tool-call shim satisfies the upstream history helpers unmodified
- the full 8-node graph is accepted by dora and every edge resolves

Not yet verified (needs hardware or spend, not code):

- **motion** — servo power was off during this build; nothing has driven a leg
- **ultrasonic readings** — the sensor returns no echo, but it does the same on
  the upstream code, so this is pre-existing and probably just unpowered
- **camera** — the Pi 5 CSI receive path is defective (pinned upstream); confirmed
  by test, and the node now fails each capture in 8s and disables itself after 3.
  The old text claimed clean degradation before it was true; the
  node is written to degrade cleanly and needs no change when the board is replaced
- **the voice loop end to end** — wake word → Whisper → GPT-4o → tools → TTS
