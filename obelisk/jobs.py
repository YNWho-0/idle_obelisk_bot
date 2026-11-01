"""Jobs. Each press targets an anchor matched on a fresh frame, and is
followed by waiting for the expected result; anything unexpected aborts."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from . import screens
from .screens import (A, CLOUD, CLOUD_AUTOSAVE, CLOUD_DOWNLOAD, CLOUD_TIME, CLOUD_UPLOAD, GRID_MENU,
                      LOOTBUG_POPUP, MINING, OFFLINE_OPTIONS, OFFLINE_RESULTS, STORE)

log = logging.getLogger("obelisk.jobs")

FAIL_DIR = Path(__file__).resolve().parent.parent / "logs" / "failures"


class JobAborted(RuntimeError):
    def __init__(self, reason: str, frame: Optional[np.ndarray] = None):
        super().__init__(reason)
        self.frame = frame


def wait_for(dev, pred: Callable[[np.ndarray], object], timeout: float,
             interval: float = 0.4) -> Optional[np.ndarray]:
    """Capture until pred(frame) is truthy; returns that frame or None."""
    deadline = time.monotonic() + timeout
    while True:
        frame = dev.capture()
        if pred(frame):
            return frame
        if time.monotonic() >= deadline:
            return None
        dev.sleep(interval)


def save_failure(frame: Optional[np.ndarray], reason: str) -> None:
    if frame is None:
        return
    FAIL_DIR.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in reason)[:40]
    path = FAIL_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}.png"
    cv2.imwrite(str(path), frame)
    log.warning("saved failure screenshot %s", path)


class FreebieJob:
    """Open Store -> claim Freebie Pack if ready -> verify -> close Store."""

    name = "freebie"
    MIN_GAP = 60 * 60       # claim at most hourly when the badge says ready
    MAX_GAP = 2 * 60 * 60   # open the Store anyway this often (badge fallback)

    def __init__(self):
        self.last_run = float("-inf")

    def due(self, now: float, frame: np.ndarray) -> bool:
        since = now - self.last_run
        if since >= self.MAX_GAP:
            return True
        return since >= self.MIN_GAP and screens.store_badge(frame)

    def run(self, dev) -> str:
        """Returns 'claimed' or 'nothing'; raises JobAborted otherwise."""
        self.last_run = time.monotonic()
        frame = dev.capture()
        if screens.identify(frame) != MINING:
            raise JobAborted("not on mining screen", frame)

        icon = A.get("store_icon").find(frame)
        dev.press(*icon.center)
        frame = wait_for(dev, lambda f: screens.identify(f) == STORE, timeout=6)
        if frame is None:
            raise JobAborted("store did not open", dev.capture())

        try:
            frame = wait_for(dev, lambda f: screens.freebie_state(f)[0] != "unknown", timeout=3)
            if frame is None:
                frame = find_freebie_row(dev)
            if frame is None:
                raise JobAborted("freebie row not recognised", dev.capture())
            state, claim = screens.freebie_state(frame)
            if state == "ready":
                dev.press(*claim.center)
                done = wait_for(dev, lambda f: screens.freebie_state(f)[0] == "empty", timeout=8)
                if done is None:
                    raise JobAborted("claim not verified (button not dimmed)", dev.capture())
                log.info("freebie claimed")
                result = "claimed"
            else:
                log.info("freebie: nothing banked")
                result = "nothing"
        except JobAborted:
            try:
                close_store(dev)
            except JobAborted as e2:
                log.error("also could not close store: %s", e2)
            raise
        close_store(dev)
        return result


# The Store keeps its scroll position; the Freebie row sits below the Value
# Packs / Special / Perks sections. Drag along the right edge (x=700: no
# buttons there), down first, then back up.
STORE_DRAG_X = 700
STORE_DRAGS = [(1000, 450)] * 5 + [(450, 1000)] * 10


def find_freebie_row(dev):
    for y1, y2 in STORE_DRAGS:
        dev.swipe(STORE_DRAG_X, y1, STORE_DRAG_X, y2, 500)
        dev.sleep(1.0)
        frame = dev.capture()
        if screens.identify(frame) != STORE:
            return None
        if screens.freebie_state(frame)[0] != "unknown":
            return frame
    return None


def close_store(dev) -> None:
    frame = dev.capture()
    if screens.identify(frame) != STORE:
        raise JobAborted("expected store before closing", frame)
    dev.press(*A.get("store_close").find(frame).center)
    if wait_for(dev, lambda f: screens.identify(f) == MINING, timeout=6) is None:
        raise JobAborted("store did not close", dev.capture())


class LootbugJob:
    """Press a lootbug on the field -> popup -> close it. One press loots all
    (game option "Tap any Lootbug to Loot-All").

    Free loot is credited automatically (game option "Auto-Loot free Lootbug
    offerings"); the popup itself is a gem shop, so the only thing ever pressed
    in it is its top-right X.
    """

    name = "lootbug"
    MIN_GAP = 60       # seconds between attempts
    CHECK_EVERY = 20   # seconds between looks (bugs bank for hours; matching is slow)

    def __init__(self):
        self.last_run = float("-inf")
        self.last_check = float("-inf")

    def due(self, now: float, frame: np.ndarray) -> bool:
        if now - self.last_run < self.MIN_GAP or now - self.last_check < self.CHECK_EVERY:
            return False
        self.last_check = now
        return screens.lootbug_sprite(frame) is not None

    def run(self, dev) -> str:
        self.last_run = time.monotonic()
        frame = dev.capture()
        if screens.identify(frame) != MINING:
            raise JobAborted("not on mining screen", frame)
        bug = screens.lootbug_sprite(frame)
        if bug is None:
            return "none"
        dev.press(*bug.center)
        if wait_for(dev, lambda f: screens.identify(f) == LOOTBUG_POPUP, timeout=5) is None:
            raise JobAborted("lootbug popup did not open", dev.capture())
        close_lootbug_popup(dev)
        if screens.lootbug_sprite(dev.capture()) is not None:
            log.warning("a lootbug is still on the field after looting (new arrival?)")
        log.info("lootbugs looted")
        return "looted"


def close_lootbug_popup(dev) -> None:
    frame = dev.capture()
    if screens.identify(frame) != LOOTBUG_POPUP:
        raise JobAborted("expected lootbug popup before closing", frame)
    dev.press(*A.get("lootbug_close").find(frame).center)
    if wait_for(dev, lambda f: screens.identify(f) == MINING, timeout=6) is None:
        raise JobAborted("lootbug popup did not close", dev.capture())


class LootbugPopupInterrupt:
    """Whenever the Lootbug popup (a gem shop) is open, close it via its X."""

    def handle(self, dev, frame: np.ndarray) -> bool:
        if screens.identify(frame) != LOOTBUG_POPUP:
            return False
        log.info("interrupt: closing lootbug popup")
        try:
            close_lootbug_popup(dev)
        except JobAborted as e:
            save_failure(e.frame, f"interrupt_{e}")
            log.error("interrupt failed: %s", e)
        return True


class FounderJob:
    """Press the falling Founder parachute crate; rewards are credited at once
    (no popup). Verified by the crate disappearing.

    The crate drops on the game's own hourly cycle (not restarted by claims)
    and waits on the field until pressed, so it is simply looked for on every
    poll (~0.35 s per frame on the Pi; sessions are short). The last claim
    time is still recorded in logs/state.json for the daily summary.
    """

    name = "founder"
    MIN_GAP = 10
    PERIOD = 3600
    WINDOW = (-120, 300)    # seconds around the expected drop
    FALLBACK = 60

    def __init__(self, state: "State | None" = None, clock=time.time):
        self.last_run = float("-inf")
        self.last_look = float("-inf")
        self.state = state if state is not None else State()
        self.clock = clock

    def in_window(self, t: float) -> bool:
        """Always look. The drop cycle is NOT anchored to claims (the Store
        countdown read 28:48 right after a claim) and the crate waits on the
        field, so there is no window to predict; sessions are short anyway."""
        return True

    def due(self, now: float, frame: np.ndarray) -> bool:
        if now - self.last_run < self.MIN_GAP:
            return False
        if not self.in_window(self.clock()) and now - self.last_look < self.FALLBACK:
            return False
        self.last_look = now
        return screens.founder_crate(frame) is not None

    def run(self, dev) -> str:
        self.last_run = time.monotonic()
        frame = dev.capture()
        if screens.identify(frame) != MINING:
            raise JobAborted("not on mining screen", frame)
        crate = screens.founder_crate(frame)
        if crate is None:
            return "none"
        dev.press(*crate.center)
        if wait_for(dev, lambda f: screens.founder_crate(f) is None, timeout=4) is None:
            raise JobAborted("founder crate still there after press", dev.capture())
        self.state.set("founder_last_claim", self.clock())
        log.info("founder drop claimed")
        return "claimed"


class State:
    """Tiny persistent key/value store (logs/state.json)."""

    PATH = Path(__file__).resolve().parent.parent / "logs" / "state.json"

    def __init__(self, path: "Path | None" = None):
        self.path = path or self.PATH
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("could not save state: %s", e)


class NotificationInterrupt:
    """Dismiss left-side notification toasts (they can hide the first lootbug
    spot) by pressing them; verified by the box disappearing. Backs off for
    10 min if a press doesn't dismiss it."""

    MIN_GAP = 60
    BACKOFF = 600

    def __init__(self):
        self.next_ok = float("-inf")

    def handle(self, dev, frame: np.ndarray) -> bool:
        now = time.monotonic()
        if now < self.next_ok or screens.identify(frame) != MINING:
            return False
        box = screens.notification_box(frame)
        if box is None:
            return False
        self.next_ok = now + self.MIN_GAP
        y = (box[0] + box[1]) // 2
        log.info("interrupt: dismissing notification at y %d-%d", *box)
        dev.press(100, y)
        if wait_for(dev, lambda f: screens.notification_box(f) is None, timeout=3) is None:
            log.warning("notification still shown after press; backing off %d s", self.BACKOFF)
            self.next_ok = now + self.BACKOFF
        return True


class OfflineGainsInterrupt:
    """After the game returns from the background it shows "Offline Gains":
    first an options screen (press Confirm; the Buffs/Drone-fuel checkboxes are
    never touched, so buffs stay unconsumed), then a results screen (Close)."""

    def handle(self, dev, frame: np.ndarray) -> bool:
        screen = screens.identify(frame)
        if screen == OFFLINE_OPTIONS:
            log.info("interrupt: offline gains options -> Confirm")
            dev.press(*A.get("offline_confirm").find(frame).center)
            if wait_for(dev, lambda f: screens.identify(f) != OFFLINE_OPTIONS, timeout=5) is None:
                save_failure(dev.capture(), "offline_confirm_stuck")
            return True
        if screen == OFFLINE_RESULTS:
            log.info("interrupt: offline gains results -> Close")
            dev.press(*A.get("offline_close").find(frame).center)
            if wait_for(dev, lambda f: screens.identify(f) != OFFLINE_RESULTS, timeout=5) is None:
                save_failure(dev.capture(), "offline_close_stuck")
            return True
        return False


class CloudSync:
    """Cloud save sync, policy "B": never overwrite progress from another device.

    The bot remembers the cloud play time right after each of its uploads
    (state "cloud_last_upload"). If the cloud save later shows anything else,
    another device has uploaded: the bot sets "cloud_conflict" and stops
    playing/uploading until resolved (python -m obelisk cloud --keep-pi, or a
    download once the cloud has more play time). The game itself only enables
    Download when the cloud save has MORE play time than this device's.

    Upload = two presses (red -> orange "armed" -> upload), verified by the
    cloud play time changing; Download is never pressed (not automated).
    """

    name = "cloud"

    def __init__(self, state: "State | None" = None):
        self.state = state if state is not None else State()

    def check(self, dev) -> str:
        return self._visit(dev, upload=False)

    def upload(self, dev) -> str:
        return self._visit(dev, upload=True)

    def _visit(self, dev, upload: bool) -> str:
        frame = dev.capture()
        if screens.identify(frame) != MINING:
            raise JobAborted("cloud: not on mining screen", frame)
        dev.press(*A.get("grid_button").find(frame).center)
        frame = wait_for(dev, lambda f: screens.identify(f) == GRID_MENU, timeout=5)
        if frame is None:
            raise JobAborted("cloud: grid menu did not open", dev.capture())
        dev.press(*A.get("grid_cloud_button").find(frame).center)
        frame = wait_for(dev, lambda f: screens.identify(f) == CLOUD, timeout=6)
        if frame is None:
            raise JobAborted("cloud: Cloud screen did not open", dev.capture())
        try:
            result = self._decide(dev, frame, upload)
        finally:
            self._close(dev)
        return result

    def _conflict(self, frame, why: str) -> str:
        if not self.state.get("cloud_conflict"):
            save_failure(frame, "cloud_conflict")
            from .notify import alert
            alert("cloud_conflict", "Obelisk bot: cloud save conflict",
                  f"{why}. The bot is paused (no playing/uploading) until you decide: "
                  "keep the Pi's save with 'python -m obelisk cloud --keep-pi', or "
                  "play longer on the phone and upload, then ask for a download.",
                  priority="high", tags="warning,cloud", state=self.state)
        self.state.set("cloud_conflict", why)
        log.warning("cloud: CONFLICT (%s). Not playing or uploading until resolved: "
                    "python -m obelisk cloud --keep-pi  (or make the other device's save "
                    "longer and download it)", why)
        return "conflict"

    def _autosave_off(self, dev, frame):
        """Loading a save restores its settings, incl. Cloud Auto-Save = on;
        uploads must only come from this bot's checked path."""
        if not screens.cloud_autosave_on(frame):
            return frame
        log.warning("cloud: in-game Cloud Auto-Save was ON again; turning it off")
        dev.press(*_center(CLOUD_AUTOSAVE))
        off = wait_for(dev, lambda f: not screens.cloud_autosave_on(f), timeout=3)
        if off is None:
            raise JobAborted("cloud: could not turn Cloud Auto-Save off", dev.capture())
        return off

    def _decide(self, dev, frame, upload: bool) -> str:
        from .playtime import CLOUD_ROW, read_seconds
        frame = self._autosave_off(dev, frame)
        if screens.cloud_button(frame, CLOUD_DOWNLOAD) != "disabled":
            return self._conflict(frame, "cloud save has more play time than this device's")
        if self.state.get("cloud_conflict"):
            log.warning("cloud: conflict still unresolved (%s)", self.state.get("cloud_conflict"))
            return "conflict"
        cloud_t = read_seconds(frame, CLOUD_ROW)
        if cloud_t is None:
            log.warning("cloud: couldn't read the cloud play time; not uploading this session")
            save_failure(frame, "cloud_time_unreadable")
            return "unreadable"
        last = self.state.get("cloud_last_upload")
        if last is not None and cloud_t != last:
            return self._conflict(frame, f"cloud play time {cloud_t}s != this bot's last upload {last}s")
        up = screens.cloud_button(frame, CLOUD_UPLOAD)
        if not upload or up == "disabled":
            return "in_sync" if up == "disabled" else "local_newer"
        x1, y1, x2, y2 = CLOUD_TIME
        before = frame[y1:y2, x1:x2].copy()
        dev.press(*_center(CLOUD_UPLOAD))
        armed = wait_for(dev, lambda f: screens.cloud_button(f, CLOUD_UPLOAD) == "armed", timeout=3)
        if armed is None:
            raise JobAborted("cloud: Upload did not arm", dev.capture())
        if screens.cloud_button(armed, CLOUD_DOWNLOAD) != "disabled":
            raise JobAborted("cloud: Download became active; not uploading", armed)
        dev.press(*_center(CLOUD_UPLOAD))
        done = wait_for(dev, lambda f: not np.array_equal(f[y1:y2, x1:x2], before), timeout=12)
        if done is None:
            raise JobAborted("cloud: upload not verified (cloud time unchanged)", dev.capture())
        dev.sleep(0.5)
        new_t = read_seconds(dev.capture(), CLOUD_ROW)
        self.state.set("cloud_last_upload", new_t)
        if new_t is None:
            log.warning("cloud: uploaded, but couldn't read the new cloud play time; "
                        "next session can't compare (no conflict check that once)")
            save_failure(done, "cloud_time_unreadable_after_upload")
        else:
            log.info("cloud: uploaded (cloud play time now %ds)", new_t)
        return "uploaded"

    @staticmethod
    def _close(dev) -> None:
        frame = dev.capture()
        if screens.identify(frame) in (CLOUD, GRID_MENU):
            dev.press(*A.get("store_close").find(frame).center)
            if wait_for(dev, lambda f: screens.identify(f) == MINING, timeout=6) is None:
                raise JobAborted("cloud: could not get back to the mining screen", dev.capture())


def _center(box) -> tuple[int, int]:
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
