"""Read the save play times on the Cloud screen ("05d 16h 05m 58s").

Same approach as the gem reader (obelisk/gems.py): Otsu-thresholded glyphs,
nearest stored sample by Hausdorff distance, strict acceptance. Glyphs keep
their vertical position in the row (m/s are shorter than digits/d/h).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from .gems import MAX_DIST, MIN_MARGIN
from .vision import ANCHOR_DIR

CLOUD_ROW = (162, 384, 356, 406)    # x1, y1, x2, y2 (layout v2)
LOCAL_ROW = (162, 637, 356, 659)
CANVAS_W = 10
SAMPLE_DIR = ANCHOR_DIR / "playtime_glyphs"
RE_TIME = re.compile(r"^(\d+)d(\d+)h(\d+)m(\d+)s$")
UNREAD_DIR = ANCHOR_DIR.parent.parent / "logs" / "playtime_unread"


def glyphs(frame: np.ndarray, row) -> list[np.ndarray]:
    """Ink masks (full row height, own width), left to right."""
    x1, y1, x2, y2 = row
    gray = cv2.cvtColor(np.ascontiguousarray(frame[y1:y2, x1:x2]), cv2.COLOR_BGR2GRAY)
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = gray > max(t, 90)
    cols = ink.any(axis=0)
    out, start = [], None
    for x, c in enumerate(list(cols) + [False]):
        if c and start is None:
            start = x
        elif not c and start is not None:
            sub = ink[:, start:x]
            if sub.sum() >= 4:
                out.append(sub)
            start = None
    return out


def _canvas(g: np.ndarray) -> np.ndarray:
    out = np.zeros((g.shape[0], CANVAS_W), np.uint8)
    w = min(g.shape[1], CANVAS_W)
    off = (CANVAS_W - w) // 2
    out[:, off:off + w] = g[:, :w]
    return out


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    A = _canvas(a)
    dA = cv2.distanceTransform(1 - A, cv2.DIST_L2, 3)
    best = 99.0
    for s in (-1, 0, 1):
        B = np.roll(_canvas(b), s, axis=1)
        if not A.any() or not B.any() or A.shape != B.shape:
            continue
        dB = cv2.distanceTransform(1 - B, cv2.DIST_L2, 3)
        best = min(best, float(max(dB[A > 0].max(), dA[B > 0].max())))
    return best


@lru_cache(maxsize=None)
def _samples() -> tuple:
    return tuple((p.stem.split("_")[0], cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) > 127)
                 for p in sorted(SAMPLE_DIR.glob("*.png")))


def read_text(frame: np.ndarray, row) -> Optional[str]:
    chars = []
    for g in glyphs(frame, row):
        best: dict[str, float] = {}
        for ch, s in _samples():
            best[ch] = min(best.get(ch, 99.0), _dist(g, s))
        ranked = sorted(best.items(), key=lambda kv: kv[1])
        if len(ranked) < 2 or ranked[0][1] > MAX_DIST or ranked[1][1] - ranked[0][1] < MIN_MARGIN:
            _save_unread(frame, row)
            return None
        chars.append(ranked[0][0])
    return "".join(chars) or None


def read_seconds(frame: np.ndarray, row) -> Optional[int]:
    """Play time in seconds, or None if it can't be read with confidence."""
    text = read_text(frame, row)
    m = RE_TIME.match(text or "")
    if not m:
        return None
    d, h, mi, s = map(int, m.groups())
    if h > 23 or mi > 59 or s > 59:
        return None
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _save_unread(frame: np.ndarray, row) -> None:
    try:
        UNREAD_DIR.mkdir(parents=True, exist_ok=True)
        if len(list(UNREAD_DIR.glob("*.png"))) >= 50:
            return
        x1, y1, x2, y2 = row
        crop = frame[y1:y2, x1:x2]
        cv2.imwrite(str(UNREAD_DIR / f"{abs(hash(crop.tobytes())) % 10**10}.png"), crop)
    except OSError:
        pass
