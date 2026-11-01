"""Anchor matching: small template crops searched within a small window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

ANCHOR_DIR = Path(__file__).parent / "anchors"

Box = tuple[int, int, int, int]  # x1, y1, x2, y2 (exclusive)


@dataclass(frozen=True)
class Match:
    name: str
    box: Box
    score: float

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.box
        return (x1 + x2) // 2, (y1 + y2) // 2


class Anchor:
    """A template crop plus where to look for it.

    `search` is the window searched; by default the template's home box grown
    by `margin` pixels, so small UI shifts still match but nothing far away can.
    """

    def __init__(self, name: str, home: Box, threshold: float = 0.9,
                 margin: int = 6, search: Optional[Box] = None):
        self.name = name
        self.home = home
        self.threshold = threshold
        path = ANCHOR_DIR / f"{name}.png"
        self.template = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if self.template is None:
            raise FileNotFoundError(f"missing anchor {path}; run scripts/make_anchors.py")
        x1, y1, x2, y2 = home
        self.search = search or (x1 - margin, y1 - margin, x2 + margin, y2 + margin)

    def find(self, frame: np.ndarray, search: Optional[Box] = None) -> Optional[Match]:
        m = self.score(frame, search)
        return m if m and m.score >= self.threshold else None

    def score(self, frame: np.ndarray, search: Optional[Box] = None) -> Optional[Match]:
        """Best match in the search window regardless of threshold."""
        sx1, sy1, sx2, sy2 = clamp(search or self.search, frame)
        region = frame[sy1:sy2, sx1:sx2]
        th, tw = self.template.shape[:2]
        if region.shape[0] < th or region.shape[1] < tw:
            return None
        res = cv2.matchTemplate(region, self.template, cv2.TM_CCOEFF_NORMED)
        _, best, _, (bx, by) = cv2.minMaxLoc(res)
        return Match(self.name, (sx1 + bx, sy1 + by, sx1 + bx + tw, sy1 + by + th), float(best))


def clamp(box: Box, frame: np.ndarray) -> Box:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    return max(0, x1), max(0, y1), min(w, x2), min(h, y2)


def green_fraction(frame: np.ndarray, box: Box) -> float:
    """Share of pixels that are saturated bright green (the 'Claim' button colour,
    mean HSV ~ (71, 197, 164)). Red/gem buttons score ~0."""
    x1, y1, x2, y2 = clamp(box, frame)
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1], hsv[..., 2]
    mask = (np.abs(h - 68) <= 17) & (s >= 120) & (v >= 100)
    return float(mask.mean()) if mask.size else 0.0
