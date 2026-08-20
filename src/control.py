# -*- coding: utf-8 -*-
import time
import math
import os
import copy
import threading
import numpy as np
from src.pid import Incremental_PID
from src.command import COMMAND as cmd
from src.imu import IMU
from src.servo import Servo
from src.gait_profile import ease, swing_height_a, swing_height_b

# Millimetres the stance feet are commanded BELOW the resting plane, to keep
# them loaded against calibration error and a body that flexes. The original
# gait applied up to ~3mm of this by accident, as rounding error in its
# frame-by-frame height accumulation; computing height from the phase removes
# that, so if the stance legs ever seem to lose contact this is the deliberate
# way to put it back.
GAIT_GROUND_PRESSURE_MM = float(os.environ.get("PIBOT_GAIT_GROUND_PRESSURE_MM", "0"))

# Frames spent easing the feet down when a walk stops, so the swing legs are
# placed rather than dropped from full lift height.
GAIT_SETDOWN_FRAMES = max(1, int(os.environ.get("PIBOT_GAIT_SETDOWN_FRAMES", "8")))

# Target period of one gait frame. The original slept this long AFTER each
# frame's work, so the real period was 10ms plus however long the 18 servo
# writes took — measured at ~35ms on this robot, and varying frame to frame
# with bus contention. Scheduling against an absolute deadline instead makes
# the period what it says it is whenever the work fits inside it, and stops
# early frames from being wasted once the write cache starts skipping writes.
GAIT_FRAME_S = max(0.001, float(os.environ.get("PIBOT_GAIT_FRAME_MS", "10")) / 1000.0)


