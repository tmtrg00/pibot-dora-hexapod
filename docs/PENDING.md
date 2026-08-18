# PENDING — PiBot-Dora

Open work only. When something ships it moves to CHANGELOG in the same commit — moved, not
copied. If this file starts reading like a history of what got done, the system has been
violated. Tags: `[TODO date]` not started, `[IN-FLIGHT date]` started but unverified,
`[PINNED date]` blocker that must surface in every briefing, `[SHELVED date]` waiting on an
external trigger. The date is when the tag was applied and never updates — a stale date
tells you something.

## Active

- [PINNED 2026-08-17] **Camera produces no frames on Ubuntu, but works on Raspberry Pi OS on this
  same board. The hardware is fine — do NOT RMA the Pi.** This replaces the previous entry, which
  said the RP1 receiver was defective; see CHANGELOG 2026-08-17 for why that was wrong. The
  owner's recollection that the camera once worked was correct and is no longer an open question.
  - Check state: `rpicam-hello --list-cameras` enumerates both sensors; captures fail with
    `Camera frontend has timed out` and every CSI-2 counter reads zero.
  - Fails on both Ubuntu kernels (6.17.0-1021 and 7.0.0-1016), works on Raspberry Pi OS, so it is
    Ubuntu-specific packaging, not a kernel-version regression.
  - Everything checkable on the Ubuntu side looks correct: sensor endpoints (`data-lanes <1 2>`,
    297/450 MHz link frequencies), power rails enabling during capture (`cam0_reg` use 0→1),
    link rates programmed (437/900 Mbps), and a fully linked media pipeline with matching
    formats. The bug is below all of that.
  - Eliminated so far, each by direct test rather than argument (CHANGELOG 2026-08-17): the
    upstream CFE driver; **vendor libcamera 0.7.2 built from source** and installed to
    `/usr/local`; **the vendor CFE driver built from `raspberrypi/linux` `rpi-7.0.y`**, matching
    this kernel exactly. Also verified the device tree matches vendor sources property for
    property, including the `iommus` assignment. None of it changes the failure.
  - Do **not** re-run these: the DTB comparison, the driver override, the vendor libcamera build,
    the vendor CFE driver build, or removing the CSI `iommus` property. All are recorded as
    negative results (CHANGELOG 2026-08-17).
  - Treat the "zero packets" register evidence with suspicion — `CSI2_CH_DEBUG` and
    `CSI2_CH_FE_FRAME_ID` cover the direct `csi2 → csi2_chN` channels, but libcamera uses
    `csi2 → pisp-fe`, so they may read zero even on a working system.
  - **Decide the direction (blocks everything vision-related):** move the robot to Raspberry Pi
    OS, which is proven working on this board and is what `picamera2` is developed against, or
    stay on Ubuntu 26.04 with no camera. The remaining difference is the rest of the Ubuntu
    kernel — RP1 platform, clock, IOMMU and regulator code — which cannot be swapped piecemeal.
  - The node needs no change either way.
  - Blocks: vision-guided obstacle avoidance, the autonomous observation loop, and the
    responsiveness comparison that MASTERPLAN makes the definition of success.
  - **Do NOT buy a ribbon cable, a camera module or a board.** A parallel session on 2026-08-16
    concluded the fault was hardware and set that replacement order; it is superseded (CHANGELOG
    2026-08-17). Its decisive evidence was the IMX708's on-chip colour-bar generator also
    returning zero bytes, which isolates the imaging path but not the software that commands the
    sensor to stream — so it did not eliminate software, and the camera captures fine on
    Raspberry Pi OS with this hardware.
  - The `imx708` overlay `link-frequency` variants (447/450/453 MHz) are the one software lever
    never tried, but they exist to rescue a marginal link and Pi OS works here at the default,
    so there is no marginal link to rescue. Low value.

- [TODO 2026-08-17] **Rewrite `requirements.txt` to match what is actually installed.** It is now
  actively misleading: a 90+ line inherited freeze pinning `anthropic`, `google-generativeai`,
  `groq`, `ollama`, Adafruit CircuitPython and `luma.oled`, none of which the code imports and
  none of which the rebuilt venv contains. The real set is `dora-rs` and `dora-rs-cli` at 0.5.0
  plus `openai`, `python-dotenv`, `pvporcupine`, `smbus2`, `webrtcvad`, `rpi-ws281x`,
  `audioop-lts` and `pydub`, with the rest coming from system packages via
  `--system-site-packages`. Left unchanged for now because replacing it is a scope decision, not
  a side effect of the rebuild.


- [TODO 2026-08-16] **Copy `.env` into this project.** The fork carried `src/`, `config/` and
  the calibration but not the secrets, so `OPENAI_API_KEY` and `PICOVOICE_ACCESS_KEY` are
  missing and the full graph cannot run. Sensor, motion, turn and stance graphs are unaffected.
  `cp /opt/pibot-hexapod/.env /opt/pibot-dora/.env && chmod 600 /opt/pibot-dora/.env`
  (RUNBOOKS §8).

- [TODO 2026-08-16] **Verify the full autonomous graph end to end.** Four of the eight nodes
  have never run against real hardware: `audio`, `llm`, `brain` and `buzzer`. The wake word,
  Whisper, GPT-4o, tool dispatch, TTS and barge-in path is entirely unproven. `buzzer` is the
  easy one to miss — it appears only in `dataflow.yml` and in no single-purpose test graph, so
  unlike the others it is not covered even indirectly. Note also that every test graph
  substitutes a test node for the brain, so no graph has yet exercised `brain` talking to
  anything, and the behaviour state machine that replaced the `while True` loop in
  `src/main.py` has never executed. Needs `.env`, a charged pack and accepts API spend.

## Pinned for later

Deliberately deferred. Review when Active clears.

- [TODO 2026-08-16] **Measure the parallelism claim.** The central argument for the port is
  that audio, vision and the gait loop stop competing for one GIL. Nothing has measured it.
  Needs a working camera to be a fair test.

- [TODO 2026-08-16] **Stream camera frames as Arrow buffers instead of file paths.** Captures
  are written to JPEG and passed by path, which wastes dora's zero-copy shared memory. This is
  where the architecture would start paying for itself, and it is the prerequisite for a local
  detector node. Blocked on the camera.

- [TODO 2026-08-16] **Restructure the brain as a behaviour tree (`py_trees`).** Carried over
  from upstream. The brain owns no hardware, so its logic can be replaced without touching any
  other node — this architecture makes it a contained change.

- [TODO 2026-08-16] **Remove `src/server.py`.** The Freenove TCP control path is a
  testing-only holdover with no node using it, kept only because the fork copied `src/` whole.
