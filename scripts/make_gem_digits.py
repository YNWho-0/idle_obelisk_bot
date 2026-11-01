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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from obelisk.gems import glyphs  # noqa: E402

OUT = ROOT / "obelisk" / "anchors" / "gem_digits"
KNOWN = {
    "01_wm720x1280.png": "3852", "10_store_freebie_ready.png": "3852",
    "13_store_closed.png": "3882", "20_giftbug_1.png": "3882",
    "21_bug_popup_3.png": "3884", "40_mining_after_settings.png": "3884",
    "24_after_redbug.png": "3974", "25_after_notification_press.png": "3974",
    "50_founder_1.png": "3994",
    "51_founder_press_3.png": "4014", "26_redbug_banked.png": "4014",
    "60_stats.png": "4057", "63_stickers.png": "4057",
    "65_gems_4110.png": "4110",   # thin-stroke rendering of 1 and 0
    "66_gems_6294.png": "6294",   # first 6
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    counts: dict[str, int] = {}
    for name, value in KNOWN.items():
        gl = glyphs(cv2.imread(str(ROOT / "screens" / name)))
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