class Control:
    def __init__(self):
        self.imu = IMU()
        self.servo = Servo()
        # Servo owns GPIO4 (servo power enable).  Expose the same .on()/.off()
        # adapter so actions.py keeps working unchanged.
        self.servo_power_disable = self.servo.servo_power
        self.movement_flag = 0x01
        self.relaxation_flag = False
        # One controller per axis. A single Incremental_PID carries `last_error`
        # and an integral accumulator, so feeding it roll and then pitch made
        # each axis compute its derivative against the OTHER axis's previous
        # error and share one integrator between them. The proportional term
        # happened to survive that; the D and I terms were meaningless.
        self.pid_roll = Incremental_PID(0.500, 0.00, 0.0025)
        self.pid_pitch = Incremental_PID(0.500, 0.00, 0.0025)
        self.status_flag = 0x00
        self.timeout = 0
        self.body_height = -25
        self.body_points = [[137.1, 189.4, self.body_height], [225, 0, self.body_height], [137.1, -189.4, self.body_height], 
                           [-137.1, -189.4, self.body_height], [-225, 0, self.body_height], [-137.1, 189.4, self.body_height]]
        self.calibration_leg_positions = self.read_from_txt('point')
        self.leg_positions = [[140, 0, 0], [140, 0, 0], [140, 0, 0], [140, 0, 0], [140, 0, 0], [140, 0, 0]]
        self.calibration_angles = [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]
        self.current_angles = [[90, 0, 0], [90, 0, 0], [90, 0, 0], [90, 0, 0], [90, 0, 0], [90, 0, 0]]
        self.command_queue = ['', '', '', '', '', '']
        # Gait odometry. run_gait() executes exactly one gait cycle per call,
        # but nothing outside this class could tell when a cycle began or
        # ended, so callers wanting "walk N cycles" had to sleep for an
        # estimated duration and hope. These two make a cycle observable:
        # gait_cycles counts completed cycles, last_cycle_s is how long the
        # most recent one actually took. Both are written only by run_gait and
        # only ever read elsewhere.
        self.gait_cycles = 0
        self.last_cycle_s = 0.0
        # Stride clamp for run_gait, mm. 35 is the upstream hard-coded bound
        # and stays the default: at top cadence the NEUTRAL stance cannot
        # validate much beyond it (the swing leg dips under minimum reach).
        # The sprint raises it temporarily — only after validating the wider
        # stance + stride combination offline — and restores it after.
        self.stride_limit = 35
        # Swing-leg lift for run_gait, mm. 40 is the upstream default (the Z
        # parameter nothing ever passed). The sprint raises it temporarily so
        # fast long strides step clear of the floor instead of skimming it;
        # offline validation shows lift barely moves the reach envelope.
        self.gait_lift = 40
        # True while a tripod is in the air, which is how every gait cycle
        # ends. Lets the next cycle carry straight on from mid-swing, and lets
        # a stop lower those legs instead of dropping them.
        self.swing_raised = False
        # Body-frame height last commanded for each foot. transform_coordinates
        # converts these into the per-leg frame, so leg_positions cannot be read
        # back as heights; recording them here is what lets a set-down start
        # from where the feet actually are.
        self.last_foot_z = [self.body_height] * 6
        # Frames in the last cycle that took longer than the target period, so
        # a cycle that runs slow says why rather than merely being slow.
        self.frames_late = 0
        self.calibrate()
        self.set_leg_angles()
        self.condition_thread = threading.Thread(target=self.condition_monitor, daemon=True)
        self.Thread_conditiona = threading.Condition()

    def read_from_txt(self, filename):
        with open(filename + ".txt", "r") as file:
            lines = file.readlines()
            data = [list(map(int, line.strip().split("\t"))) for line in lines]
        return data

    def save_to_txt(self, data, filename):
        with open(filename + '.txt', 'w') as file:
            for row in data:
                file.write('\t'.join(map(str, row)) + '\n')

    def coordinate_to_angle(self, x, y, z, l1=33, l2=90, l3=110):
        a = math.pi / 2 - math.atan2(z, y)
        x_3 = 0
        x_4 = l1 * math.sin(a)
        x_5 = l1 * math.cos(a)
        l23 = math.sqrt((z - x_5) ** 2 + (y - x_4) ** 2 + (x - x_3) ** 2)
        w = self.restrict_value((x - x_3) / l23, -1, 1)
        v = self.restrict_value((l2 * l2 + l23 * l23 - l3 * l3) / (2 * l2 * l23), -1, 1)
        u = self.restrict_value((l2 ** 2 + l3 ** 2 - l23 ** 2) / (2 * l3 * l2), -1, 1)
        b = math.asin(round(w, 2)) - math.acos(round(v, 2))
        c = math.pi - math.acos(round(u, 2))
        return round(math.degrees(a)), round(math.degrees(b)), round(math.degrees(c))

    def angle_to_coordinate(self, a, b, c, l1=33, l2=90, l3=110):
        a = math.pi / 180 * a
        b = math.pi / 180 * b
        c = math.pi / 180 * c
        x = round(l3 * math.sin(b + c) + l2 * math.sin(b))
        y = round(l3 * math.sin(a) * math.cos(b + c) + l2 * math.sin(a) * math.cos(b) + l1 * math.sin(a))
        z = round(l3 * math.cos(a) * math.cos(b + c) + l2 * math.cos(a) * math.cos(b) + l1 * math.cos(a))
        return x, y, z

    def calibrate(self):
        self.leg_positions = [[140, 0, 0], [140, 0, 0], [140, 0, 0], [140, 0, 0], [140, 0, 0], [140, 0, 0]]
        for i in range(6):
            self.calibration_angles[i][0], self.calibration_angles[i][1], self.calibration_angles[i][2] = self.coordinate_to_angle(
                -self.calibration_leg_positions[i][2], self.calibration_leg_positions[i][0], self.calibration_leg_positions[i][1])
        for i in range(6):
            self.current_angles[i][0], self.current_angles[i][1], self.current_angles[i][2] = self.coordinate_to_angle(
                -self.leg_positions[i][2], self.leg_positions[i][0], self.leg_positions[i][1])
        for i in range(6):
            self.calibration_angles[i][0] = self.calibration_angles[i][0] - self.current_angles[i][0]
            self.calibration_angles[i][1] = self.calibration_angles[i][1] - self.current_angles[i][1]
            self.calibration_angles[i][2] = self.calibration_angles[i][2] - self.current_angles[i][2]

    def set_leg_angles(self):
        if self.check_point_validity():
            for i in range(6):
                self.current_angles[i][0], self.current_angles[i][1], self.current_angles[i][2] = self.coordinate_to_angle(
                    -self.leg_positions[i][2], self.leg_positions[i][0], self.leg_positions[i][1])
            for i in range(3):
                self.current_angles[i][0] = self.restrict_value(self.current_angles[i][0] + self.calibration_angles[i][0], 0, 180)
                self.current_angles[i][1] = self.restrict_value(90 - (self.current_angles[i][1] + self.calibration_angles[i][1]), 0, 180)
                self.current_angles[i][2] = self.restrict_value(self.current_angles[i][2] + self.calibration_angles[i][2], 0, 180)
                self.current_angles[i + 3][0] = self.restrict_value(self.current_angles[i + 3][0] + self.calibration_angles[i + 3][0], 0, 180)
                self.current_angles[i + 3][1] = self.restrict_value(90 + self.current_angles[i + 3][1] + self.calibration_angles[i + 3][1], 0, 180)
                self.current_angles[i + 3][2] = self.restrict_value(180 - (self.current_angles[i + 3][2] + self.calibration_angles[i + 3][2]), 0, 180)
            # Leg 1
            self.servo.set_servo_angle(15, self.current_angles[0][0])
            self.servo.set_servo_angle(14, self.current_angles[0][1])
            self.servo.set_servo_angle(13, self.current_angles[0][2])
            # Leg 2
            self.servo.set_servo_angle(12, self.current_angles[1][0])
            self.servo.set_servo_angle(11, self.current_angles[1][1])
            self.servo.set_servo_angle(10, self.current_angles[1][2])
            # Leg 3
            self.servo.set_servo_angle(9, self.current_angles[2][0])
            self.servo.set_servo_angle(8, self.current_angles[2][1])
            self.servo.set_servo_angle(31, self.current_angles[2][2])
            # Leg 6
            self.servo.set_servo_angle(16, self.current_angles[5][0])
            self.servo.set_servo_angle(17, self.current_angles[5][1])
            self.servo.set_servo_angle(18, self.current_angles[5][2])
            # Leg 5
            self.servo.set_servo_angle(19, self.current_angles[4][0])
            self.servo.set_servo_angle(20, self.current_angles[4][1])
            self.servo.set_servo_angle(21, self.current_angles[4][2])
            # Leg 4
            self.servo.set_servo_angle(22, self.current_angles[3][0])
            self.servo.set_servo_angle(23, self.current_angles[3][1])
            self.servo.set_servo_angle(27, self.current_angles[3][2])
        else:
            print("This coordinate point is out of the active range")

    def check_point_validity(self):
        is_valid = True
        leg_lengths = [0] * 6
        for i in range(6):
            leg_lengths[i] = math.sqrt(self.leg_positions[i][0] ** 2 + self.leg_positions[i][1] ** 2 + self.leg_positions[i][2] ** 2)
        for length in leg_lengths:
            if length > 248 or length < 90:
                is_valid = False
        return is_valid

    def condition_monitor(self):
        while True:
            if (time.time() - self.timeout) > 10 and self.timeout != 0 and self.command_queue[0] == '':
                self.timeout = time.time()
                self.relax(True)
                self.status_flag = 0x00
            if cmd.CMD_POSITION in self.command_queue and len(self.command_queue) == 4:
                if self.status_flag != 0x01:
                    self.relax(False)
                x = self.restrict_value(int(self.command_queue[1]), -40, 40)
                y = self.restrict_value(int(self.command_queue[2]), -40, 40)
                z = self.restrict_value(int(self.command_queue[3]), -20, 20)
                self.move_position(x, y, z)
                self.status_flag = 0x01
                self.command_queue = ['', '', '', '', '', '']
            elif cmd.CMD_ATTITUDE in self.command_queue and len(self.command_queue) == 4:
                if self.status_flag != 0x02:
                    self.relax(False)
                roll = self.restrict_value(int(self.command_queue[1]), -15, 15)
                pitch = self.restrict_value(int(self.command_queue[2]), -15, 15)
                yaw = self.restrict_value(int(self.command_queue[3]), -15, 15)
                points = self.calculate_posture_balance(roll, pitch, yaw)
                self.transform_coordinates(points)
                self.set_leg_angles()
                self.status_flag = 0x02
                self.command_queue = ['', '', '', '', '', '']
            elif cmd.CMD_MOVE in self.command_queue and len(self.command_queue) == 6:
                # Single-shot only for the "stop and stand" form: no stride AND
                # no rotation. Everything that actually moves the robot —
                # including a turn in place, which has no stride but does have
                # an angle — leaves the command queued so run_gait is re-entered
                # cycle after cycle.
                #
                # This used to test the stride alone, which put turns in the
                # single-shot branch: one cycle, then the queue was cleared.
                # Two consequences, both bad. `steps` was silently ignored for
                # turns, since nothing re-queued the command. And a multi-cycle
                # turn had to be driven from outside as a sequence of separate
                # commands, each waiting for the queue to clear, so the robot
                # turned in visible discrete lurches instead of rotating
                # smoothly (owner observation, 2026-08-19).
                if (self.command_queue[2] == "0" and self.command_queue[3] == "0"
                        and self.command_queue[5] == "0"):
                    self.run_gait(self.command_queue)
                    self.command_queue = ['', '', '', '', '', '']
                else:
                    if self.status_flag != 0x03:
                        self.relax(False)
                    self.run_gait(self.command_queue)
                    self.status_flag = 0x03
            elif cmd.CMD_BALANCE in self.command_queue and len(self.command_queue) == 2:
                if self.command_queue[1] == "1":
                    self.command_queue = ['', '', '', '', '', '']
                    if self.status_flag != 0x04:
                        self.relax(False)
                    self.status_flag = 0x04
                    self.imu6050()
            elif cmd.CMD_CALIBRATION in self.command_queue:
                self.timeout = 0
                self.calibrate()
                self.set_leg_angles()
                if len(self.command_queue) >= 2:
                    if self.command_queue[1] == "one":
                        self.calibration_leg_positions[0][0] = int(self.command_queue[2])
                        self.calibration_leg_positions[0][1] = int(self.command_queue[3])
                        self.calibration_leg_positions[0][2] = int(self.command_queue[4])
                        self.calibrate()
                        self.set_leg_angles()
                    elif self.command_queue[1] == "two":
                        self.calibration_leg_positions[1][0] = int(self.command_queue[2])
                        self.calibration_leg_positions[1][1] = int(self.command_queue[3])
                        self.calibration_leg_positions[1][2] = int(self.command_queue[4])
                        self.calibrate()
                        self.set_leg_angles()
                    elif self.command_queue[1] == "three":
                        self.calibration_leg_positions[2][0] = int(self.command_queue[2])
                        self.calibration_leg_positions[2][1] = int(self.command_queue[3])
                        self.calibration_leg_positions[2][2] = int(self.command_queue[4])
                        self.calibrate()
                        self.set_leg_angles()
                    elif self.command_queue[1] == "four":
                        self.calibration_leg_positions[3][0] = int(self.command_queue[2])
                        self.calibration_leg_positions[3][1] = int(self.command_queue[3])
                        self.calibration_leg_positions[3][2] = int(self.command_queue[4])
                        self.calibrate()
                        self.set_leg_angles()
                    elif self.command_queue[1] == "five":
                        self.calibration_leg_positions[4][0] = int(self.command_queue[2])
                        self.calibration_leg_positions[4][1] = int(self.command_queue[3])
                        self.calibration_leg_positions[4][2] = int(self.command_queue[4])
                        self.calibrate()
                        self.set_leg_angles()
                    elif self.command_queue[1] == "six":
                        self.calibration_leg_positions[5][0] = int(self.command_queue[2])
                        self.calibration_leg_positions[5][1] = int(self.command_queue[3])
                        self.calibration_leg_positions[5][2] = int(self.command_queue[4])
                        self.calibrate()
                        self.set_leg_angles()
                    elif self.command_queue[1] == "save":
                        self.save_to_txt(self.calibration_leg_positions, 'point')
                self.command_queue = ['', '', '', '', '', '']
            else:
                time.sleep(0.005)  # Yield GIL when idle to prevent CPU spin

    def relax(self, flag):
        if flag:
            self.servo.relax()
        else:
            self.set_leg_angles()

    def transform_coordinates(self, points):
        self.last_foot_z = [p[2] for p in points]
        # Leg 1
        self.leg_positions[0][0] = points[0][0] * math.cos(54 / 180 * math.pi) + points[0][1] * math.sin(54 / 180 * math.pi) - 94
        self.leg_positions[0][1] = -points[0][0] * math.sin(54 / 180 * math.pi) + points[0][1] * math.cos(54 / 180 * math.pi)
        self.leg_positions[0][2] = points[0][2] - 14
        # Leg 2
        self.leg_positions[1][0] = points[1][0] * math.cos(0 / 180 * math.pi) + points[1][1] * math.sin(0 / 180 * math.pi) - 85
        self.leg_positions[1][1] = -points[1][0] * math.sin(0 / 180 * math.pi) + points[1][1] * math.cos(0 / 180 * math.pi)
        self.leg_positions[1][2] = points[1][2] - 14
        # Leg 3
        self.leg_positions[2][0] = points[2][0] * math.cos(-54 / 180 * math.pi) + points[2][1] * math.sin(-54 / 180 * math.pi) - 94
        self.leg_positions[2][1] = -points[2][0] * math.sin(-54 / 180 * math.pi) + points[2][1] * math.cos(-54 / 180 * math.pi)
        self.leg_positions[2][2] = points[2][2] - 14
        # Leg 4
        self.leg_positions[3][0] = points[3][0] * math.cos(-126 / 180 * math.pi) + points[3][1] * math.sin(-126 / 180 * math.pi) - 94
        self.leg_positions[3][1] = -points[3][0] * math.sin(-126 / 180 * math.pi) + points[3][1] * math.cos(-126 / 180 * math.pi)
        self.leg_positions[3][2] = points[3][2] - 14
        # Leg 5
        self.leg_positions[4][0] = points[4][0] * math.cos(180 / 180 * math.pi) + points[4][1] * math.sin(180 / 180 * math.pi) - 85
        self.leg_positions[4][1] = -points[4][0] * math.sin(180 / 180 * math.pi) + points[4][1] * math.cos(180 / 180 * math.pi)
        self.leg_positions[4][2] = points[4][2] - 14
        # Leg 6
        self.leg_positions[5][0] = points[5][0] * math.cos(126 / 180 * math.pi) + points[5][1] * math.sin(126 / 180 * math.pi) - 94
        self.leg_positions[5][1] = -points[5][0] * math.sin(126 / 180 * math.pi) + points[5][1] * math.cos(126 / 180 * math.pi)
        self.leg_positions[5][2] = points[5][2] - 14

    def restrict_value(self, value, min_value, max_value):
        if value < min_value:
            return min_value
        elif value > max_value:
            return max_value
        else:
            return value

    def map_value(self, value, from_low, from_high, to_low, to_high):
        return (to_high - to_low) * (value - from_low) / (from_high - from_low) + to_low

    def move_position(self, x, y, z):
        points = copy.deepcopy(self.body_points)
        for i in range(6):
            points[i][0] = self.body_points[i][0] - x
            points[i][1] = self.body_points[i][1] - y
            points[i][2] = -30 - z
            self.body_height = points[i][2]
            self.body_points[i][2] = points[i][2]
        self.transform_coordinates(points)
        self.set_leg_angles()

    def calculate_posture_balance(self, roll, pitch, yaw):
        position = np.asmatrix([0.0, 0.0, self.body_height]).T
        rpy = np.array([roll, pitch, yaw]) * math.pi / 180
        roll_angle, pitch_angle, yaw_angle = rpy[0], rpy[1], rpy[2]
        # Roll turns about X, pitch about Y. These two were swapped: the `roll`
        # argument was building the X matrix from `pitch_angle` and vice versa,
        # so set_attitude(roll=10) tilted the robot nose-down and the tilted
        # stances leaned sideways instead of forward. Confirmed by computing
        # the commanded foot heights: `roll` moved the nose and tail legs,
        # `pitch` moved the two side legs (2026-08-19).
        #
        # The IMU balance loop was accidentally unharmed, because it also
        # unpacked update_imu_state() in the wrong order and the two errors
        # cancelled. That call is corrected alongside this, so the pair stays
        # consistent -- fixing either one alone would have broken balancing.
        rotation_x = np.asmatrix([[1, 0, 0],
                             [0, math.cos(roll_angle), -math.sin(roll_angle)],
                             [0, math.sin(roll_angle), math.cos(roll_angle)]])
        rotation_y = np.asmatrix([[math.cos(pitch_angle), 0, -math.sin(pitch_angle)],
                             [0, 1, 0],
                             [math.sin(pitch_angle), 0, math.cos(pitch_angle)]])
        rotation_z = np.asmatrix([[math.cos(yaw_angle), -math.sin(yaw_angle), 0],
                             [math.sin(yaw_angle), math.cos(yaw_angle), 0],
                             [0, 0, 1]])
        rotation_matrix = rotation_x * rotation_y * rotation_z
        body_structure = np.asmatrix([[55, 76, 0],
                                [85, 0, 0],
                                [55, -76, 0],
                                [-55, -76, 0],
                                [-85, 0, 0],
                                [-55, 76, 0]]).T
        footpoint_structure = np.asmatrix([[137.1, 189.4, 0],
                                     [225, 0, 0],
                                     [137.1, -189.4, 0],
                                     [-137.1, -189.4, 0],
                                     [-225, 0, 0],
                                     [-137.1, 189.4, 0]]).T
        ab = np.asmatrix(np.zeros((3, 6)))
        foot_positions = [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]
        for i in range(6):
            ab[:, i] = position + rotation_matrix * footpoint_structure[:, i]
            foot_positions[i][0] = ab[0, i]
            foot_positions[i][1] = ab[1, i]
            foot_positions[i][2] = ab[2, i]
        return foot_positions

    def imu6050(self):
        old_roll = 0
        old_pitch = 0
        points = self.calculate_posture_balance(0, 0, 0)
        self.transform_coordinates(points)
        self.set_leg_angles()
        time.sleep(2)
        self.imu.Error_value_accel_data, self.imu.Error_value_gyro_data = self.imu.calculate_average_sensor_data()
        time.sleep(1)
        while True:
            if self.command_queue[0] != "":
                break
            time.sleep(0.02)
            # update_imu_state returns (pitch, roll, yaw) -- see the return
            # statement in src/imu.py. Unpacking it as roll-first swapped the
            # two, which used to cancel the swap in calculate_posture_balance.
            # Both are now correct, so they no longer need to cancel.
            pitch, roll, yaw = self.imu.update_imu_state()
            roll = self.pid_roll.pid_calculate(roll)
            pitch = self.pid_pitch.pid_calculate(pitch)
            points = self.calculate_posture_balance(roll, pitch, 0)
            self.transform_coordinates(points)
            self.set_leg_angles()

    # The height profile lives in src/gait_profile.py so nodes/stances.py can
    # replay it offline without importing the drivers. These two are kept as
    # methods because that is how run_gait reads, and because subclassing or
    # patching the profile stays possible.
    def _swing_height_a(self, j, F):
        return swing_height_a(j, F)

    def _swing_height_b(self, j, F, first_cycle):
        return swing_height_b(j, F, first_cycle)

    def set_feet_down(self, frames=None):
        """Ease every foot onto the resting plane from wherever it is.

        A walk ends with one tripod still in the air. The stop command rebuilds
        the feet on the resting footprint and commands it in one frame, which
        drops those three legs the full 40mm — a thump at the end of every
        walk, and the mirror image of the lurch at the start.
        """
        frames = GAIT_SETDOWN_FRAMES if frames is None else max(1, int(frames))
        start = list(self.last_foot_z)
        target = copy.deepcopy(self.body_points)
        for k in range(1, frames + 1):
            fraction = ease(k / frames)
            points = copy.deepcopy(target)
            for i in range(6):
                points[i][2] = target[i][2] + (start[i] - target[i][2]) * (1.0 - fraction)
            self.transform_coordinates(points)
            self.set_leg_angles()
            time.sleep(0.01)
        self.swing_raised = False

    def run_gait(self, data, Z=None, F=64):  # Example: data=['CMD_MOVE', '1', '0', '25', '10', '0']
        if Z is None:
            Z = getattr(self, "gait_lift", 40)
        gait = data[1]
        x = self.restrict_value(int(data[2]), -self.stride_limit, self.stride_limit)
        y = self.restrict_value(int(data[3]), -self.stride_limit, self.stride_limit)
        if gait == "1":
            F = round(self.map_value(int(data[4]), 2, 10, 126, 22))
        else:
            F = round(self.map_value(int(data[4]), 2, 10, 171, 45))
        angle = int(data[5])
        z = Z / F
        delay = GAIT_FRAME_S
        points = copy.deepcopy(self.body_points)
        xy = [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]]
        for i in range(6):
            xy[i][0] = ((points[i][0] * math.cos(angle / 180 * math.pi) + points[i][1] * math.sin(angle / 180 * math.pi) - points[i][0]) + x) / F
            xy[i][1] = ((-points[i][0] * math.sin(angle / 180 * math.pi) + points[i][1] * math.cos(angle / 180 * math.pi) - points[i][1]) + y) / F
        cycle_started = time.time()
        first_cycle = not self.swing_raised
        if x == 0 and y == 0 and angle == 0:
            # Not a gait cycle: this is the "stop and stand" form, which just
            # puts the feet back on the resting footprint. It is deliberately
            # not counted, so gait_cycles stays a count of cycles *travelled*.
            if self.swing_raised:
                self.set_feet_down()
            else:
                self.transform_coordinates(points)
                self.set_leg_angles()
        elif gait == "1":
            # Horizontal motion is unchanged from the original tripod gait.
            # Foot HEIGHT is computed from the phase rather than accumulated
            # frame by frame, for two reasons.
            #
            # Shape. The original raised and lowered a foot at a constant rate,
            # so vertical velocity stepped from 0 to ~430mm/s and back to 0 at
            # each phase boundary — an impulsive acceleration at exactly the
            # two moments that matter, lift-off and touchdown. That is what
            # makes a foot slap the floor and jars the body. `swing_height`
            # below eases in and out, so the foot leaves and meets the ground
            # with near-zero vertical speed. Same lift, same timing, same
            # endpoints; only the shape between them changes.
            #
            # Accumulation. Phase boundaries fall on fractions of F, but frames
            # are whole numbers, so a phase rarely contains exactly F/8 frames.
            # The old per-frame increments therefore over- or under-shot the
            # 40mm lift by a few percent, leaving a stance foot commanded up to
            # ~3mm below the resting plane. Computing height from the phase
            # cannot drift.
            frame_due = time.time()
            self.frames_late = 0
            for j in range(F):
                stance_z = self.body_height - GAIT_GROUND_PRESSURE_MM
                lift_a = self.body_height + Z * self._swing_height_a(j, F)
                lift_b = self.body_height + Z * self._swing_height_b(j, F, first_cycle)
                for i in range(3):
                    a, b = 2 * i, 2 * i + 1
                    if j < (F / 8):
                        points[a][0] = points[a][0] - 4 * xy[a][0]
                        points[a][1] = points[a][1] - 4 * xy[a][1]
                        points[b][0] = points[b][0] + 8 * xy[b][0]
                        points[b][1] = points[b][1] + 8 * xy[b][1]
                    elif j < (F / 4):
                        points[a][0] = points[a][0] - 4 * xy[a][0]
                        points[a][1] = points[a][1] - 4 * xy[a][1]
                    elif j < (3 * F / 8):
                        points[b][0] = points[b][0] - 4 * xy[b][0]
                        points[b][1] = points[b][1] - 4 * xy[b][1]
                    elif j < (5 * F / 8):
                        points[a][0] = points[a][0] + 8 * xy[a][0]
                        points[a][1] = points[a][1] + 8 * xy[a][1]
                        points[b][0] = points[b][0] - 4 * xy[b][0]
                        points[b][1] = points[b][1] - 4 * xy[b][1]
                    elif j < (3 * F / 4):
                        points[b][0] = points[b][0] - 4 * xy[b][0]
                        points[b][1] = points[b][1] - 4 * xy[b][1]
                    elif j < (7 * F / 8):
                        points[a][0] = points[a][0] - 4 * xy[a][0]
                        points[a][1] = points[a][1] - 4 * xy[a][1]
                    elif j < (F):
                        points[a][0] = points[a][0] - 4 * xy[a][0]
                        points[a][1] = points[a][1] - 4 * xy[a][1]
                        points[b][0] = points[b][0] + 8 * xy[b][0]
                        points[b][1] = points[b][1] + 8 * xy[b][1]
                    points[a][2] = lift_a if lift_a > self.body_height else stance_z
                    points[b][2] = lift_b if lift_b > self.body_height else stance_z
                self.transform_coordinates(points)
                self.set_leg_angles()
                # Sleep until this frame's deadline rather than for a fixed
                # period, so a frame that finished quickly gives its time back
                # to the schedule instead of extending the cycle. A frame that
                # overran gets no sleep and the deadline is reset to now, so
                # lateness is never carried forward and compounded.
                frame_due += delay
                remaining = frame_due - time.time()
                if remaining > 0:
                    time.sleep(remaining)
                else:
                    self.frames_late += 1
                    frame_due = time.time()
            # The B group ends a cycle in the air, mid-swing. Recording that
            # lets the next cycle carry straight on, and lets a stop lower the
            # feet instead of dropping them.
            self.swing_raised = True
            self.last_cycle_s = time.time() - cycle_started
            self.gait_cycles += 1
        elif gait == "2":
            number = [5, 2, 1, 0, 3, 4]
            for i in range(6):
                for j in range(int(F / 6)):
                    for k in range(6):
                        if number[i] == k:
                            if j < int(F / 18):
                                points[k][2] += 18 * z
                            elif j < int(F / 9):
                                points[k][0] += 30 * xy[k][0]
                                points[k][1] += 30 * xy[k][1]
                            elif j < int(F / 6):
                                points[k][2] -= 18 * z
                        else:
                            points[k][0] -= 2 * xy[k][0]
                            points[k][1] -= 2 * xy[k][1]
                    self.transform_coordinates(points)
                    self.set_leg_angles()
                    time.sleep(delay)
            self.last_cycle_s = time.time() - cycle_started
            self.gait_cycles += 1

if __name__ == '__main__':
    pass
