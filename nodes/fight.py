"""The fight routine: rear back onto four legs and spar with the front pair.

A hexapod boxing gesture. The body shifts backward so its weight sits over the
middle and rear legs, the two front legs lift off the ground into a raised
guard, throw a few alternating jabs, and then everything returns to neutral.

Why this module exists — the upstream API cannot express it
-----------------------------------------------------------
`CMD_POSITION` moves all six feet by the same offset and `CMD_ATTITUDE` tilts
the body within +/-15deg; neither can hold four feet on the ground while two
others wave in the air. `Control.transform_coordinates()` can: it takes six
independent [x, y, z] foot points in the body frame. This module builds
keyframes of such points and plays them through eased interpolation, the same
direct-drive path `apply_stance` already uses for footprints.

Safety, in the same style as stances.py
---------------------------------------
Two things can go wrong and both are checked offline before a servo moves:

1. **Reach.** `set_leg_angles()` silently refuses any frame where a leg falls
   outside 90..248mm. `validate_routine()` walks every interpolated frame of
   the whole choreography through the same reach maths first, so an invalid
   pose is rejected at import/call time, not discovered as a mid-gesture
   freeze on the robot.
2. **Balance.** With the front feet in the air the support polygon is the
   quadrilateral of the four grounded feet. The whole routine happens with the
   body shifted BACK_SHIFT_MM backward, placing the centre of mass well behind
   the polygon's front edge (the line between the two middle feet), and while
   the front legs are up the body additionally pitches nose-up so its mass
   rotates further back over the rear legs. Without the shift the COM sits
   exactly ON that edge and the robot tips forward onto its face the moment
   the front legs lift — which is what the first version did on hardware.

Coordinate frame (same as Control.body_points): x lateral, y forward, z is the
foot's height relative to the body — standing feet are at z = -30, so a foot
at z = +25 is raised 55mm off the ground.
"""

from __future__ import annotations

import math
import os
import sys
import time
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stances

Points = List[List[float]]

FRONT_LEGS = (0, 5)          # front-right, front-left in body_points order
SUPPORT_LEGS = (1, 2, 3, 4)  # middle-right, rear-right, rear-left, middle-left

GROUND_Z = stances.Z_BASE_MM   # standing foot height in the body frame (-30)
BACK_SHIFT_MM = 60.0           # body moves back this far before the lift

# Before the front legs lift, the MIDDLE feet step forward to stand in for
# them (owner's suggestion, 2026-08-20): each middle foot takes a real lifted
# step, one leg at a time so five feet stay planted. That moves the support
# polygon's front edge from the body's midline to well AHEAD of it, which is
# what neither the 40mm nor the 60mm body shift managed on hardware — both
# left the centre of mass close enough to the front edge that the jab
# dynamics pitched the robot onto its head.
#
# The forward station is reached by SWINGING the leg about its hip, not by
# extending it: the first stepped version pushed the foot radially out to
# 210mm reach and the tibia visibly straightened out of its load-bearing
# upside-down-V (owner observation, 2026-08-20). Keeping the foot at the same
# 140mm horizontal hip distance as neutral standing preserves that V exactly —
# only the coxa angle changes — so the stepped-forward legs carry weight in
# the same geometry they always stand in.
MID_SWING_DEG = max(20.0, min(50.0, float(os.environ.get("PIBOT_FIGHT_MID_SWING_DEG", "45"))))
MID_HIP_OFFSET = 85.0  # middle-leg mount offset from the body centre (x)
MID_RADIAL = 140.0     # horizontal hip-to-foot distance when standing = the V
MID_STEP_LIFT = 35.0   # how high a stepping middle foot is picked up


def mid_station(sign: int) -> List[float]:
    """Where a middle foot stands after swinging MID_SWING_DEG forward about
    its hip. sign +1 = right leg, -1 = left."""
    a = math.radians(MID_SWING_DEG)
    x = MID_HIP_OFFSET + MID_RADIAL * math.cos(a)
    y = MID_RADIAL * math.sin(a)
    return [sign * x, y]

# While the front legs are airborne the body also pitches nose-up: the middle
# feet (now the forward supports) sit deeper below the body than the rear
# feet, so the front of the body rises and its mass rotates backward — the
# rearing look, on top of the stepped-forward support.
MID_SUPPORT_Z = -40.0   # forward supports deeper -> nose rises
REAR_SUPPORT_Z = -24.0  # rear supports shallower -> tail drops

# Front-leg air positions, body frame. The guard holds both feet FORWARD and
# PARALLEL — same y, mirrored small x — like a boxer's fists, not splayed
# along the legs' 54deg mounting angle. The jab snaps straight ahead.
GUARD_RIGHT = [60.0, 215.0, 30.0]
JAB_RIGHT = [65.0, 290.0, 45.0]
GUARD_LEFT = [-GUARD_RIGHT[0], GUARD_RIGHT[1], GUARD_RIGHT[2]]
JAB_LEFT = [-JAB_RIGHT[0], JAB_RIGHT[1], JAB_RIGHT[2]]

