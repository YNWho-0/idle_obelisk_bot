#!/usr/bin/env python3
"""Set (or restore) in-game Options checkboxes, verifying each change.

Start with the Options screen open (grid menu -> Options).

  venv/bin/python -I scripts/set_options.py            # apply bot settings
  venv/bin/python -I scripts/set_options.py --restore  # back to the originals
  venv/bin/python -I scripts/set_options.py --check    # report only, no presses

Each row is found by its label (template cut from a reference screenshot),
the checkbox state is read from pixels, and after a press exactly that one
checkbox must have flipped, or the script stops. Original values are recorded
in docs/game_settings.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from obelisk.device import Device  # noqa: E402

SCREENS = ROOT / "screens"
SHOTS = SCREENS / "settings"
TABS = {"gameplay": 315, "display": 403, "menus": 579}
LIST_TOP, LIST_BOTTOM = 240, 965
CHECK_X = 121

# (tab, label, reference screenshot, row y in it, original, bot value)
OPTIONS = [
    ("gameplay", "Remove Lootbug Gem confirmation", "33_opt_gameplay_0.png", 610, True, False),
    ("display", "Disable Ore Damage Popups", "32_options_display.png", 316, False, True),
    ("display", "Disable Bomb-Bag/Drone Pause tooltips", "32_options_display.png", 406, False, True),
    ("display", "Hide Void Portal Resource Gain Popups", "32_options_display.png", 452, False, True),
    ("display", "Hide Coal Full Popup", "32_options_display.png", 497, False, True),
    ("display", "Make Pets Invisible", "32_options_display.png", 927, False, True),
    ("display", "Minimize Mining Effects To Stop Flashing", "32_options_display_s2.png", 504, False, True),
    ("display", "Hide Golden Floor Effects", "32_options_display_s2.png", 685, False, True),
    ("display", "Hide Rainbow Floor Effects", "32_options_display_s2.png", 730, False, True),
    ("display", "Hide All Vein Effects", "32_options_display_s2.png", 775, False, True),
    ("display", "Disable Drone radius display", "32_options_display_s2.png", 911, False, True),
    ("menus", "Make Drones Invisible", "33_opt_menus_0.png", 361, False, True),
]


def checkboxes(img) -> list[tuple[int, bool]]:
    """[(y_center, checked)] for the checkbox column (orange border, x 105-135)."""
    col = img[:, 106].astype(int)
    orange = (np.abs(col[:, 2] - 240) < 30) & (col[:, 1] > 130) & (col[:, 0] < 90)
    rows, start = [], None
    for y in range(LIST_TOP, LIST_BOTTOM):
        if orange[y] and start is None:
            start = y
        elif not orange[y] and start is not None:
            if y - start >= 20:
                rows.append((start, y))
            start = None
    out = []
    for a, b in rows:
        inner = img[a + 6:b - 6, 112:131]
        out.append(((a + b) // 2, bool((inner.min(axis=2) > 200).mean() > 0.08)))
    return out


def label_template(src: str, y: int) -> np.ndarray:
    img = cv2.imread(str(SCREENS / src))
    return img[y - 11:y + 11, 170:600]


def find_label(frame, tpl) -> int | None:
    region = frame[LIST_TOP:LIST_BOTTOM, 150:620]
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, best, _, (_, by) = cv2.minMaxLoc(res)
    return LIST_TOP + by + tpl.shape[0] // 2 if best >= 0.9 else None


def state_at(frame, y) -> bool | None:
    for cy, checked in checkboxes(frame):
        if abs(cy - y) <= 8:
            return checked
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--restore", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()

    dev = Device()
    SHOTS.mkdir(parents=True, exist_ok=True)
    for i, (tab, label, src, ref_y, original, bot) in enumerate(OPTIONS):
        want = original if args.restore else bot
        dev.press(TABS[tab], 198)
        time.sleep(1.2)
        for _ in range(4):                       # tabs keep their scroll; go to top
            dev.swipe(660, 400, 660, 900, 300)
        time.sleep(1.0)
        tpl = label_template(src, ref_y)
        frame, y = dev.capture(), None
        for _ in range(5):
            y = find_label(frame, tpl)
            if y is not None:
                break
            dev.swipe(660, 900, 660, 400, 600)   # scroll right of the rows
            time.sleep(1.0)
            frame = dev.capture()
        if y is None:
            sys.exit(f"STOP: could not find '{label}'")
        now = state_at(frame, y)
        if now is None:
            sys.exit(f"STOP: no checkbox next to '{label}' at y={y}")
        if args.check or now == want:
            print(f"{'ok  ' if now == want else 'DIFF'} {label}: {'ON' if now else 'off'}")
            continue
        before = checkboxes(frame)
        cv2.imwrite(str(SHOTS / f"{i:02d}_before.png"), frame)
        dev.press(CHECK_X, y)
        time.sleep(1.0)
        after_frame = dev.capture()
        cv2.imwrite(str(SHOTS / f"{i:02d}_after.png"), after_frame)
        after = checkboxes(after_frame)
        expected = [(cy, (not c) if abs(cy - y) <= 8 else c) for cy, c in before]
        if after != expected:
            sys.exit(f"STOP after '{label}': checkbox states not as expected\n"
                     f"  before   {before}\n  expected {expected}\n  after    {after}")
        print(f"set  {label}: {'ON' if now else 'off'} -> {'ON' if want else 'off'}")


if __name__ == "__main__":
    main()
