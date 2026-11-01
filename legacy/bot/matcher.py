"""
Template matching helpers built on OpenCV.

Single-scale matching is enough here because Waydroid renders the game at
a fixed resolution -- templates never need runtime scaling, which keeps
this cheap enough to run every couple of seconds on a Pi 5.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

import cv2
import numpy as np

logger = logging.getLogger("idle_obelisk_bot.matcher")


@dataclass
class MatchResult:
    found: bool
    confidence: float
    center: Optional[Tuple[int, int]] = None
    top_left: Optional[Tuple[int, int]] = None
    size: Optional[Tuple[int, int]] = None


def load_templates(template_dir: str, grayscale: bool = True) -> Dict[str, np.ndarray]:
    templates: Dict[str, np.ndarray] = {}
    if not os.path.isdir(template_dir):
        logger.warning("template dir %s does not exist yet", template_dir)
        return templates
    for fname in sorted(os.listdir(template_dir)):
        if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        path = os.path.join(template_dir, fname)
        flag = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
        img = cv2.imread(path, flag)
        if img is None:
            logger.warning("failed to load template %s", path)
            continue
        templates[fname] = img
    logger.info("loaded %d templates from %s", len(templates), template_dir)
    return templates


def _prep(img: np.ndarray, grayscale: bool) -> np.ndarray:
    if grayscale and img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def find_template(
    screen: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.85,
    grayscale: bool = True,
    region: Optional[Tuple[int, int, int, int]] = None,  # x, y, w, h
) -> MatchResult:
    search_img = screen
    offset_x, offset_y = 0, 0
    if region:
        x, y, w, h = region
        search_img = screen[y : y + h, x : x + w]
        offset_x, offset_y = x, y

    search_img = _prep(search_img, grayscale)
    tmpl = _prep(template, grayscale)

    if search_img.shape[0] < tmpl.shape[0] or search_img.shape[1] < tmpl.shape[1]:
        return MatchResult(found=False, confidence=0.0)

    result = cv2.matchTemplate(search_img, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        return MatchResult(found=False, confidence=float(max_val))

    h, w = tmpl.shape[:2]
    top_left = (max_loc[0] + offset_x, max_loc[1] + offset_y)
    center = (top_left[0] + w // 2, top_left[1] + h // 2)
    return MatchResult(found=True, confidence=float(max_val), center=center, top_left=top_left, size=(w, h))
