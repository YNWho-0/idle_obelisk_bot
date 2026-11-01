"""
Small helpers to make tap positions and timing less mechanically regular.

Nothing here changes *what* the bot does, only exactly where within a
matched button it taps and exactly how long it waits between actions --
both randomized within bounds you control via config.yaml.
"""

from __future__ import annotations

import random
import time
from typing import Tuple


def jittered_point(
    center: Tuple[int, int],
    bounds_top_left: Tuple[int, int],
    bounds_size: Tuple[int, int],
    max_jitter_px: int,
) -> Tuple[int, int]:
    """
    Returns a point near `center`, randomized by up to max_jitter_px in
    each axis, but clamped so it can never land outside the matched
    element's own bounding box (bounds_top_left/bounds_size) -- jitter
    should never risk missing the button entirely.
    """
    cx, cy = center
    _, _ = bounds_top_left  # bounds are already baked into center/size from matcher
    w, h = bounds_size

    max_dx = min(max_jitter_px, max(0, w // 2 - 1))
    max_dy = min(max_jitter_px, max(0, h // 2 - 1))

    dx = random.randint(-max_dx, max_dx) if max_dx > 0 else 0
    dy = random.randint(-max_dy, max_dy) if max_dy > 0 else 0
    return cx + dx, cy + dy


def jittered_sleep(base_seconds: float, fraction: float = 0.25) -> None:
    """Sleeps base_seconds +/- `fraction` (e.g. 0.25 = +/-25%) at random."""
    if fraction <= 0:
        time.sleep(base_seconds)
        return
    low = base_seconds * (1 - fraction)
    high = base_seconds * (1 + fraction)
    time.sleep(max(0.05, random.uniform(low, high)))


def maybe_micro_pause(chance: float, range_seconds) -> None:
    """With probability `chance`, sleeps a random extra beat before/after an action."""
    if chance > 0 and random.random() < chance:
        time.sleep(random.uniform(*range_seconds))
