"""Idle Obelisk Miner bot, v2.

Fixed 720x1280 resolution (Android `wm size 720x1280` override inside the
1024x732 Waydroid window). Screens are identified by small anchor regions,
every press targets a freshly matched anchor, and every press is verified by
waiting for the expected next screen.
"""

WIDTH, HEIGHT = 720, 1280
PACKAGE = "com.checkbox.minershminer"
ACTIVITY = ".RunnerActivity"
import os

# Waydroid's adb endpoint (Android's IP on the waydroid0 bridge; see
# `waydroid status`). The only place it is written: scripts/restart_all.sh
# reads it from here too. Override with the OBELISK_ADB_SERIAL env variable.
ADB_SERIAL = os.environ.get("OBELISK_ADB_SERIAL") or "192.168.240.112:5555"
