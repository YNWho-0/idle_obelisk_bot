"""
Thin wrapper around the `adb` binary for talking to the Waydroid container.

All calls shell out to `adb` rather than using a Python ADB library, since
you already have a known-good adb setup. Screenshots are streamed straight
into memory via `adb exec-out screencap -p`, so nothing ever touches disk.
"""

from __future__ import annotations

import subprocess
import shlex
import time
import logging
from typing import Optional, List

import numpy as np
import cv2

logger = logging.getLogger("idle_obelisk_bot.adb")


class AdbError(RuntimeError):
    pass


class AdbClient:
    def __init__(self, serial: Optional[str] = None, adb_path: str = "adb"):
        """
        serial: device serial as shown by `adb devices`, e.g.
                "192.168.240.112:5555" for Waydroid over TCP, or None to
                let adb pick the only connected device.
        """
        self.serial = serial
        self.adb_path = adb_path

    # ---- low level -----------------------------------------------------

    def _base_cmd(self) -> List[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, args: List[str], timeout: float = 10.0, binary: bool = False):
        cmd = self._base_cmd() + args
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except FileNotFoundError as e:
            raise AdbError(f"adb binary not found ({self.adb_path}): {e}") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb command timed out: {' '.join(cmd)}") from e

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise AdbError(f"adb command failed ({' '.join(cmd)}): {stderr.strip()}")

        return result.stdout if binary else result.stdout.decode(errors="replace")

    # ---- connection management -----------------------------------------

    def connect_tcp(self, host: str, port: int = 5555, retries: int = 3, delay: float = 2.0):
        target = f"{host}:{port}"
        for attempt in range(1, retries + 1):
            try:
                out = self.run(["connect", target], timeout=10)
                logger.info("adb connect %s -> %s", target, out.strip())
                if "connected" in out.lower() or "already" in out.lower():
                    self.serial = target
                    return
            except AdbError as e:
                logger.warning("connect attempt %d/%d failed: %s", attempt, retries, e)
            time.sleep(delay)
        raise AdbError(f"could not connect to {target} after {retries} attempts")

    def is_connected(self) -> bool:
        try:
            out = self.run(["get-state"], timeout=5)
            return out.strip() == "device"
        except AdbError:
            return False

    # ---- screen capture --------------------------------------------------

    def screenshot(self) -> np.ndarray:
        """Returns a BGR numpy array (OpenCV format) of the current screen."""
        raw_png = self.run(["exec-out", "screencap", "-p"], binary=True, timeout=10)
        arr = np.frombuffer(raw_png, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise AdbError("failed to decode screenshot (empty or corrupt PNG stream)")
        return img

    # ---- input --------------------------------------------------------------

    def tap(self, x: int, y: int):
        self.run(["shell", "input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self.run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])

    def input_text(self, text: str):
        # `input text` on Android chokes on literal spaces; %s is its escape for one.
        escaped = text.replace(" ", "%s")
        self.run(["shell", "input", "text", shlex.quote(escaped)])

    def press_key(self, keycode: str):
        # e.g. "KEYCODE_BACK", "KEYCODE_ENTER"
        self.run(["shell", "input", "keyevent", keycode])

    def press_back(self):
        self.press_key("KEYCODE_BACK")

    # ---- app lifecycle -----------------------------------------------------

    def start_app(self, package: str, activity: Optional[str] = None):
        if activity:
            self.run(["shell", "am", "start", "-n", f"{package}/{activity}"])
        else:
            self.run(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])

    def force_stop(self, package: str):
        self.run(["shell", "am", "force-stop", package])

    def is_app_running(self, package: str) -> bool:
        out = self.run(["shell", "pidof", package])
        return bool(out.strip())
