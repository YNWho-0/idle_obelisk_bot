#!/usr/bin/env python3
"""Build masked lootbug templates for all 14 skins from the wiki screenshot.

Source: reference/wiki/lootbug_skins_screenshot.png (a phone screenshot of
the Idle Obelisk Miner wiki's "Regular / Golden Lootbugs" section, 1170x2532).
Each sprite is cut out of the dark wiki background (that becomes the alpha
mask), cropped to its central columns (wings flap on the sides), and scaled
to in-game size. Scale 0.37 was measured by matching the wiki Default and
Halloween sprites against real bugs in screens/26_* and screens/20_*.

  venv/bin/python -I scripts/make_wiki_lootbugs.py
"""

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "reference" / "wiki" / "lootbug_skins_screenshot.png"
OUT = ROOT / "obelisk" / "anchors" / "lootbug_wiki"

SCALE = 0.37
BODY_FRACTION = 0.42           # keep the central 42% of columns
BG = np.array([33, 32, 33])    # wiki page background (BGR)
NAMES = ["default", "valentines", "easter", "magma", "summer", "halloween", "christmas"]
# (x, y, w, h) per sprite in SRC, found as connected components on the background
BOXES = {
    "regular": [(97, 652, 221, 242), (326, 652, 242, 242), (577, 652, 215, 242),
                (801, 652, 269, 242), (296, 1016, 119, 242), (459, 1016, 221, 242),
                (696, 1016, 206, 242)],
    "golden": [(97, 1521, 221, 242), (326, 1521, 242, 242), (577, 1521, 215, 242),
               (801, 1521, 269, 242), (272, 1885, 167, 242), (459, 1885, 221, 242),
               (696, 1885, 206, 242)],
}


def build(img, box):
    x, y, w, h = box
    crop = img[y:y + h, x:x + w]
    mask = (np.abs(crop.astype(int) - BG).sum(axis=2) > 40).astype(np.uint8)
    ys, xs = np.nonzero(mask)
    crop = crop[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    cw = crop.shape[1]
    a, b = int(cw * (0.5 - BODY_FRACTION / 2)), int(cw * (0.5 + BODY_FRACTION / 2))
    crop, mask = crop[:, a:b], mask[:, a:b]
    crop = cv2.resize(crop, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    mask = cv2.resize(mask * 255, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    alpha = np.where(mask > 127, 255, 0).astype(np.uint8)
    return np.dstack([crop, alpha])


def main() -> None:
    img = cv2.imread(str(SRC))
    if img is None:
        raise SystemExit(f"missing {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, boxes in BOXES.items():
        for name, box in zip(NAMES, boxes):
            tpl = build(img, box)
            cv2.imwrite(str(OUT / f"{kind}_{name}.png"), tpl)
            print(f"{kind}_{name:11s} {tpl.shape[1]}x{tpl.shape[0]}")


if __name__ == "__main__":
    main()
