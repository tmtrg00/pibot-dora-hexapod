# Design notes — what the dora port actually changes

Written from the port itself, not from the plan for it. If you are deciding
whether to keep this experiment, this is the file to read.

## The shape of the change

The upstream robot is one Python process of ~5,900 lines with threads bolted on
wherever concurrency was needed: a wake-word thread, the `condition_monitor`
gait thread, per-command movement threads, an LED animation thread. They share
memory and call into each other directly.

The dora version is eight processes that share nothing and talk only by
message. Nothing was rewritten at the driver level — every node imports the
same `src/*.py` modules. What was rewritten is the *spine*: the `while True`
loop in `src/main.py` became `nodes/brain_node.py`, an explicit state machine.

That is the whole port. Roughly 1,400 lines of node code sitting on top of an
untouched 5,900-line codebase.

## Where the process boundaries fell, and why

Ownership was decided by **which device can tolerate being shared**, not by
what would look tidy.

| Node | Owns | Why it is its own process |
|---|---|---|
| `hardware` | I2C: 2× PCA9685, MPU6050, ADS7830, + gait thread | Cannot be split further — see below |
| `audio` | mic, speaker, Porcupine, Whisper, TTS | The biggest GIL contender; also a single exclusive device |
| `llm` | OpenAI calls, SQLite memory | 1–3s of blocking network I/O per turn |
| `camera` | picamera2 / CSI | Frame capture should never stall the loop |
| `led` | WS2812B / SPI | Animation threads run free of everything else |
| `ultrasonic` | HC-SR04 / GPIO | An echo timeout blocks only itself |
| `buzzer` | GPIO buzzer | `buzz` sleeps for the duration of the sound |
| `brain` | nothing | Cannot block on hardware if it holds none |

### The I2C limit is the honest cost

Both servo drivers (0x40/0x41), the IMU (0x68) and the battery ADC (0x48) are on
one I2C bus. In a single process the GIL serialised access for free. Across
processes nothing does, and these drivers issue multi-step write-then-read
transactions that interleave badly.

So servos, IMU and ADC stay **fused in one node**. This is the part of the
original pitch for dora that this hardware does not pay out: you cannot
parallelise the sensor layer, because the sensor layer is one wire.

### Audio is one node for the same kind of reason

The wake-word listener and the VAD recorder both need the microphone, and it is
exclusive. Upstream handed it back and forth with a `threading.Event`; splitting
it across processes would mean arbitrating an exclusive device over IPC for no
gain. So `audio_node.py` keeps that handoff internally, on a worker thread,
while the main thread owns the dora event loop and is the sole caller of
`send_output`. No thread-safety is assumed of the dora handle.

## What the port genuinely buys

**Real parallelism where it was contended.** Whisper upload, TTS playback,
camera capture and vision calls no longer compete with the gait loop for the
GIL. On four Pi 5 cores these now actually overlap. This is the main prize and
it is the one thing still unmeasured — see *Not yet verified* in the README.

**Silent failures become loud.** The project's worst scar is the
`condition_monitor` daemon thread swallowing exceptions: the NumPy 2.0 `np.mat`
removal broke every attitude and balance command and surfaced only as a
timeout, weeks later. Three things now catch that class of bug:

- the hardware node watches its own gait thread and publishes `health` when it
  dies, and *refuses* motion commands rather than reporting success while
  nothing moves;
- a dead node is reported by dora and its stderr is captured, instead of a
  thread vanishing inside a live process;
- every wait in the brain has a deadline, and a tool that never answers is
  recorded as `"no result (timed out)"` — the model is told the truth rather
  than silently seeing a gap.

**The battery rule became code.** Upstream, "read the battery before driving
servos" is a convention in `AGENTS.md` that a human or an agent has to
remember. Here the hardware node owns the ADC, so it reads the pack *before*
constructing `Control()` — which drives the legs to the standing pose merely by
being instantiated — and refuses below 6.0V. Every motion tool is gated on a
reading no older than 10 seconds. Relaxing is always permitted, since it
reduces current draw. This is a real safety improvement independent of dora.