# Playback pacing. Jabs are deliberately quicker than the posture changes:
# the strike reads as a strike only if it is faster than the stance work
# around it. The recover is slower than the jab — punch out, ease back.
FRAME_S = max(0.005, float(os.environ.get("PIBOT_FIGHT_FRAME_S", "0.02")))
JAB_ROUNDS = max(1, min(6, int(os.environ.get("PIBOT_FIGHT_JABS", "2"))))

BRACE_S = 0.9      # neutral -> body shifted back, all feet down
STEP_LIFT_S = 0.3  # a stepping middle foot picks up and swings halfway
STEP_PLACE_S = 0.35  # ...and sets down at its forward station
LIFT_S = 0.7       # front feet rise into the guard, body pitches nose-up
JAB_S = 0.12       # strike out — a snap, not a reach
RECOVER_S = 0.22   # ease back to guard, still quick
HOLD_S = 0.5       # hold the guard before standing down
LOWER_S = 0.7      # front feet back to the ground, body level again
UNBRACE_S = 0.9    # body forward again to neutral


def neutral_points() -> Points:
    return [[x, y, GROUND_Z] for x, y in stances.BASE_FOOTPRINT]


def braced_points() -> Points:
    """All six feet down, shifted forward in the body frame = body leant back."""
    return [[x, y + BACK_SHIFT_MM, GROUND_Z] for x, y in stances.BASE_FOOTPRINT]


def mid_forward_points() -> Points:
    """Braced, with both middle feet at their forward station, body level."""
    pts = braced_points()
    pts[1][0], pts[1][1] = mid_station(+1)
    pts[4][0], pts[4][1] = mid_station(-1)
    return pts


def _airborne_base() -> Points:
    """Support pose while the front legs are up: body back, middle feet
    stepped forward as the new front supports, AND pitched nose-up — middle
    feet deep, rear feet shallow."""
    pts = mid_forward_points()
    for i in (1, 4):
        pts[i][2] = MID_SUPPORT_Z
    for i in (2, 3):
        pts[i][2] = REAR_SUPPORT_Z
    return pts


def _with_front(base: Points, right: List[float], left: List[float]) -> Points:
    pts = [list(p) for p in base]
    pts[0] = list(right)
    pts[5] = list(left)
    return pts


def guard_points() -> Points:
    return _with_front(_airborne_base(), GUARD_RIGHT, GUARD_LEFT)


def jab_points(side: str) -> Points:
    if side == "right":
        return _with_front(_airborne_base(), JAB_RIGHT, GUARD_LEFT)
    return _with_front(_airborne_base(), GUARD_RIGHT, JAB_LEFT)


def _mid_step(current: Points, leg: int, target_xy: List[float],
              label: str) -> List[Tuple[str, Points, float]]:
    """One middle foot takes a lifted two-beat step to target_xy: pick up and
    swing halfway, then set down at the station. Never dragged — a planted
    foot sliding under load skids or stalls."""
    x0, y0 = current[leg][0], current[leg][1]
    tx, ty = target_xy
    lifted = [list(p) for p in current]
    lifted[leg] = [(x0 + tx) / 2.0, (y0 + ty) / 2.0, GROUND_Z + MID_STEP_LIFT]
    placed = [list(p) for p in lifted]
    placed[leg] = [tx, ty, GROUND_Z]
    return [
        (f"{label} (lift)", lifted, STEP_LIFT_S),
        (f"{label} (place)", placed, STEP_PLACE_S),
    ]


def build_sequence(rounds: int = JAB_ROUNDS) -> List[Tuple[str, Points, float]]:
    """The whole choreography as (label, target keyframe, duration) steps."""
    seq: List[Tuple[str, Points, float]] = [
        ("brace back", braced_points(), BRACE_S),
    ]
    # Middle feet swing forward one at a time to become the front supports.
    seq += _mid_step(seq[-1][1], 1, mid_station(+1), "step mid-right fwd")
    seq += _mid_step(seq[-1][1], 4, mid_station(-1), "step mid-left fwd")
    seq.append(("guard up", guard_points(), LIFT_S))
    for i in range(rounds):
        for side in ("left", "right"):
            seq.append((f"jab {side}", jab_points(side), JAB_S))
            seq.append(("recover", guard_points(), RECOVER_S))
    seq.append(("guard hold", guard_points(), HOLD_S))
    seq.append(("feet down", mid_forward_points(), LOWER_S))
    # ...and step back home before the body comes forward again.
    braced = braced_points()
    seq += _mid_step(seq[-1][1], 4, braced[4][:2], "step mid-left back")
    seq += _mid_step(seq[-1][1], 1, braced[1][:2], "step mid-right back")
    seq.append(("stand neutral", neutral_points(), UNBRACE_S))
    return seq


