#!/usr/bin/env python3
"""Cut Cloud-screen play-time glyphs from screenshots with known values.

  venv/bin/python -I scripts/make_playtime_glyphs.py
"""

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from obelisk.playtime import CLOUD_ROW, LOCAL_ROW, SAMPLE_DIR, glyphs  # noqa: E402

# screenshot -> (cloud text, local text), spaces removed
KNOWN = {
    "80_cloud.png": ("05d15h17m30s", "05d16h00m56s"),
    "82_upload_1.png": ("05d15h17m30s", "05d16h02m25s"),
    "84_upload_3.png": ("05d15h17m30s", "05d16h02m35s"),
    "85_upload_confirm_1.png": ("05d15h17m30s", "05d16h03m04s"),
    "87_upload_confirm_3.png": ("05d16h03m04s", "05d16h03m19s"),
    "90_cloud_newer.png": ("05d16h05m58s", "05d16h06m24s"),
}


def main() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    for old in SAMPLE_DIR.glob("*.png"):
        old.unlink()
    counts: dict[str, int] = {}
    for name, texts in KNOWN.items():
        img = cv2.imread(str(ROOT / "screens" / name))
        for row, text in zip((CLOUD_ROW, LOCAL_ROW), texts):
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
