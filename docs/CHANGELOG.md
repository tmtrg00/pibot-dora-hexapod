# CHANGELOG — PiBot-Dora

Append-only. Newest entry at the top. Bugs and decisions are history and live here; open work
lives in PENDING; procedures live in RUNBOOKS. Superseding a decision takes a new dated entry
whose `**Decision:**` line says so and references the prior date — never edit or silently
revert an old entry.

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
