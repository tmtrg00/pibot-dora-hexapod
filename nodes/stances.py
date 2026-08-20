"""Named hexapod stances, with offline reach validation.

A stance is a named body pose: how tall the robot stands, how wide it plants
its feet, and how it is tilted. Upstream exposes height and tilt piecemeal
(`set_position` z, `set_attitude` roll/pitch) but never the *footprint*, and
has no concept of a named pose. This module adds both.

Why footprint matters: `Control.body_points` is the resting position of the six
feet, and `run_gait` deep-copies it as the base for every step. So widening the
footprint widens the stance *and* the walking gait — one knob for stability.

The safety issue this module exists to solve
--------------------------------------------
`Control.set_leg_angles()` calls `check_point_validity()` first, and if any leg
would need to reach beyond 90..248mm it prints a line and **returns without
moving anything**. No exception, no return value. So an out-of-range stance is
a silent no-op — exactly the failure class that has bitten this project before.

Rather than discover that on the robot, every stance here is checked against
the same reach maths offline. `validate()` replicates `transform_coordinates()`
and `check_point_validity()` so a stance can be rejected before any servo is
asked to do anything.

The geometry constants below mirror `src/control.py`. `verify_against(control)`
cross-checks them against a live Control instance so the mirror cannot drift
unnoticed.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Dict, List, Tuple

# Runnable standalone (`python nodes/stances.py`) as well as importable from a
# node, so put the project root on the path for the gait-profile import below.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Resting foot positions, mirroring Control.body_points (x, y only; z comes
# from the height term). Six legs, front-right going clockwise.
BASE_FOOTPRINT: List[List[float]] = [
    [137.1, 189.4],
    [225.0, 0.0],
    [137.1, -189.4],
    [-137.1, -189.4],
    [-225.0, 0.0],
    [-137.1, 189.4],
]

# Per-leg mounting angle and body-offset, mirroring transform_coordinates().
LEG_TRANSFORMS: List[Tuple[float, float]] = [
    (54.0, 94.0),
    (0.0, 85.0),
    (-54.0, 94.0),
    (-126.0, 94.0),
    (180.0, 85.0),
    (126.0, 94.0),
]

# Reach limits from check_point_validity().
MIN_REACH_MM = 90.0
MAX_REACH_MM = 248.0

# move_position() computes foot z as (-30 - z), and transform subtracts 14.
Z_BASE_MM = -30.0
Z_LEG_OFFSET_MM = -14.0

# `set_position` clamps z to -20..20 in src/actions.py.
Z_MIN, Z_MAX = -20, 20

# Keep a margin off the hard reach limits. Landing within a millimetre of the
# limit is fine in arithmetic and marginal on a real robot with calibration
# error, servo slop and a body that flexes under load.
REACH_MARGIN_MM = 8.0


class Stance:
    __slots__ = ("name", "spread", "z", "roll", "pitch", "description")

    def __init__(self, name, spread, z, roll=0, pitch=0, description=""):
        self.name = name
        self.spread = float(spread)   # footprint scale, 1.0 = stock
        self.z = int(z)               # height offset, +ve = taller
        self.roll = int(roll)
        self.pitch = int(pitch)
        self.description = description

    def footprint(self) -> List[List[float]]:
        return [[x * self.spread, y * self.spread] for x, y in BASE_FOOTPRINT]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "spread": self.spread,
            "z": self.z,
            "roll": self.roll,
            "pitch": self.pitch,
            "description": self.description,
        }


STANCES: Dict[str, Stance] = {
    s.name: s
    for s in [
        Stance("neutral", 1.00, 0, 0, 0, "Stock standing pose"),
        Stance("crouch", 1.00, -15, 0, 0, "Body low, centre of gravity dropped"),
        Stance("tall", 1.00, 15, 0, 0, "Body raised to clear obstacles"),
        Stance("wide", 1.12, 0, 0, 0, "Feet planted wider, most stable"),
        Stance("narrow", 0.90, 0, 0, 0, "Feet tucked in, compact footprint"),
        Stance("brace", 1.12, -12, 0, 0, "Wide and low, maximum stability"),
        Stance("alert", 1.00, 10, 0, -6, "Raised with a slight nose-up tilt"),
        Stance("lean_forward", 1.00, 0, 0, 8, "Tilted nose-down, ready to advance"),
    ]
}


def leg_reach(footprint: List[List[float]], z: int) -> List[float]:
    """Distance each leg must reach for this footprint and height.

    Replicates transform_coordinates() followed by the length calculation in
    check_point_validity().
    """
    foot_z = Z_BASE_MM - z + Z_LEG_OFFSET_MM
    reaches = []
    for (x, y), (angle_deg, offset) in zip(footprint, LEG_TRANSFORMS):
        a = math.radians(angle_deg)
        lx = x * math.cos(a) + y * math.sin(a) - offset
        ly = -x * math.sin(a) + y * math.cos(a)
        reaches.append(math.sqrt(lx * lx + ly * ly + foot_z * foot_z))
    return reaches


def validate(stance: Stance, margin: float = REACH_MARGIN_MM) -> Tuple[bool, List[float], str]:
    """Check a stance is reachable. Returns (ok, per-leg reaches, reason)."""
    if not (Z_MIN <= stance.z <= Z_MAX):
        return False, [], f"z={stance.z} outside the {Z_MIN}..{Z_MAX} range set_position allows"

    # Spread and tilt cannot be combined. CMD_ATTITUDE is served by
    # calculate_posture_balance(), which builds foot positions from its OWN
    # hardcoded footpoint_structure rather than from Control.body_points. So
    # applying a tilt silently reverts the footprint to stock geometry, and the
    # robot would end up tilted at normal width while still believing it is
    # wide. Reject the combination rather than ship that surprise.
    if abs(stance.spread - 1.0) > 1e-6 and (stance.roll or stance.pitch):
        return False, [], (
            "spread and tilt cannot be combined: CMD_ATTITUDE rebuilds foot "
            "positions from a hardcoded footprint, discarding the spread"
        )

    reaches = leg_reach(stance.footprint(), stance.z)
    low, high = MIN_REACH_MM + margin, MAX_REACH_MM - margin

    worst_low = min(reaches)
    worst_high = max(reaches)
    if worst_high > high:
        return False, reaches, (
            f"leg reach {worst_high:.1f}mm exceeds {high:.1f}mm "
            f"(hard limit {MAX_REACH_MM:.0f}mm, margin {margin:.0f}mm)"
        )
    if worst_low < low:
        return False, reaches, (
            f"leg reach {worst_low:.1f}mm below {low:.1f}mm "
            f"(hard limit {MIN_REACH_MM:.0f}mm, margin {margin:.0f}mm)"
        )
    return True, reaches, "ok"


# run_gait lifts the swing legs by this much (the Z default in run_gait).
GAIT_LIFT_MM = 40.0

# The foot-height profile is imported, not reimplemented. The horizontal
# arithmetic below IS a mirror of run_gait and has to be maintained as one,
# which is why this module carries `verify_against` as a drift check. The
# height half needs no such check because there is only one definition of it.
# src/gait_profile.py deliberately imports no drivers, so this module stays
# usable without lgpio or a live I2C bus.
from src.gait_profile import swing_height_a, swing_height_b  # noqa: E402


def simulate_gait_reach(
    stance: Stance, x: int = 0, y: int = 0, angle: int = 0, speed: int = 6,
    lift: float = GAIT_LIFT_MM,
) -> Tuple[float, float]:
    """Worst-case leg reach over one tripod gait cycle in this stance, offline.

    The static `validate()` check covers the resting pose only. Walking is
    harder on reach: run_gait deep-copies `body_points` (so a wide stance
    starts every leg further out), shifts feet by up to the full stride, and
    lifts swing legs 40mm — any of which can push a leg outside the 90..248mm
    window mid-cycle. When that happens `set_leg_angles()` skips the frame
    silently, which on a real robot looks like a stutter or a dragged foot.

    This replicates the tripod ("1") gait arithmetic from `Control.run_gait`
    frame by frame — same phase structure, same 4x/8x step factors, same lift —
    and returns (min_reach, max_reach) across every leg and frame, so a
    stance+direction combination can be rejected before a servo moves.
    The wave gait ("2") is not simulated; the test graphs drive gait 1.
    """
    f_frames = round((22 - 126) * (speed - 2) / (10 - 2) + 126)  # map_value(speed, 2,10, 126,22)
    body_height = Z_BASE_MM - stance.z
    points = [[fx, fy, body_height] for fx, fy in stance.footprint()]

    rad = math.radians(angle)
    xy = [
        [
            ((px * math.cos(rad) + py * math.sin(rad) - px) + x) / f_frames,
            ((-px * math.sin(rad) + py * math.cos(rad) - py) + y) / f_frames,
        ]
        for px, py, _ in points
    ]

    def frame_reaches() -> List[float]:
        out = []
        for (px, py, pz), (ang_deg, offset) in zip(points, LEG_TRANSFORMS):
            a = math.radians(ang_deg)
            lx = px * math.cos(a) + py * math.sin(a) - offset
            ly = -px * math.sin(a) + py * math.cos(a)
            lz = pz + Z_LEG_OFFSET_MM
            out.append(math.sqrt(lx * lx + ly * ly + lz * lz))
        return out

    lo = min(frame_reaches())
    hi = max(frame_reaches())
    for j in range(f_frames):
        # Height from the phase, calling the gait engine's own profile. Worst
        # case for reach is a swing that begins already raised, so first_cycle
        # is False.
        lift_a = body_height + lift * swing_height_a(j, f_frames)
        lift_b = body_height + lift * swing_height_b(j, f_frames, False)
        for i in range(3):
            if j < (f_frames / 8):
                points[2 * i][0] -= 4 * xy[2 * i][0]
                points[2 * i][1] -= 4 * xy[2 * i][1]
                points[2 * i + 1][0] += 8 * xy[2 * i + 1][0]
                points[2 * i + 1][1] += 8 * xy[2 * i + 1][1]
            elif j < (f_frames / 4):
                points[2 * i][0] -= 4 * xy[2 * i][0]
                points[2 * i][1] -= 4 * xy[2 * i][1]
            elif j < (3 * f_frames / 8):
                points[2 * i + 1][0] -= 4 * xy[2 * i + 1][0]
                points[2 * i + 1][1] -= 4 * xy[2 * i + 1][1]
            elif j < (5 * f_frames / 8):
                points[2 * i][0] += 8 * xy[2 * i][0]
                points[2 * i][1] += 8 * xy[2 * i][1]
                points[2 * i + 1][0] -= 4 * xy[2 * i + 1][0]
                points[2 * i + 1][1] -= 4 * xy[2 * i + 1][1]
            elif j < (3 * f_frames / 4):
                points[2 * i + 1][0] -= 4 * xy[2 * i + 1][0]
                points[2 * i + 1][1] -= 4 * xy[2 * i + 1][1]
            elif j < (7 * f_frames / 8):
                points[2 * i][0] -= 4 * xy[2 * i][0]
                points[2 * i][1] -= 4 * xy[2 * i][1]
            else:
                points[2 * i][0] -= 4 * xy[2 * i][0]
                points[2 * i][1] -= 4 * xy[2 * i][1]
                points[2 * i + 1][0] += 8 * xy[2 * i + 1][0]
                points[2 * i + 1][1] += 8 * xy[2 * i + 1][1]
            points[2 * i][2] = lift_a
            points[2 * i + 1][2] = lift_b
        reaches = frame_reaches()
        lo = min(lo, min(reaches))
        hi = max(hi, max(reaches))
    return lo, hi


def validate_for_gait(
    stance: Stance,
    x: int = 0,
    y: int = 0,
    angle: int = 0,
    speed: int = 6,
    margin: float = REACH_MARGIN_MM,
    lift: float = GAIT_LIFT_MM,
) -> Tuple[bool, float, float, str]:
    """Check a stance stays reachable while walking. (ok, min, max, reason)."""
    ok, _, reason = validate(stance, margin)
    if not ok:
        return False, 0.0, 0.0, f"static pose already invalid: {reason}"
    lo, hi = simulate_gait_reach(stance, x, y, angle, speed, lift)
    low, high = MIN_REACH_MM + margin, MAX_REACH_MM - margin
    if hi > high:
        return False, lo, hi, (
            f"mid-gait leg reach {hi:.1f}mm exceeds {high:.1f}mm "
            f"(hard limit {MAX_REACH_MM:.0f}mm, margin {margin:.0f}mm)"
        )
    if lo < low:
        return False, lo, hi, (
            f"mid-gait leg reach {lo:.1f}mm below {low:.1f}mm "
            f"(hard limit {MIN_REACH_MM:.0f}mm, margin {margin:.0f}mm)"
        )
    return True, lo, hi, "ok"


def verify_against(control, expected: List[List[float]] = None) -> Tuple[bool, str]:
    """Cross-check the mirrored geometry against a live Control instance.

    `expected` is the footprint the caller last applied (None means the stock
    BASE_FOOTPRINT). Comparing against the *applied* footprint matters: after
    a spread stance, control.body_points legitimately holds the scaled values,
    and comparing those against BASE_FOOTPRINT misreads a current stance as
    drift — which is exactly how the first stancewalk run got stuck in `wide`
    (2026-08-18).
    """
    reference = expected if expected is not None else BASE_FOOTPRINT
    try:
        live = [[p[0], p[1]] for p in control.body_points]
    except Exception as exc:
        return False, f"could not read control.body_points: {exc}"
    for i, ((bx, by), (lx, ly)) in enumerate(zip(reference, live)):
        if abs(bx - lx) > 0.5 or abs(by - ly) > 0.5:
            return False, (
                f"leg {i} footprint drifted: expected ({bx:.1f}, {by:.1f}), "
                f"control.py has ({lx:.1f}, {ly:.1f}). Update BASE_FOOTPRINT "
                f"if control.py's geometry changed."
            )
    return True, "geometry matches control.py"


if __name__ == "__main__":
    print(f"{'stance':<14} {'spread':>7} {'z':>4} {'roll':>5} {'pitch':>6}  "
          f"{'min reach':>10} {'max reach':>10}  verdict")
    print("-" * 84)
    failures = 0
    for stance in STANCES.values():
        ok, reaches, reason = validate(stance)
        if not ok:
            failures += 1
        span = f"{min(reaches):10.1f} {max(reaches):10.1f}" if reaches else f"{'-':>10} {'-':>10}"
        print(f"{stance.name:<14} {stance.spread:>7.2f} {stance.z:>4} {stance.roll:>5} "
              f"{stance.pitch:>6}  {span}  {'OK' if ok else 'REJECT: ' + reason}")
    print("-" * 84)
    print(f"reach window {MIN_REACH_MM:.0f}..{MAX_REACH_MM:.0f}mm, "
          f"usable {MIN_REACH_MM + REACH_MARGIN_MM:.0f}..{MAX_REACH_MM - REACH_MARGIN_MM:.0f}mm "
          f"with a {REACH_MARGIN_MM:.0f}mm margin")
    print(f"{len(STANCES) - failures}/{len(STANCES)} stances reachable")

    print()
    print("walking in each stance (tripod gait, speed 6, simulated frame by frame):")
    print(f"{'stance':<14} {'fwd y=35':>22} {'turn angle=8':>22}")
    print("-" * 84)
    for stance in STANCES.values():
        cells = []
        reasons = []
        for x, y, angle in ((0, 35, 0), (0, 0, 8)):
            ok, lo, hi, reason = validate_for_gait(stance, x, y, angle)
            cells.append(f"{lo:6.1f}..{hi:6.1f} {'OK' if ok else 'REJECT'}")
            if not ok:
                reasons.append(reason)
        print(f"{stance.name:<14} {cells[0]:>22} {cells[1]:>22}")
        for reason in reasons:
            print(f"{'':<14}   {reason}")
