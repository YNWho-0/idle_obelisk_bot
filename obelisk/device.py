"""ADB access: raw screencap decoded with numpy, and held presses.

Findings this is built on (verified 2026-11-01):
  * `input tap` is ignored by the game; `input swipe X Y X Y 150` works.
  * `exec-out screencap` (no -p) returns a 16-byte header (w, h, format,
    colorspace) followed by w*h*4 RGBA bytes. Older Androids use a 12-byte
    header, so the header size is derived from the payload length.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

import cv2
import numpy as np

from . import ACTIVITY, ADB_SERIAL, HEIGHT, PACKAGE, WIDTH

log = logging.getLogger("obelisk.device")

PRESS_MS = 150


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, serial: str = ADB_SERIAL, dry_run: bool = False):
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

    def connect(self) -> None:
        """`adb connect` (starts the adb server too). Never raises: if Android
        isn't up yet, the first capture fails and systemd retries."""
        try:
            res = subprocess.run(["adb", "connect", self.serial], capture_output=True,
                                 text=True, timeout=15)
            log.info("adb connect %s: %s", self.serial, (res.stdout + res.stderr).strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("adb connect %s failed: %s", self.serial, e)

    def capture(self) -> np.ndarray:
        """Current screen as a BGR uint8 array of shape (HEIGHT, WIDTH, 3).

        If screencap hangs, nudge the host desktop and try once more: on
        2026-11-02 the Pi's monitor-less desktop stopped giving the restarted
        game a window, and screencap hung for 3 h until a VNC viewer connected
        (a host screenshot makes the compositor render, as VNC does)."""
        try:
            raw = self._adb("exec-out", "screencap")
        except DeviceError as e:
            if not str(e).startswith("timeout:"):
                raise
            log.warning("screencap timed out; nudging the host display and retrying")
            nudge_host_display()
            time.sleep(2)
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

    def restart_game(self) -> None:
        """Fresh start (the game only fetches cloud-save info at startup). Safe:
        tested 2026-11-01, the game saves locally when sent to the background."""
        if self.dry_run:
            log.info("DRY RUN restart %s", PACKAGE)
            return
        log.info("restarting %s", PACKAGE)
        self._adb("shell", "am", "force-stop", PACKAGE)
        time.sleep(2)
        self._adb("shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}")

    def launch_game(self) -> None:
        if self.dry_run:
            log.info("DRY RUN launch %s", PACKAGE)
            return
        log.info("launching %s", PACKAGE)
        self._adb("shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}")

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


def grab_host_display(timeout: float = 10) -> "np.ndarray | None":
    """Screenshot of the Pi's own Wayland desktop (what VNC shows) as BGR, or
    None if the desktop doesn't answer. Taking it also makes the compositor
    render a frame. Never raises."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    try:
        res = subprocess.run(["grim", "-l", "0", "-"], env=env, timeout=timeout,
                             capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("host display grab failed: %s", e)
        return None
    if res.returncode != 0:
        log.warning("host display grab failed: %s", res.stderr.decode(errors="replace").strip())
        return None
    img = cv2.imdecode(np.frombuffer(res.stdout, np.uint8), cv2.IMREAD_COLOR)
    return img


def nudge_host_display() -> bool:
    """Make the host compositor render a frame (see Device.capture). True if it answered."""
    return grab_host_display() is not None


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
