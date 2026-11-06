"""Read the gem counter (top bar) and guard jobs against losing gems.

Reading is deliberately strict: every glyph must closely match a stored digit
AND beat the second-best digit by a margin, and two captures must agree;
otherwise the value is None ("don't know") and the guard skips that check.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from .vision import ANCHOR_DIR

UNREAD_DIR = ANCHOR_DIR.parent.parent / "logs" / "gem_unread"
MAX_UNREAD_FILES = 50
STABLE_WAIT = 6.0     # s; game toasts dim the counter or pass next to it for a few seconds

log = logging.getLogger("obelisk.gems")

GEM_BOX = (430, 35, 536, 57)      # layout v2: digits end x~533, gem icon from x~540
GLYPH_H, PAD_W = 15, 10
# Glyph distance = Hausdorff (max distance from any ink pixel to the other
# glyph's nearest ink). Stroke width varies 1-2 px with sub-pixel position, so
# pixel XOR confused 0 with 8; Hausdorff doesn't (a missing bar costs >= 1.9).
# Distances come in steps of ~0.96: same digit <= 0.96, different >= 1.91.
MAX_DIST = 1.0
MIN_MARGIN = 0.9


def glyphs(frame: np.ndarray) -> list[np.ndarray]:
    """Boolean ink masks of the counter's glyphs, left to right."""
    x1, y1, x2, y2 = GEM_BOX
    # Otsu threshold: the counter is sometimes dimmed by an overlay (grey
    # digits); a fixed cut-off at 150 lost parts of them
    gray = cv2.cvtColor(np.ascontiguousarray(frame[y1:y2, x1:x2]), cv2.COLOR_BGR2GRAY)
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    white = gray > max(t, 90)
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
            _save_unread(frame)
            return None
        digits.append(ranked[0][0])
    return int("".join(digits))


def stable_gems(dev) -> Optional[int]:
    """Gem count once two captures in a row agree, or None after STABLE_WAIT."""
    deadline = time.monotonic() + STABLE_WAIT
    last = None
    while True:
        value = read_gems(dev.capture())
        if value is not None and value == last:
            return value
        last = value
        if time.monotonic() >= deadline:
            return None
        dev.sleep(0.5)


def _save_unread(frame: np.ndarray) -> None:
    """Keep the counter crop of failed reads (new digits/formats) for labelling."""
    try:
        UNREAD_DIR.mkdir(parents=True, exist_ok=True)
        if len(list(UNREAD_DIR.glob("*.png"))) >= MAX_UNREAD_FILES:
            return
        x1, y1, x2, y2 = GEM_BOX
        crop = frame[y1:y2, x1:x2]
        name = UNREAD_DIR / f"{abs(hash(crop.tobytes())) % 10**10}.png"
        if not name.exists():
            cv2.imwrite(str(name), crop)
    except OSError as e:
        log.debug("could not save unread gem crop: %s", e)
