#!/usr/bin/env python3
"""
Interactive helper: grabs a live screenshot from the game and lets you
drag a box around a UI element (a button, an icon, a popup's close X)
to save it as a template PNG.

Usage:
    python3 scripts/capture_template.py --name collect_button

Requires a graphical session on the Pi (Raspberry Pi OS desktop) and the
full `opencv-python` package (not opencv-python-headless), since it opens
an OpenCV window for the crop selection. See requirements.txt.
"""
import argparse
import os
import sys

import cv2
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.adb_client import AdbClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="template filename, without extension")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--templates", default="templates")
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    adb = AdbClient(serial=config.get("adb", {}).get("serial"))
    screen = adb.screenshot()

    print("Drag a box tightly around the UI element, then press ENTER or SPACE.")
    print("Press 'c' to cancel without saving.")
    box = cv2.selectROI("Select template - ENTER to confirm", screen, showCrosshair=True)
    cv2.destroyAllWindows()

    x, y, w, h = box
    if w == 0 or h == 0:
        print("empty selection, nothing saved")
        return

    crop = screen[y : y + h, x : x + w]
    os.makedirs(args.templates, exist_ok=True)
    out_path = os.path.join(args.templates, f"{args.name}.png")
    cv2.imwrite(out_path, crop)
    print(f"saved {out_path}  (region was x={x} y={y} w={w} h={h})")
    print("Now add/point a task entry in config.yaml at this filename.")


if __name__ == "__main__":
    main()
