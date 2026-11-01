#!/usr/bin/env python3
"""Cut gem-counter digit glyphs out of reference screenshots with known values.

  venv/bin/python -I scripts/make_gem_digits.py

Glyphs go to obelisk/anchors/gem_digits/<digit>_<n>.png (white = ink). The
game draws a digit 5-7 px wide depending on position, so several samples per
digit are kept and matched nearest-neighbour (see obelisk/gems.py).
Add a screenshot + its gem count to KNOWN when a new digit (e.g. 6) shows up.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from obelisk.gems import GEM_BOX, glyphs  # noqa: E402

OUT = ROOT / "obelisk" / "anchors" / "gem_digits"
KNOWN = {   # layout v2 screenshots with gem counts read by eye
    "n10_mining_bugs_crate_1.png": "632",
    "n10_mining_bugs_crate_3.png": "632",
    "n21_lootbug_popup_settled.png": "638",
    "n32_founder_after.png": "658",
    "n42_store_freebie_ready.png": "658",
    "n50_grid_menu.png": "768",
    "n60_resume_5.png": "768",
    "n80_gems_802_crop.png": "802",     # *_crop.png = GEM_BOX crop only
    "n83_gems_928_crop.png": "928",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    counts: dict[str, int] = {}
    for name, value in KNOWN.items():
        img = cv2.imread(str(ROOT / "screens" / name))
        if name.endswith("_crop.png"):              # a GEM_BOX crop: put it in a blank frame
            frame = np.zeros((1280, 720, 3), np.uint8)
            x1, y1, x2, y2 = GEM_BOX
            frame[y1:y2, x1:x2] = img
            img = frame
        gl = glyphs(img)
        if len(gl) != len(value):
            raise SystemExit(f"{name}: found {len(gl)} glyphs for {value!r}")
        for ch, g in zip(value, gl):
            n = counts.get(ch, 0)
            counts[ch] = n + 1
            cv2.imwrite(str(OUT / f"{ch}_{n}.png"), g.astype("uint8") * 255)
    print("samples per digit:", dict(sorted(counts.items())))
    missing = sorted(set("0123456789") - set(counts))
    if missing:
        print("no samples yet for:", " ".join(missing), "(readings containing them return None)")


if __name__ == "__main__":
    main()
