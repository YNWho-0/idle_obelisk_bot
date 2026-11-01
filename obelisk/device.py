"""ADB access: raw screencap decoded with numpy, and held presses.

Findings this is built on (verified 2026-11-01):
  * `input tap` is ignored by the game; `input swipe X Y X Y 150` works.
  * `exec-out screencap` (no -p) returns a 16-byte header (w, h, format,
    colorspace) followed by w*h*4 RGBA bytes. Older Androids use a 12-byte
    header, so the header size is derived from the payload length.
"""

from __future__ import annotations

import logging
import subprocess
import time

import cv2
import numpy as np

from . import ACTIVITY, HEIGHT, PACKAGE, WIDTH

log = logging.getLogger("obelisk.device")

PRESS_MS = 150


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, serial: str = "192.168.240.112:5555", dry_run: bool = False):
        self.serial = serial
        self.dry_run = dry_run

    def _adb(self, *args: str, timeout: float = 10.0) -> bytes:
        cmd = ["adb", "-s", self.serial, *args]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise DeviceError(f"timeout: {' '.join(cmd)}") from e
        if res.returncode != 0:
            raise DeviceError(f"{' '.join(cmd)} -> {res.stderr.decode(errors='replace').strip()}")
        return res.stdout

    def capture(self) -> np.ndarray:
        """Current screen as a BGR uint8 array of shape (HEIGHT, WIDTH, 3)."""
        raw = self._adb("exec-out", "screencap")
        return decode_raw(raw)

    def press(self, x: int, y: int) -> None:
        """Held press. Callers should only pass coordinates of a matched anchor."""
        x, y = int(x), int(y)
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise DeviceError(f"press out of bounds: {x},{y}")
        if self.dry_run:
            log.info("DRY RUN press %d,%d", x, y)
            return
        log.info("press %d,%d", x, y)
        self._adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(PRESS_MS))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 600) -> None:
        """Drag, e.g. to scroll a list."""
        if self.dry_run:
            log.info("DRY RUN swipe %d,%d -> %d,%d", x1, y1, x2, y2)
            return
        self._adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))

    def check_resolution(self) -> None:
        img = self.capture()
        if img.shape[:2] != (HEIGHT, WIDTH):
            raise DeviceError(
                f"screen is {img.shape[1]}x{img.shape[0]}, expected {WIDTH}x{HEIGHT}; "
                f"run: adb -s {self.serial} shell wm size {WIDTH}x{HEIGHT}")

    def ensure_resolution(self) -> None:
        """Re-apply the 720x1280 override if it was lost (e.g. Waydroid restart)."""
        img = self.capture()
        if img.shape[:2] == (HEIGHT, WIDTH):
            return
        log.warning("screen is %dx%d; re-applying wm size %dx%d",
                    img.shape[1], img.shape[0], WIDTH, HEIGHT)
        if not self.dry_run:
            self._adb("shell", "wm", "size", f"{WIDTH}x{HEIGHT}")
        time.sleep(3)
        self.check_resolution()

    # ---- game lifecycle ----------------------------------------------------

    def game_in_front(self) -> bool:
        out = self._adb("shell", "dumpsys", "activity", "activities").decode(errors="replace")
        for line in out.splitlines():
            if "topResumedActivity" in line or "mResumedActivity" in line:
                return f"{PACKAGE}/" in line
        return False

    def go_home(self) -> None:
        """Send the game to the background (it then counts as offline)."""
        if self.dry_run:
            log.info("DRY RUN HOME")
            return
        log.info("HOME (game to background)")
        self._adb("shell", "input", "keyevent", "KEYCODE_HOME")

    def launch_game(self) -> None:
        if self.dry_run:
            log.info("DRY RUN launch %s", PACKAGE)
            return
        log.info("launching %s", PACKAGE)
        self._adb("shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}")

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


def decode_raw(raw: bytes) -> np.ndarray:
    if len(raw) < 12:
        raise DeviceError("screencap returned no data")
    w, h, fmt = np.frombuffer(raw[:12], dtype="<u4")
    if fmt != 1:  # 1 = RGBA_8888
        raise DeviceError(f"unsupported screencap pixel format {fmt}")
    header = len(raw) - int(w) * int(h) * 4
    if header not in (12, 16):
        raise DeviceError(f"unexpected screencap size {len(raw)} for {w}x{h}")
    rgba = np.frombuffer(raw, dtype=np.uint8, offset=header).reshape(int(h), int(w), 4)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
