"""Read the gem counter (top bar) and guard jobs against losing gems.

Reading is deliberately strict: every glyph must closely match a stored digit
AND beat the second-best digit by a margin, and two captures must agree;
otherwise the value is None ("don't know") and the guard skips that check.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from .vision import ANCHOR_DIR

log = logging.getLogger("obelisk.gems")

GEM_BOX = (466, 126, 568, 148)    # x1, y1, x2, y2: digits end at x~558, gem icon after
GLYPH_H, PAD_W = 13, 9
# Glyph distance = Hausdorff (max distance from any ink pixel to the other
# glyph's nearest ink). Stroke width varies 1-2 px with sub-pixel position, so
# pixel XOR confused 0 with 8; Hausdorff doesn't (a missing bar costs >= 1.9).
# Distances come in steps of ~0.96: same digit <= 0.96, different >= 1.91.
MAX_DIST = 1.0
MIN_MARGIN = 0.9


def glyphs(frame: np.ndarray) -> list[np.ndarray]:
    """Boolean ink masks of the counter's glyphs, left to right."""
    x1, y1, x2, y2 = GEM_BOX
    white = frame[y1:y2, x1:x2].min(axis=2) > 150
    cols = white.any(axis=0)
    out, start = [], None
    for x, c in enumerate(list(cols) + [False]):
        if c and start is None:
            start = x
        elif not c and start is not None:
            sub = white[:, start:x]
            ys = np.nonzero(sub.any(axis=1))[0]
            if ys.max() - ys.min() >= 9:          # skip the gem icon's highlight
                out.append(sub[ys.min():ys.max() + 1])
            start = None
    return out


def _canvas(g: np.ndarray) -> np.ndarray:
    out = np.zeros((GLYPH_H, PAD_W), np.uint8)
    h, w = min(g.shape[0], GLYPH_H), min(g.shape[1], PAD_W)
    off = (PAD_W - w) // 2
    out[:h, off:off + w] = g[:h, :w]
    return out


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    """Symmetric Hausdorff distance, best over -1/0/+1 px horizontal shifts."""
    A = _canvas(a)
    dA = cv2.distanceTransform(1 - A, cv2.DIST_L2, 3)
    best = 99.0
    for s in (-1, 0, 1):
        B = np.roll(_canvas(b), s, axis=1)
        if not A.any() or not B.any():
            continue
        dB = cv2.distanceTransform(1 - B, cv2.DIST_L2, 3)
        best = min(best, float(max(dB[A > 0].max(), dA[B > 0].max())))
    return best


@lru_cache(maxsize=None)
def _samples() -> tuple:
    out = []
    for p in sorted((ANCHOR_DIR / "gem_digits").glob("*.png")):
        out.append((p.stem.split("_")[0], cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) > 127))
    return tuple(out)


def read_gems(frame: np.ndarray) -> Optional[int]:
    gl = glyphs(frame)
    if not gl or len(gl) > 7:
        return None
    digits = []
    for g in gl:
        best: dict[str, float] = {}
        for ch, s in _samples():
            best[ch] = min(best.get(ch, 99.0), _dist(g, s))
        ranked = sorted(best.items(), key=lambda kv: kv[1])
        if len(ranked) < 2 or ranked[0][1] > MAX_DIST or ranked[1][1] - ranked[0][1] < MIN_MARGIN:
            return None
        digits.append(ranked[0][0])
    return int("".join(digits))


def stable_gems(dev, tries: int = 2) -> Optional[int]:
    """Gem count only if consecutive captures agree."""
    vals = []
    for i in range(tries):
        if i:
            dev.sleep(0.3)
        vals.append(read_gems(dev.capture()))
    return vals[0] if vals[0] is not None and all(v == vals[0] for v in vals) else None
