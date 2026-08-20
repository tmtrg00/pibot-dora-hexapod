"""The dance: feet planted, the body grooves — bounce, sway, circle, twist, bow.

Every foot stays on the ground for the entire routine; only the body moves
between them. That is the safety story: with six planted feet the support
polygon never changes and there is nothing to tip. The expressiveness comes
from driving all six foot points together in the body frame — shifting them
one way moves the body the other — plus per-leg z for lean and bow, which the
queued CMD_POSITION/CMD_ATTITUDE cannot do (uniform offsets, +/-15deg, and a
footprint reset — see stances.py on CMD_ATTITUDE).

This replaces the upstream `dance` tool behaviour (a ±10deg roll rock through
CMD_ATTITUDE): the hardware node serves `dance` from here, so the voice
command reaches this choreography under the unchanged upstream schema.

Standalone module by project convention: movement routines never import each
other; the small easing/interp/reach helpers are deliberate copies, and only
the geometry constants come from stances.py.

Structure: a "groove" base pose (slightly crouched), then a set list of MOVES,
each a parametric oscillation around that base. Every move starts and ends
exactly ON the base pose and its oscillation is faded in and out over its
first and last beats, so move follows move with no seam and no stop-start —
the same envelope idea that made the hypno wave fluid (2026-08-20). Chained
easing carries the body down into the groove and back up to standing.

Coordinate frame (same as Control.body_points): x lateral, y forward, z foot
height relative to the body; standing feet at z = -30, groove at z = -24
(body 6mm lower). Moving all feet -x moves the BODY +x, and rotating the feet
about the origin yaws the body the opposite way.
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
FRAME_S = max(0.005, float(os.environ.get("PIBOT_DANCE_FRAME_S", "0.02")))

GROOVE_Z = -24.0     # the dance happens slightly crouched
BOUNCE_MM = 8.0      # vertical bob amplitude
SWAY_MM = 22.0       # lateral body shift amplitude
LEAN_MM = 5.0        # extra per-leg z lean into the sway
CIRCLE_MM = 18.0     # body-circle radius
TWIST_DEG = 12.0     # body yaw amplitude
BOW_MM = 10.0        # how far the nose dips in the bow
ENV_S = 0.25         # per-move oscillation fade in/out

BEAT_S = max(0.3, min(1.5, float(os.environ.get("PIBOT_DANCE_BEAT_S", "0.75"))))
REPEATS = max(1, min(4, int(os.environ.get("PIBOT_DANCE_REPEATS", "1"))))

SINK_S = 1.5   # standing -> groove, one eased motion
RISE_S = 1.5   # groove -> standing


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _interp(a: Points, b: Points, t: float) -> Points:
    return [
        [pa[i] + (pb[i] - pa[i]) * t for i in range(3)]
        for pa, pb in zip(a, b)
    ]


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


def _flat(z: float) -> Points:
    return [[x, y, z] for x, y in stances.BASE_FOOTPRINT]


def groove_points() -> Points:
    return _flat(GROOVE_Z)


def _envelope(t: float, duration: float) -> float:
    rise = _smoothstep(min(1.0, t / ENV_S))
    fall = _smoothstep(min(1.0, max(0.0, (duration - t) / ENV_S)))
    return rise * fall


# ---------------------------------------------------------------------------
# The moves. Each is f(t, duration) -> Points, oscillating around the groove
# base, already envelope-faded so it starts and ends exactly on the base.
# ---------------------------------------------------------------------------

def _bounce(t: float, duration: float) -> Points:
    e = _envelope(t, duration)
    dz = BOUNCE_MM * e * math.sin(2.0 * math.pi * t / BEAT_S)
    return _flat(GROOVE_Z + dz)


def _sway(t: float, duration: float) -> Points:
    """Body shifts side to side and leans into it: the feet on the side the
    body moves toward sit deeper (higher body side), the far side shallower."""
    e = _envelope(t, duration)
    s = e * math.sin(2.0 * math.pi * t / (2.0 * BEAT_S))
    pts = []
    for x, y in stances.BASE_FOOTPRINT:
        lean = LEAN_MM * s * (x / 225.0)
        pts.append([x - SWAY_MM * s, y, GROOVE_Z - lean])
    return pts


def _circle(t: float, duration: float) -> Points:
    """The body grinds a horizontal circle over the planted feet."""
    e = _envelope(t, duration)
    a = 2.0 * math.pi * t / (2.0 * BEAT_S)
    dx = CIRCLE_MM * e * math.sin(a)
    dy = CIRCLE_MM * e * math.sin(a) * math.cos(a)  # lissajous-lite: figure-8 hint
    return [[x - dx, y - dy, GROOVE_Z] for x, y in stances.BASE_FOOTPRINT]


def _twist(t: float, duration: float) -> Points:
    """Feet rotate about the body centre, so the body yaws the other way."""
    e = _envelope(t, duration)
    theta = math.radians(TWIST_DEG) * e * math.sin(2.0 * math.pi * t / (2.0 * BEAT_S))
    c, s = math.cos(theta), math.sin(theta)
    return [[x * c + y * s, -x * s + y * c, GROOVE_Z] for x, y in stances.BASE_FOOTPRINT]


def _bow(t: float, duration: float) -> Points:
    """Nose dips and comes back: front feet shallower (nose drops), rear feet
    deeper (tail rises), one slow eased dip-and-hold."""
    e = _envelope(t, duration)
    pts = []
    for x, y in stances.BASE_FOOTPRINT:
        tilt = BOW_MM * e * (y / 189.4)  # +front legs shallower, -rear deeper
        pts.append([x, y, GROOVE_Z + tilt])
    return pts


def _pop(t: float, duration: float) -> Points:
    """Finale: one deep quick dip and spring back up."""
    e = _envelope(t, duration)
    dz = -12.0 * e * math.sin(math.pi * t / duration)
    return _flat(GROOVE_Z + dz)


def build_setlist(repeats: int = REPEATS) -> List[Tuple[str, Callable, float]]:
    """(label, move fn, duration) — the routine between sink and rise."""
    one_round = [
        ("bounce", _bounce, 4.0 * BEAT_S),
        ("sway", _sway, 4.0 * BEAT_S),
        ("circle", _circle, 4.0 * BEAT_S),
        ("twist", _twist, 4.0 * BEAT_S),
    ]
    setlist = one_round * repeats
    setlist.append(("bow", _bow, 3.0 * BEAT_S))
    setlist.append(("pop", _pop, 1.5 * BEAT_S))
    return setlist


def chain_frames(start: Points, end: Points, total_s: float) -> List[Points]:
    """One eased motion from start to end (sink into the groove / rise out)."""
    n = max(2, int(round(total_s / FRAME_S)))
    return [_interp(start, end, _smoothstep(f / n)) for f in range(1, n + 1)]


def validate_routine(repeats: int = REPEATS,
                     margin: float = stances.REACH_MARGIN_MM) -> Tuple[bool, str]:
    """Every frame of the whole dance through the reach window offline."""
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

    for f, frame in enumerate(chain_frames(_flat(GROUND_Z), groove_points(), SINK_S)):
        bad = check(frame, f"sink frame {f}")
        if bad:
            return False, bad
    for label, fn, duration in build_setlist(repeats):
        t = 0.0
        while t < duration:
            bad = check(fn(t, duration), f"move {label!r} t={t:.2f}s")
            if bad:
                return False, bad
            t += FRAME_S
    for f, frame in enumerate(chain_frames(groove_points(), _flat(GROUND_Z), RISE_S)):
        bad = check(frame, f"rise frame {f}")
        if bad:
            return False, bad
    return True, f"all frames reachable ({lo_seen:.1f}..{hi_seen:.1f}mm)"


def perform(control, repeats: int = REPEATS,
            log: Optional[Callable[[str], None]] = None) -> str:
    """Play the dance on a live Control. The caller guarantees the robot is
    standing in the neutral stance with the gait queue idle.

    Drives transform_coordinates()/set_leg_angles() directly from this thread,
    which is safe while the command queue stays empty — condition_monitor's
    only idle-time action is the 10s auto-relax, held off by refreshing
    control.timeout at every move. body_points is never touched, so the
    walking gait is unaffected and the final frame leaves leg_positions
    exactly consistent with the neutral stance."""
    ok, reason = validate_routine(repeats)
    if not ok:
        return f"dance refused before moving: {reason}"

    started = time.time()

    def play_frames(frames):
        for frame in frames:
            control.transform_coordinates(frame)
            control.set_leg_angles()
            time.sleep(FRAME_S)

    if log:
        log("dance: sinking into the groove")
    control.timeout = time.time()
    play_frames(chain_frames(_flat(GROUND_Z), groove_points(), SINK_S))

    setlist = build_setlist(repeats)
    for label, fn, duration in setlist:
        control.timeout = time.time()
        if log:
            log(f"dance: {label}")
        t = 0.0
        while t < duration:
            control.transform_coordinates(fn(t, duration))
            control.set_leg_angles()
            time.sleep(FRAME_S)
            t += FRAME_S

    if log:
        log("dance: standing back up")
    control.timeout = time.time()
    play_frames(chain_frames(groove_points(), _flat(GROUND_Z), RISE_S))

    control.timeout = time.time()
    elapsed = time.time() - started
    moves = ", ".join(label for label, _, _ in setlist)
    return (
        f"Dance complete: {moves} — feet planted throughout, "
        f"{elapsed:.1f}s at a {BEAT_S:.2f}s beat."
    )


if __name__ == "__main__":
    print("dance moves (oscillation extremes around the groove):")
    print(f"{'move':<10} {'min reach':>10} {'max reach':>10}")
    print("-" * 36)
    for label, fn, duration in build_setlist(1):
        lo, hi = float("inf"), 0.0
        t = 0.0
        while t < duration:
            r = points_reach(fn(t, duration))
            lo, hi = min(lo, min(r)), max(hi, max(r))
            t += FRAME_S
        print(f"{label:<10} {lo:>10.1f} {hi:>10.1f}")
    print("-" * 36)
    ok, reason = validate_routine()
    total = SINK_S + RISE_S + sum(d for _, _, d in build_setlist())
    print(f"full dance ({REPEATS} round(s), beat {BEAT_S:.2f}s, ~{total:.1f}s): "
          f"{'OK' if ok else 'REJECT'} — {reason}")
