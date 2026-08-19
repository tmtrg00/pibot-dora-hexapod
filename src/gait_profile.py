# -*- coding: utf-8 -*-
"""Foot-height profile for the tripod gait.

Split out of `control.py` so it has exactly one definition and no hardware
dependencies. Two callers need it and neither should have to copy it:
`Control.run_gait` drives the real legs with it, and `nodes/stances.py` replays
it offline to check that a stance stays within leg reach for a whole gait
cycle. That module is deliberately importable without `lgpio` or the I2C
drivers, which importing `control` would have dragged in.

The profile is a pure function of the phase — how far through the cycle a frame
is — which is what makes sharing it safe. The horizontal half of the gait is
stateful and accumulates frame by frame; `stances.py` still mirrors that half
and carries a drift check for it.
"""

import math


def ease(u):
    """Smoothstep on 0..1: zero rate of change at both ends.

    A foot moved at a constant rate starts and stops instantly, which puts an
    impulsive acceleration into the leg at lift-off and touchdown. Easing
    spends the same time covering the same distance while starting and
    finishing at rest, which is the whole point: the foot is placed on the
    ground rather than driven into it.
    """
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    return (1.0 - math.cos(math.pi * u)) / 2.0


def swing_height_a(j, frames):
    """Height fraction (0..1) for the A tripod at frame `j` of `frames`.

    A is on the ground for the first quarter of the cycle, swings through the
    middle half, and is back down for the last quarter — the same schedule the
    original gait used, eased rather than linear.
    """
    if j < (frames / 4):
        return 0.0
    if j < (3 * frames / 8):
        return ease((j - frames / 4) / (frames / 8))
    if j < (5 * frames / 8):
        return 1.0
    if j < (3 * frames / 4):
        return ease(1.0 - (j - 5 * frames / 8) / (frames / 8))
    return 0.0


def swing_height_b(j, frames, first_cycle=False):
    """Height fraction (0..1) for the B tripod at frame `j` of `frames`.

    B's swing straddles the cycle boundary: it is already in the air when a
    cycle begins and is airborne again by the time it ends. Mid-walk that is
    seamless, because the previous cycle left it up there.

    `first_cycle` is the exception. Starting from a stand, every foot is on the
    ground, and the original gait commanded B straight to full lift height in a
    single 10ms frame — a 40mm jump, once per walk, felt as a lurch at the
    moment of setting off. On a first cycle B eases up over the opening phase
    instead.
    """
    if j < (frames / 8):
        return ease(j / (frames / 8)) if first_cycle else 1.0
    if j < (frames / 4):
        return ease(1.0 - (j - frames / 8) / (frames / 8))
    if j < (3 * frames / 4):
        return 0.0
    if j < (7 * frames / 8):
        return ease((j - 3 * frames / 4) / (frames / 8))
    return 1.0
