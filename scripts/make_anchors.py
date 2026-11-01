#!/usr/bin/env python3
"""Cut anchor templates out of the reference screenshots (layout v2).

Layout v2 = the game's native 720x1280 layout (no black bars), which it uses
after any reload since 2026-11-01 ~13:15. The old letterboxed layout's
screenshots and anchors are in screens/v1layout/ and legacy/anchors_v1/.

Every anchor is (name, source screenshot, box x1, y1, x2, y2). Re-run after
recapturing a reference screen:  venv/bin/python -I scripts/make_anchors.py
"""

from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "screens"
OUT = ROOT / "obelisk" / "anchors"

ANCHORS = [
    # bottom bar (mining screen; also visible under the Store/grid menu)
    ("anvil_button",   "n10_mining_bugs_crate_1.png", (58, 1188, 106, 1234)),
    ("grid_button",    "n10_mining_bugs_crate_1.png", (338, 1188, 386, 1234)),
    # Store icon awning only: the icon background fills green as freebies bank up
    ("store_icon",     "n45_store_closed.png",        (598, 1195, 634, 1205)),
    # ...and its "freebie bank full" form: solid green with a gift chest
    ("store_icon_full", "n90_mining_store_full.png",  (594, 1192, 634, 1226)),
    # Store screen (its close X is also the grid menu's)
    ("store_close",    "n42_store_freebie_ready.png", (414, 1188, 466, 1234)),
    ("loot_tables",    "n42_store_freebie_ready.png", (553, 89, 663, 124)),
    ("freebie_header", "n42_store_freebie_ready.png", (137, 624, 297, 651)),
    ("claim_ready",    "n42_store_freebie_ready.png", (550, 665, 644, 726)),
    ("claim_empty",    "n44_freebie_claimed.png",     (550, 665, 644, 726)),
    # Lootbug popup (a GEM SHOP below the header; only ever press its top-right X)
    ("lootbug_header", "n21_lootbug_popup_settled.png", (250, 128, 420, 154)),  # words only: the row is centred, shifts with the count
    ("lootbug_close",  "n21_lootbug_popup_settled.png", (575, 148, 625, 197)),
    # Lootbug sprite on the field (head + body, no flapping wings)
    ("lootbug_red_1",  "n10_mining_bugs_crate_1.png", (377, 605, 409, 690)),
    # Offline Gains popups after the game returns from the background
    ("offline_options_label", "n73_resume5min_2.png",  (214, 704, 506, 763)),
    ("offline_confirm",       "n73_resume5min_2.png",  (74, 1168, 646, 1218)),
    ("offline_results_label", "n74_offline_results.png", (284, 10, 436, 31)),
    ("offline_close",         "n74_offline_results.png", (74, 1168, 646, 1218)),
    # Grid menu -> Cloud screen
    ("grid_cloud_button", "n50_grid_menu.png", (194, 927, 260, 993)),
    ("cloud_title",       "n51_cloud.png",     (178, 329, 450, 356)),
    # Founder drop: lower half of the parachute crate (3 sway poses)
    ("founder_crate_1", "n10_mining_bugs_crate_1.png", (432, 582, 530, 632)),
    ("founder_crate_2", "n30_founder_before.png",      (435, 590, 533, 640)),
    ("founder_crate_3", "n10_mining_bugs_crate_2.png", (448, 590, 546, 640)),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, src, (x1, y1, x2, y2) in ANCHORS:
        img = cv2.imread(str(SCREENS / src))
        if img is None:
            raise SystemExit(f"missing reference screenshot {SCREENS / src}")
        cv2.imwrite(str(OUT / f"{name}.png"), img[y1:y2, x1:x2])
        print(f"{name:22s} {x2 - x1:3d}x{y2 - y1:<3d} from {src}")


if __name__ == "__main__":
    main()
