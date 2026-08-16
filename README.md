# PiBot-Dora

The PiBot-Hexapod robot running as a [dora-rs](https://dora-rs.ai) dataflow graph: eight
cooperating processes that share nothing and talk only by message, instead of one Python
process with threads bolted on. Same robot, same hardware, same inverse kinematics and gaits —
a different spine.

Forked from `/opt/pibot-hexapod` on 2026-08-16 and now **fully standalone**: it runs with the
original deleted. That original is untouched and remains the fallback.

- Mission and scope — [docs/MASTERPLAN.md](docs/MASTERPLAN.md)
- What the port buys and costs — [docs/DESIGN.md](docs/DESIGN.md)
- Open work — [docs/PENDING.md](docs/PENDING.md)
- Procedures, credentials, troubleshooting — [docs/RUNBOOKS.md](docs/RUNBOOKS.md)
- History and decisions — [docs/CHANGELOG.md](docs/CHANGELOG.md)

---

## Quick start

Read the battery first, **with servo power already on** — the unloaded reading is about a volt
optimistic:

```bash
cd /opt/pibot-dora && ./venv/bin/python -c "from src.adc import ADC; print(ADC().read_battery_voltage())"
```

Then the safe graph. Sensors and LEDs only: no motion, no microphone, no API spend.

```bash
/opt/pibot-dora/run.sh sensors
```

Stop cleanly with Ctrl+C. After an unclean exit (killed terminal, dropped ssh), release the
hardware:

```bash
/opt/pibot-dora/stop.sh
```

---

## The graphs

Each graph is a test of one thing. Together they are the test suite; there is no other one.

| Command | What it does | Moves? |
|---|---|---|
| `./run.sh sensors` | Battery, distance, LED telemetry | no |
| `./run.sh camera` | Capture attempts only | no |
| `./run.sh stance` | Cycle the eight named stances | legs only |
| `./run.sh motion` | Stand, roll, pitch, head, relax | legs only |
| `./run.sh turn` | Rotate in place | **travels** |
| `./run.sh` | Full autonomous graph | **travels** |

The full graph is the equivalent of the original `python src/main.py`. It needs `.env` and
makes OpenAI calls.

---

## Layout

```
nodes/        one file per dora node; each owns its hardware exclusively
src/          robot drivers, forked from PiBot-Hexapod
config/       config.yaml + wake-word model
point.txt     servo calibration      params.json   PCB/Pi version
dataflow*.yml the graphs
bin/py        node launcher — forces the venv interpreter
docs/         MASTERPLAN, PENDING, CHANGELOG, RUNBOOKS, DESIGN
```

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
                         ├──────▶ hardware    walk, stand, attitude, head, stance, battery
                         ├──────▶ led         set_led
                         ├──────▶ ultrasonic  get_distance
                         └──────▶ buzzer      buzz
```

Node boundaries follow which device tolerates sharing, not tidiness. Servos, IMU and battery
ADC are fused in `hardware` because they share one I2C bus; mic and speaker are fused in
`audio` because the mic is exclusive. The `brain` owns no hardware, so it can never block on a
servo, a socket or a microphone.

---

## Two things that will bite you

**Orphaned nodes look exactly like a hardware fault.** If the dora CLI is killed rather than
exiting cleanly, its node processes survive holding the I2C bus, gpiochip0, the mic and any
energised servos, and the next run fails with `GPIO busy`. `run.sh` cleans up on entry and
exit; `stop.sh` does it on demand.

**A movement command can return OK having done nothing.** `set_leg_angles()` silently declines
when a leg would reach outside 90–248 mm. Poses are validated before they are sent, and
confirmed after — see RUNBOOKS §4.

---

## Status

Working and verified on hardware: the dataflow runtime, the process split, battery telemetry
over I2C, the LED strip, motion (stand, attitude, head), turning in place at a measured
~7.8°/gait cycle, and the eight named stances.

Blocked: the camera produces no frames — the Pi 5's CSI receive path is defective and the board
needs replacing. The battery pack is flat. The full voice loop — wake word, Whisper, GPT-4o,
TTS — has never been run. See [docs/PENDING.md](docs/PENDING.md).

---

## Falling back

The original project is untouched and still runs:

```bash
cd /opt/pibot-hexapod && source venv/bin/activate && python src/main.py
```

To remove this experiment entirely:

```bash
/opt/pibot-dora/stop.sh && sudo rm -rf /opt/pibot-dora
```

Nothing else needs undoing. Note that the two projects no longer share anything — including
conversation history and long-term memory, which are now separate.
