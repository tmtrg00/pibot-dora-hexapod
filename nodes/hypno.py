"""The hypno wave: sit on the belly and ripple all six legs in the air.

The robot lowers itself until the chassis rests on the ground, lifts all six
now-unloaded legs, and waves them in a slow travelling ripple — each leg runs
the same motion offset by 60 degrees of phase from its neighbour, and because
`body_points` orders the legs clockwise around the body (front-right, middle-
right, rear-right, rear-left, middle-left, front-left), the ripple visibly
rotates around the robot. Each foot traces a small ellipse: vertical lift and
radial in-out a quarter-cycle apart, which reads as an undulating, tentacle-
like motion rather than a mechanical bob.

Standalone direct-drive choreography: no queued command can express per-leg
heights, so keyframes and wave frames go straight through
`Control.transform_coordinates()`, with every frame validated against the
90..248mm reach window offline before a servo moves. Belly-sitting is what
makes the wave safe: once the chassis carries the weight, the legs are
unloaded and nothing the wave does can tip the robot — the getting down and
standing back up are the only load-bearing transitions, and both are plain
symmetric all-six-feet ramps, the same shape as a deep crouch.

Coordinate frame (same as Control.body_points): x lateral, y forward, z is
the foot's height relative to the body — standing feet are at z = -30; a foot
at z = +20 sits above the hip plane, clearly airborne once the belly rests.
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

GROUND_Z = stances.Z_BASE_MM  # standing foot height in the body frame (-30)
FRAME_S = max(0.005, float(os.environ.get("PIBOT_HYPNO_FRAME_S", "0.02")))


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


# Getting down: all six feet rise toward the body plane together until the
# chassis takes the weight. BELLY_Z is deliberately past the expected contact
# point — extra travel just unloads the legs further, it cannot pull the body
# below the floor.
SETTLE_Z = -12.0   # intermediate crouch on the way down
BELLY_Z = 5.0      # feet above the standing plane: chassis carries the weight

# The wave. Feet hover at HOVER_Z and oscillate +/-AMP vertically while
# breathing RADIAL_AMP in and out along each leg's own direction, a quarter
# cycle apart, so each foot draws an ellipse. Lowest wave point stays above
# BELLY_Z so no foot strikes the ground mid-ripple.
HOVER_Z = 25.0
AMP_MM = max(4.0, min(20.0, float(os.environ.get("PIBOT_HYPNO_AMP_MM", "14"))))
RADIAL_AMP_MM = 12.0
PERIOD_S = max(0.8, min(6.0, float(os.environ.get("PIBOT_HYPNO_PERIOD_S", "2.0"))))
CYCLES = max(1, min(20, int(os.environ.get("PIBOT_HYPNO_CYCLES", "4"))))
PHASE_STEP = math.pi / 3.0  # 60deg per leg: one full wave wraps the body

SIT_S = 3.0      # neutral -> belly -> legs hovering, one continuous motion
STAND_S = 3.0    # feet down -> push up -> standing, one continuous motion
ENVELOPE_S = PERIOD_S  # the ripple fades in and out over one period


def _flat(z: float) -> Points:
    return [[x, y, z] for x, y in stances.BASE_FOOTPRINT]


def hover_points() -> Points:
    return _flat(HOVER_Z)


def wave_frame(t_s: float, envelope: float = 1.0) -> Points:
    """The ripple at time t: per-leg phase-offset ellipses around hover.

    `envelope` scales both amplitudes; at 0 the frame IS the hover pose, so
    fading the envelope in and out attaches the wave seamlessly to the
    transitions on either side — no separate entry/exit keyframes."""
    pts = []
    omega = 2.0 * math.pi / PERIOD_S
    for i, (x, y) in enumerate(stances.BASE_FOOTPRINT):
        theta = omega * t_s - i * PHASE_STEP
        r = math.hypot(x, y)
        scale = 1.0 + envelope * (RADIAL_AMP_MM / r) * math.cos(theta)
        pts.append([x * scale, y * scale,
                    HOVER_Z + envelope * AMP_MM * math.sin(theta)])
    return pts


def wave_envelope(t_s: float, total_s: float) -> float:
    """Fade the ripple in over the first ENVELOPE_S and out over the last."""
    rise = _smoothstep(min(1.0, t_s / ENVELOPE_S))
    fall = _smoothstep(min(1.0, max(0.0, (total_s - t_s) / ENVELOPE_S)))
    return rise * fall


def build_chains() -> Tuple[List[Tuple[str, Points, float]],
                            List[Tuple[str, Points, float]]]:
    """(way down, way back up) waypoint chains. Each chain plays as ONE
    continuous eased motion passing THROUGH its interior waypoints without
    stopping — the per-waypoint float is that leg's share of the chain's
    duration, not a segment with its own stop."""
    down = [
        ("settle", _flat(SETTLE_Z), 1.1),
        ("belly down", _flat(BELLY_Z), 1.1),
        ("legs up", hover_points(), 0.8),
    ]
    up = [
        ("feet down", _flat(BELLY_Z), 0.8),
        ("push up", _flat(SETTLE_Z), 1.1),
        ("stand neutral", _flat(GROUND_Z), 1.1),
    ]
    return down, up


def chain_frames(start: Points, chain: List[Tuple[str, Points, float]],
                 total_s: float) -> List[Points]:
    """Frames of one continuous motion through the chain's waypoints.

    A single smoothstep eases the WHOLE path — accelerate once at the start,
    settle once at the end — while the interior waypoints are passed at speed.
    Easing each segment separately (as fight.py does between its distinct
    gestures) would bring every joint to a halt at every waypoint, which is
    exactly the stop-start the owner read as non-fluid (2026-08-20)."""
    weights = [w for _, _, w in chain]
    span = sum(weights)
    bounds = []
    acc = 0.0
    for w in weights:
        bounds.append((acc / span, (acc + w) / span))
        acc += w
    waypoints = [start] + [pts for _, pts, _ in chain]
    frames = []
    n = max(2, int(round(total_s / FRAME_S)))
    for f in range(1, n + 1):
        s = _smoothstep(f / n)  # eased progress along the whole path
        for seg, (lo, hi) in enumerate(bounds):
            if s <= hi or seg == len(bounds) - 1:
                local = 0.0 if hi == lo else (s - lo) / (hi - lo)
                local = min(1.0, max(0.0, local))
                frames.append(_interp(waypoints[seg], waypoints[seg + 1], local))
                break
    return frames


def validate_routine(margin: float = stances.REACH_MARGIN_MM) -> Tuple[bool, str]:
    """Every transition frame and one full wave period, through the reach
    window offline. One period suffices: the wave is periodic, so cycle N
    revisits exactly the frames of cycle 1."""
    low = stances.MIN_REACH_MM + margin
    high = stances.MAX_REACH_MM - margin
    lo_seen, hi_seen = float("inf"), 0.0

    def check(frame: Points, where: str) -> Optional[str]:
        nonlocal lo_seen, hi_seen
        reaches = points_reach(frame)
        lo_seen = min(lo_seen, min(reaches))
        hi_seen = max(hi_seen, max(reaches))
        if max(reaches) > high or min(reaches) < low:
            return (
                f"{where}: leg reach {min(reaches):.1f}..{max(reaches):.1f}mm "
                f"outside the usable {low:.0f}..{high:.0f}mm window"
            )
        return None

    down, up = build_chains()
    for name, start, chain, total in (
        ("descent", _flat(GROUND_Z), down, SIT_S),
        ("ascent", hover_points(), up, STAND_S),
    ):
        for f, frame in enumerate(chain_frames(start, chain, total)):
            bad = check(frame, f"{name} frame {f}")
            if bad:
                return False, bad

    # One full-envelope period covers the worst case: the fade in/out only
    # shrinks the same periodic frames toward the (validated) hover pose.
    steps = max(2, int(round(PERIOD_S / FRAME_S)))
    for f in range(steps):
        bad = check(wave_frame(f * FRAME_S), f"wave t={f * FRAME_S:.2f}s")
        if bad:
            return False, bad

    clearance = HOVER_Z - AMP_MM - BELLY_Z
    if clearance < 2.0:
        return False, (
            f"wave low point {HOVER_Z - AMP_MM:.0f} leaves only {clearance:.0f}mm "
            f"above the belly-contact plane {BELLY_Z:.0f} — feet would strike the ground"
        )
    return True, (
        f"all frames reachable ({lo_seen:.1f}..{hi_seen:.1f}mm), "
        f"wave clears the ground by {clearance:.0f}mm"
    )


def perform(control, cycles: int = CYCLES,
            log: Optional[Callable[[str], None]] = None) -> str:
    """Play the routine on a live Control. The caller guarantees the robot is
    standing in the neutral stance with the gait queue idle.

    Drives transform_coordinates()/set_leg_angles() directly from this thread,
    which is safe while the command queue stays empty — condition_monitor's
    only idle-time action is the 10s auto-relax, held off by refreshing
    control.timeout at every stage. body_points is never touched, so the
    walking gait is unaffected and the final keyframe leaves leg_positions
    exactly consistent with the neutral stance."""
    ok, reason = validate_routine()
    if not ok:
        return f"hypno_wave refused before moving: {reason}"

    down, up = build_chains()
    started = time.time()

    def play_chain(label, start, chain, total):
        if log:
            log(f"hypno: {label}")
        for frame in chain_frames(start, chain, total):
            control.timeout = time.time()
            control.transform_coordinates(frame)
            control.set_leg_angles()
            time.sleep(FRAME_S)

    play_chain("settling onto the belly", _flat(GROUND_Z), down, SIT_S)

    if log:
        log(f"hypno: waving — {cycles} cycles of {PERIOD_S:.1f}s, faded in and out")
    # The envelope starts and ends at zero, where the wave IS the hover pose,
    # so the ripple grows out of the descent and dissolves into the ascent
    # with no seam.
    wave_until = cycles * PERIOD_S
    t = 0.0
    while t < wave_until:
        control.timeout = time.time()
        control.transform_coordinates(wave_frame(t, wave_envelope(t, wave_until)))
        control.set_leg_angles()
        time.sleep(FRAME_S)
        t += FRAME_S

    play_chain("standing back up", hover_points(), up, STAND_S)

    control.timeout = time.time()
    elapsed = time.time() - started
    return (
        f"Hypno wave complete: settled onto the belly, rippled all six legs "
        f"for {cycles} cycles of {PERIOD_S:.1f}s, stood back up. {elapsed:.1f}s."
    )


if __name__ == "__main__":
    print("hypno wave waypoints:")
    print(f"{'waypoint':<16} {'min reach':>10} {'max reach':>10}")
    print("-" * 44)
    down, up = build_chains()
    for name, pts, _ in down + up:
        r = points_reach(pts)
        print(f"{name:<16} {min(r):>10.1f} {max(r):>10.1f}")
    print("-" * 44)
    ok, reason = validate_routine()
    total = SIT_S + STAND_S + CYCLES * PERIOD_S
    print(f"full routine ({CYCLES} cycles of {PERIOD_S:.1f}s, ~{total:.1f}s): "
          f"{'OK' if ok else 'REJECT'} — {reason}")