**Sensor reads stopped being blocking calls.** `sensor_check` used to block the
autonomy loop on an ultrasonic ping and a 1-second ADC read. Distance and
battery now arrive continuously on their own streams and the brain just reads
its last-known values. Low-battery warnings became event-driven, with a
cooldown, instead of a poll.

**Restartability and record/replay.** dora can restart a failed node and can
record streams for replay. Neither is wired up here, but the graph is the
prerequisite, and replaying a captured sensor session to debug gaits without
draining the pack is the obvious next win.

## What it costs

**Debugging is genuinely harder.** No single stack trace, no `pdb` across the
robot. Eight log streams. `dora run` interleaves them, which helps, but a bug
that spans nodes is more work than it was.

**Orphaned processes are a new failure mode**, and a nasty one: a killed CLI
leaves nodes holding I2C, GPIO, the mic and energised servos, and the next run
fails in a way that looks exactly like a hardware fault. `run.sh` and `stop.sh`
exist entirely because of this. It bit this build twice during testing.

**More memory and slower start.** Eight Python interpreters, each importing
numpy and the driver stack.

**Added latency on the voice path** — a few messages' worth per turn. Almost
certainly small against a 1–3s OpenAI call, but it is unmeasured, and the voice
path is the most latency-sensitive thing the robot does.

**dora is young.** Version 0.5.0, smaller community than ROS 2, thinner docs,
breaking changes still land. One concrete example already: dora ignores the
interpreter named in a node's `path` and spawns `.py` nodes with the system
python, which required the `bin/py` wrapper to work around.

## Behavioural differences from upstream

Mostly faithful, with deliberate exceptions:

- **Barge-in** works the same, but is expressed as states rather than a nested
  loop: `speech_done{interrupted:true}` sends the brain back to `LISTENING` and
  the audio node starts recording immediately.
- **`take_photo`** keeps the upstream special case — capture, describe, and let
  the description replace the spoken reply — but it is now a capture message, a
  vision request and a tool result rather than an inline call.
- **The "Yes?" acknowledgement** is spoken by the audio node, not the brain, so
  it is not delayed by a round trip.
- **Idle behaviours** (`look_around`, `casual_movement`) used `time.sleep()`
  between steps. They are now deferred callbacks on the brain's tick, so the
  loop never blocks.
- **`sensor_check`** no longer speaks the distance; it logs, and low-battery
  warnings moved to the continuous `battery` stream.
- **Memory** lives entirely in the `llm` node — context building, extraction and
  storage — so the SQLite DB has a single writer.

## Where the remaining upside is

The camera path is still file-based: capture to JPEG, pass a path, upload to
GPT-4o. That is fine for one observation a minute and wastes the best thing
dora offers, which is zero-copy Arrow buffers in shared memory. If vision-guided
obstacle avoidance (already in the upstream PENDING) gets built, streaming real
frames from `camera_node` to a local detector node is where this architecture
starts paying for itself properly.

The other open question is whether the behaviour-tree work also sitting in
upstream PENDING (`py_trees`) belongs *inside* `brain_node.py`. It would fit
cleanly — the brain owns no hardware, so its logic is free to be restructured
without touching anything else. That is an argument for this split holding up
regardless of which planner wins.

## Honest summary

The port works and is faithful. The safety and observability gains are real and
would be worth having even without dora. The parallelism gain is real in
principle but unmeasured, and it is capped by the fact that the sensor layer is
one I2C wire that cannot be split. The costs — harder debugging, orphaned
processes, a young framework — are real and immediate.

The fair test is whether the robot feels more responsive with vision and voice
running together. That needs servo power, a working camera and a full voice
turn, none of which were available while this was built.
