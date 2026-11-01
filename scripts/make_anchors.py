#!/usr/bin/env python3
"""Cut anchor templates out of the saved reference screenshots.

Every anchor is (name, source screenshot, box). Re-run after recapturing a
reference screen:  venv/bin/python scripts/make_anchors.py
Boxes are x1, y1, x2, y2 in the fixed 720x1280 layout.
"""

from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "screens"
OUT = ROOT / "obelisk" / "anchors"

ANCHORS = [
    # bottom bar (present on mining screen and in the Store)
    ("anvil_button",   "13_store_closed.png",        (44, 1100, 94, 1148)),
    ("grid_button",    "13_store_closed.png",        (334, 1100, 386, 1148)),
    # Store icon awning only: the icon background fills with green as freebies bank up
    ("store_icon",     "13_store_closed.png",        (615, 1113, 644, 1122)),
    # Store screen
    ("store_close",    "10_store_freebie_ready.png", (403, 1100, 453, 1147)),
    ("loot_tables",    "10_store_freebie_ready.png", (520, 172, 620, 203)),
    ("freebie_header", "10_store_freebie_ready.png", (170, 460, 308, 486)),
    ("claim_ready",    "10_store_freebie_ready.png", (521, 495, 602, 548)),
    ("claim_empty",    "12_after_claim_settled.png", (521, 495, 602, 548)),
    # Left HUD column: "banked lootbugs" row = [bug icon] N [bug icon "All"]
    ("lootbug_icon",   "01_wm720x1280.png",            (11, 705, 28, 718)),
    ("lootbug_all",    "01_wm720x1280.png",            (60, 705, 79, 718)),
    # Lootbug popup (a GEM SHOP below the header; only ever press its top-right X)
    ("lootbug_header", "23_redbug_press.png",          (212, 202, 420, 232)),
    ("lootbug_close",  "23_redbug_press.png",          (542, 222, 586, 266)),
    # Lootbug sprites on the field (head + body, no flapping wings)
    ("lootbug_red_1",    "26_redbug_banked.png",           (226, 612, 258, 682)),
    ("lootbug_red_2",    "25_after_notification_press.png", (226, 612, 258, 682)),
    ("lootbug_red_3",    "50_founder_1.png",               (226, 612, 258, 682)),
    ("lootbug_purple_1", "20_giftbug_3.png",               (406, 615, 438, 682)),
    ("lootbug_purple_2", "20_giftbug_3.png",               (316, 615, 348, 682)),
    # Founder drop: lower half of the parachute crate (ribbon + glowing gem)
    ("founder_crate_1", "50_founder_1.png",            (334, 532, 418, 578)),
    ("founder_crate_2", "50_founder_2.png",            (325, 532, 409, 578)),
    ("founder_crate_3", "50_founder_3.png",            (321, 538, 405, 584)),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, src, (x1, y1, x2, y2) in ANCHORS:
        img = cv2.imread(str(SCREENS / src))
        if img is None:
            raise SystemExit(f"missing reference screenshot {SCREENS / src}")
        cv2.imwrite(str(OUT / f"{name}.png"), img[y1:y2, x1:x2])
        print(f"{name:15s} {x2 - x1:3d}x{y2 - y1:<3d} from {src}")


if __name__ == "__main__":
    main()
