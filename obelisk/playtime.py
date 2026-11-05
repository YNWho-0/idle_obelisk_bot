"""Read the save play times ("05d 16h 05m 58s") and game versions ("v2.2.52")
on the Cloud screen.

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
CLOUD_VERSION_ROW = (162, 450, 256, 472)
LOCAL_VERSION_ROW = (162, 703, 256, 725)
CANVAS_W = 10
SAMPLE_DIR = ANCHOR_DIR / "playtime_glyphs"
RE_TIME = re.compile(r"^(\d+)d(\d+)h(\d+)m(\d+)s$")
RE_VERSION = re.compile(r"^\d+(\.\d+)+$")
UNREAD_DIR = ANCHOR_DIR.parent.parent / "logs" / "playtime_unread"


def glyphs(frame: np.ndarray, row, min_ink: int = 4) -> list[np.ndarray]:
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
            if sub.sum() >= min_ink:
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


def _match(g: np.ndarray) -> Optional[str]:
    """The sample character for a glyph, or None unless it's a clear match."""
    best: dict[str, float] = {}
    for ch, s in _samples():
        best[ch] = min(best.get(ch, 99.0), _dist(g, s))
    ranked = sorted(best.items(), key=lambda kv: kv[1])
    if len(ranked) < 2 or ranked[0][1] > MAX_DIST or ranked[1][1] - ranked[0][1] < MIN_MARGIN:
        return None
    return ranked[0][0]


def read_text(frame: np.ndarray, row) -> Optional[str]:
    chars = []
    for g in glyphs(frame, row):
        ch = _match(g)
        if ch is None:
            _save_unread(frame, row)
            return None
        chars.append(ch)
    return "".join(chars) or None


def read_version(frame: np.ndarray, row) -> Optional[tuple[int, ...]]:
    """A save's game version as (2, 2, 52), or None if it can't be read with
    confidence. The leading "v" has no sample and dots are too small to match,
    so the "v" is skipped and any glyph at most 3 px tall counts as a dot."""
    text = ""
    for g in glyphs(frame, row, min_ink=1)[1:]:
        ch = "." if g.any(axis=1).sum() <= 3 else _match(g)
        if ch is None:
            return None
        text += ch
    if not RE_VERSION.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


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
