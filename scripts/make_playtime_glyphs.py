#!/usr/bin/env python3
"""Cut Cloud-screen play-time glyphs from screenshots with known values.

  venv/bin/python -I scripts/make_playtime_glyphs.py
"""

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from obelisk.playtime import CLOUD_ROW, LOCAL_ROW, SAMPLE_DIR, glyphs  # noqa: E402

# screenshot -> (cloud text, local text), spaces removed
KNOWN = {   # layout v2
    "n51_cloud.png": ("05d16h20m45s", "05d16h24m07s"),
    "n54_uploaded.png": ("05d16h25m41s", "05d16h25m52s"),
    "n81_cloudtime_05d16h33m01s_crop.png": ("05d16h33m01s", None),   # CLOUD_ROW crop only
    "n92_cloudtime_05d17h06m49s_crop.png": ("05d17h06m49s", None),   # first 9
    "n104_time_05d20h28m31s_crop.png": ("05d20h28m31s", None),      # first 8 (row crop)
    "n104_time_05d20h18m36s_crop.png": ("05d20h18m36s", None),
}


def main() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    for old in SAMPLE_DIR.glob("*.png"):
        old.unlink()
    counts: dict[str, int] = {}
    for name, texts in KNOWN.items():
        img = cv2.imread(str(ROOT / "screens" / name))
        if name.endswith("_crop.png"):              # a CLOUD_ROW crop: put it in a blank frame
            frame = np.zeros((1280, 720, 3), np.uint8)
            x1, y1, x2, y2 = CLOUD_ROW
            frame[y1:y2, x1:x2] = img
            img = frame
        for row, text in zip((CLOUD_ROW, LOCAL_ROW), texts):
            if text is None:
                continue
            gl = glyphs(img, row)
            if len(gl) != len(text):
                raise SystemExit(f"{name}: {len(gl)} glyphs for {text!r}")
            for ch, g in zip(text, gl):
                n = counts.get(ch, 0)
                counts[ch] = n + 1
                cv2.imwrite(str(SAMPLE_DIR / f"{ch}_{n}.png"), g.astype("uint8") * 255)
    print("samples:", dict(sorted(counts.items())))


if __name__ == "__main__":
    main()