def points_reach(points: Points) -> List[float]:
    """Per-leg reach for a full six-point pose (per-leg z, unlike
    stances.leg_reach whose z is uniform). Same transform arithmetic."""
    reaches = []
    for (x, y, z), (angle_deg, offset) in zip(points, stances.LEG_TRANSFORMS):
        a = math.radians(angle_deg)
        lx = x * math.cos(a) + y * math.sin(a) - offset
        ly = -x * math.sin(a) + y * math.cos(a)
        lz = z + stances.Z_LEG_OFFSET_MM
        reaches.append(math.sqrt(lx * lx + ly * ly + lz * lz))
    return reaches


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _interp(a: Points, b: Points, t: float) -> Points:
    return [
        [pa[i] + (pb[i] - pa[i]) * t for i in range(3)]
        for pa, pb in zip(a, b)
    ]


def _frames(duration_s: float) -> int:
    return max(2, int(round(duration_s / FRAME_S)))


def support_margin_mm(points: Points) -> float:
    """How far the centre of mass sits behind the support polygon's front edge
    while the front feet are airborne. The polygon front edge is the line
    between the two middle feet; the COM is taken at the body origin. Positive
    means stable; anywhere near zero means the robot tips onto its nose."""
    return min(points[1][1], points[4][1])


def validate_routine(rounds: int = JAB_ROUNDS,
                     margin: float = stances.REACH_MARGIN_MM) -> Tuple[bool, str]:
    """Walk every interpolated frame offline through the reach window.

    Interpolated frames matter, not just keyframes: a straight line between
    two valid poses can still swing a leg outside the window mid-flight.
    """
    low = stances.MIN_REACH_MM + margin
    high = stances.MAX_REACH_MM - margin
    current = neutral_points()
    lo_seen, hi_seen = float("inf"), 0.0
    for label, target, duration in build_sequence(rounds):
        n = _frames(duration)
        for f in range(1, n + 1):
            frame = _interp(current, target, _smoothstep(f / n))
            reaches = points_reach(frame)
            lo_seen = min(lo_seen, min(reaches))
            hi_seen = max(hi_seen, max(reaches))
            if max(reaches) > high or min(reaches) < low:
                return False, (
                    f"step {label!r} frame {f}/{n}: leg reach "
                    f"{min(reaches):.1f}..{max(reaches):.1f}mm outside the usable "
                    f"{low:.0f}..{high:.0f}mm window"
                )
        current = target
    stability = support_margin_mm(guard_points())
    if stability < 25.0:
        return False, (
            f"guard pose support margin {stability:.0f}mm is too thin — the COM "
            f"must sit well behind the middle feet before the front legs lift"
        )
    return True, (
        f"all frames reachable ({lo_seen:.1f}..{hi_seen:.1f}mm), "
        f"support margin {stability:.0f}mm"
    )


def perform(control, rounds: int = JAB_ROUNDS,
            log: Optional[Callable[[str], None]] = None) -> str:
    """Play the routine on a live Control. The caller guarantees the robot is
    standing in the neutral stance with the gait queue idle.

    Drives transform_coordinates()/set_leg_angles() directly from this thread,
    which is safe while the command queue stays empty — condition_monitor's
    only idle-time action is the 10s auto-relax, held off by refreshing
    control.timeout at every keyframe. body_points is never touched, so the
    walking gait is unaffected and the final keyframe leaves leg_positions
    exactly consistent with the neutral stance.
    """
    ok, reason = validate_routine(rounds)
    if not ok:
        return f"fight refused before moving: {reason}"

    sequence = build_sequence(rounds)
    current = neutral_points()
    started = time.time()
    for label, target, duration in sequence:
        control.timeout = time.time()  # hold off the idle auto-relax
        if log:
            log(f"fight: {label}")
        n = _frames(duration)
        for f in range(1, n + 1):
            frame = _interp(current, target, _smoothstep(f / n))
            control.transform_coordinates(frame)
            control.set_leg_angles()
            time.sleep(FRAME_S)
        current = target
    control.timeout = time.time()
    elapsed = time.time() - started
    return (
        f"Fight routine complete: leant back onto four legs, threw {rounds}x2 "
        f"jabs with the front pair, back to neutral. {len(sequence)} moves in "
        f"{elapsed:.1f}s."
    )


if __name__ == "__main__":
    print("fight routine keyframes:")
    print(f"{'keyframe':<16} {'min reach':>10} {'max reach':>10}")
    print("-" * 44)
    for name, pts in (
        ("neutral", neutral_points()),
        ("braced", braced_points()),
        ("mids forward", mid_forward_points()),
        ("guard", guard_points()),
        ("jab left", jab_points("left")),
        ("jab right", jab_points("right")),
    ):
        r = points_reach(pts)
        print(f"{name:<16} {min(r):>10.1f} {max(r):>10.1f}")
    print("-" * 44)
    ok, reason = validate_routine()
    total = sum(d for _, _, d in build_sequence())
    print(f"full routine ({JAB_ROUNDS} jab rounds, ~{total:.1f}s): "
          f"{'OK' if ok else 'REJECT'} — {reason}")
