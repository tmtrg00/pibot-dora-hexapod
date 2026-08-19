"""Regression check: does a turn settle, or hunt around its target?

Offline. Needs no hardware — it drives the real turn_to against a simulated
robot whose rotation per angle unit can be varied, which is the whole point:
the seed is one measurement from one surface and the loop has to converge on
whatever the robot is really doing today.

This exists because of a specific failure. On 2026-08-19 a 90deg turn arrived
at 89.0deg and then alternated -1, +1, -1 around the target until the stall
guard aborted the run. The owner described it as the robot dancing. The cause
was a stop rule that continued whenever one more cycle promised a closer
landing: the smallest correction the gait can make is about one angle unit,
which is the same size as the residual being chased, so every cycle overshot
and the next was planned to come back.

The metric is REVERSALS — how many times the commanded steering angle changes
sign over a turn. A turn should approach its target from one side and stop.
Every reversal is the robot visibly rocking back the other way, so more than
one is a failure however good the final number looks.

    ./bin/py test/turn_settles.py
"""
import sys, os, types, threading, time, tempfile

ROOT = "/opt/pibot-dora/.claude/worktrees/hexapod-movement-improvements-5ba334"
sys.path.insert(0, os.path.join(ROOT, "nodes"))
import common
common.bootstrap()
os.chdir(tempfile.mkdtemp()); os.makedirs("data", exist_ok=True)

import heading, hardware_node
hardware_node.run_action = lambda *a, **k: "stand (stub)"
CYCLE_S = 0.41
hardware_node.STEER_INTERVAL_S = 0.35 * (CYCLE_S / 2.46)
hardware_node.TURN_POLL_S = 0.08 * (CYCLE_S / 2.46)
hardware_node.HEARTBEAT_S = 1e9
hardware_node.cycle_duration_estimate = lambda g, s: CYCLE_S
heading.save_yaw_sign(-1.0, "test")


class Robot:
    def __init__(self, deg_per_unit=2.7, gyro_sign=-1):
        self.deg_per_unit, self.gyro_sign = deg_per_unit, gyro_sign
        self.command_queue = [''] * 6
        self.timeout = 0
        self.gait_cycles = 0
        self.last_cycle_s = CYCLE_S
        self.heading = 0.0
        self.imu = types.SimpleNamespace(sensor=self)
        self.address, self.bus = 0x68, self
        self._rate = 0.0
        self.angles = []          # every angle a cycle actually ran with
        threading.Thread(target=self._monitor, daemon=True).start()

    def _monitor(self):
        while True:
            q = self.command_queue
            if q and q[0] == "CMD_MOVE" and len(q) == 6:
                if q[2] == "0" and q[3] == "0" and q[5] == "0":
                    self._rate = 0.0
                    self.command_queue = [''] * 6
                    continue
                angle = int(q[5])
                self.angles.append(angle)
                per_cycle = angle * self.deg_per_unit
                self._rate = per_cycle / CYCLE_S
                for _ in range(40):
                    time.sleep(CYCLE_S / 40)
                    self.heading += per_cycle / 40
                self._rate = 0.0
                self.gait_cycles += 1
            else:
                self._rate = 0.0
                time.sleep(0.005)

    def read_i2c_block_data(self, a, r, n):
        v = int(self._rate * self.gyro_sign * heading.GYRO_LSB_PER_DPS)
        return [((max(-32768, min(32767, v)) & 0xFFFF) >> 8) & 0xFF,
                max(-32768, min(32767, v)) & 0xFF]


class HW(hardware_node.Hardware):
    def __init__(self, robot):
        self.control = robot; self.adc = None
        self.last_battery = (7.9, 7.9); self.last_battery_at = time.time()
        self.blocked_reason = None; self.applied_footprint = None
        self.applied_stance = "neutral"; self.applied_z = 0
        self.applied_roll = 0; self.applied_pitch = 0
        self.last_motion_at = time.time(); self.relaxed = False; self.approach = None
    @property
    def hardware_dict(self): return {}
    def read_battery(self, force=False): return (7.9, 7.9)
    def motion_refusal(self, n, a): return None


def reversals(angles):
    signs = [1 if a > 0 else -1 for a in angles if a != 0]
    return sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])


TOL = 5
print(f"{'deg/unit':>9} {'target':>7} {'actual':>8} {'err':>6} {'cycles':>7} "
      f"{'reversals':>10}  angles")
print("-" * 88)
bad = []
for dpu in (2.7, 3.3, 2.0, 5.0):
    for target in (90, -90, 180, 20):
        hw = HW(Robot(dpu))
        text = hw.turn_to(target, TOL)
        r = reversals(hw.control.angles)
        err = hw.control.heading - target
        flag = ""
        if abs(err) > TOL + dpu: flag += " ERR"
        if r > 1: flag += " HUNTS"
        if "aborted" in text: flag += " ABORTED"
        if flag: bad.append((dpu, target, round(err, 1), r, text[:60]))
        print(f"{dpu:>9.1f} {target:>7} {hw.control.heading:>8.1f} {err:>+6.1f} "
              f"{hw.control.gait_cycles:>7} {r:>10}  {hw.control.angles}{flag}")
        assert hw.control.command_queue[0] == "", "gait left running!"

print("-" * 88)
if bad:
    print("PROBLEMS:")
    for b in bad: print("  ", b)
    raise SystemExit(1)
print("PASS - every turn landed in tolerance with at most one reversal, none aborted")
