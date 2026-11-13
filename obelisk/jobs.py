"""Jobs. Each press targets an anchor matched on a fresh frame, and is
followed by waiting for the expected result; anything unexpected aborts."""

from __future__ import annotations

import fcntl
import json
import logging
import os
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
# cropped notification boxes, saved before they are tapped away (dashboard card)
NOTIF_DIR = FAIL_DIR.parent / "notifications"
NOTIF_KEEP = 1000


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


MINING_WAIT = 3.0   # s; the game's own toasts ("Free bomb") hide anchors for ~1-2 s


def ensure_mining(dev, what: str = "", timeout: float = MINING_WAIT) -> np.ndarray:
    """Fresh frame of the mining screen, waiting out brief overlays; aborts
    (with the last frame) if it doesn't show up within `timeout`."""
    frame = wait_for(dev, lambda f: screens.identify(f) == MINING, timeout=timeout, interval=0.5)
    if frame is None:
        raise JobAborted(f"{what}not on mining screen", dev.capture())
    return frame


def press_anchor(dev, name: str, frame: np.ndarray) -> None:
    """Press anchor `name` as matched on `frame`; a missing anchor aborts the
    job (with a screenshot) instead of crashing the bot."""
    m = A.get(name).find(frame)
    if m is None:
        raise JobAborted(f"button '{name}' not found", frame)
    dev.press(*m.center)


def save_failure(frame: Optional[np.ndarray], reason: str) -> Optional[Path]:
    if frame is None:
        return None
    FAIL_DIR.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in reason)[:40]
    path = FAIL_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}.png"
    cv2.imwrite(str(path), frame)
    log.warning("saved failure screenshot %s", path)
    return path


class FreebieJob:
    """Open Store -> claim Freebie Pack if ready -> verify -> close Store."""

    name = "freebie"
    # Checked in game 2026-11-10: a freebie every 3:19 real (6:58 game time at
    # game speed 2.10x), bank 19, so an empty bank is full in ~63 min, sooner
    # with game-speed buffs. Normal sessions claim on arrival; in keep-open mode
    # this gap applies (it was 60 min, which let the bank overflow).
    MIN_GAP = 20 * 60       # claim at most this often when the badge says ready
    MAX_GAP = 2 * 60 * 60   # open the Store anyway this often (badge fallback)
    FULL_GAP = 60           # bank shown full (the Store icon turns solid green): claim now

    def __init__(self):
        self.last_run = float("-inf")

    def due(self, now: float, frame: np.ndarray) -> bool:
        since = now - self.last_run
        if since >= self.MAX_GAP:
            return True
        if since >= self.FULL_GAP and screens.store_fill(frame) >= 1.0:
            return True
        return since >= self.MIN_GAP and screens.store_badge(frame)

    def run(self, dev) -> str:
        """Returns 'claimed' or 'nothing'; raises JobAborted otherwise."""
        self.last_run = time.monotonic()
        frame = ensure_mining(dev)

        icon = screens.store_button(frame)
        if icon is None:
            raise JobAborted("store button not found", frame)
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


# s; a big freebie reward (jackpot: "Stonks Bonus", 5x loot rolls) lists its
# rewards over the Store for several seconds and hides the store anchors
STORE_SETTLE = 12.0


def close_store(dev) -> None:
    frame = wait_for(dev, lambda f: screens.identify(f) == STORE, timeout=STORE_SETTLE)
    if frame is None:
        raise JobAborted("expected store before closing", dev.capture())
    press_anchor(dev, "store_close", frame)
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
        return (screens.lootbug_stored(frame) is not None
                or screens.lootbug_sprite(frame) is not None)

    def run(self, dev) -> str:
        """Stored lootbugs: double-tap their row in the left-side list (Loot-All
        takes the bug on the field too). With the game kept open on 2026-11-06 the
        bot stopped finding the bug on the field after 33 min while they kept
        piling up in storage. A lone bug with nothing stored has no row: press it."""
        self.last_run = time.monotonic()
        frame = ensure_mining(dev)
        row = screens.lootbug_stored(frame)
        how = "stored row"
        if row is None or not self._double_tap(dev, row):
            bug = screens.lootbug_sprite(dev.capture() if row is not None else frame)
            if bug is None:
                if row is not None:
                    raise JobAborted("Loot-All didn't open from the stored-lootbug row", dev.capture())
                return "none"
            how = "field"
            dev.press(*bug.center)
            if wait_for(dev, lambda f: screens.identify(f) == LOOTBUG_POPUP, timeout=5) is None:
                raise JobAborted("lootbug popup did not open", dev.capture())
        close_lootbug_popup(dev)
        after = dev.capture()
        if screens.lootbug_sprite(after) is not None or screens.lootbug_stored(after) is not None:
            log.warning("a lootbug is still there after looting (new arrival?)")
        log.info("lootbugs looted (%s)", how)
        return "looted"

    DOUBLE_TAP_TRIES = 2
    POPUP_WAIT = 3.0        # s for Loot-All to open after a double tap

    def _double_tap(self, dev, row) -> bool:
        """True once the Loot-All popup is open. The two presses go out back to
        back (~0.2 s apart, which the game accepts); a slow pair only leaves the
        "Tap again to loot all" hint, so it is tried once more."""
        for _ in range(self.DOUBLE_TAP_TRIES):
            dev.press(*row.center)
            dev.press(*row.center)
            if wait_for(dev, lambda f: screens.identify(f) == LOOTBUG_POPUP,
                        timeout=self.POPUP_WAIT) is not None:
                return True
            log.warning("lootbug: the double tap on the stored row didn't open Loot-All")
            frame = dev.capture()
            if screens.identify(frame) != MINING:
                return False
            row = screens.lootbug_stored(frame) or row
        return False


def close_lootbug_popup(dev) -> None:
    frame = dev.capture()
    if screens.identify(frame) != LOOTBUG_POPUP:
        raise JobAborted("expected lootbug popup before closing", frame)
    dev.press(*screens.lootbug_close(frame).center)
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

    def __init__(self, state: "State | None" = None, clock=time.time):
        self.last_run = float("-inf")
        self.state = state if state is not None else State()
        self.clock = clock

    def due(self, now: float, frame: np.ndarray) -> bool:
        if now - self.last_run < self.MIN_GAP:
            return False
        return screens.founder_crate(frame) is not None

    def run(self, dev) -> str:
        self.last_run = time.monotonic()
        frame = ensure_mining(dev)
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
    """Tiny persistent key/value store (logs/state.json), shared by the bot, the
    dashboard and the timers. Every get/set re-reads the file, so a long-lived State
    never writes back stale values (it once re-paused the bot after a resume).
    Writes take a lock, so two processes can't undo each other's change, and are
    flushed to disk before the rename, so a power cut leaves the old or new file."""

    PATH = Path(__file__).resolve().parent.parent / "logs" / "state.json"

    def __init__(self, path: "Path | None" = None):
        self.path = path or self.PATH

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.error("could not read %s: %s", self.path.name, e)
            return {}

    @property
    def data(self) -> dict:
        return self._load()

    def get(self, key, default=None):
        return self._load().get(key, default)

    def set(self, key, value) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.with_suffix(".lock"), "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)        # released when the file closes
                data = self._load()
                data[key] = value
                tmp = self.path.with_suffix(".tmp")
                with open(tmp, "w") as f:
                    f.write(json.dumps(data, indent=1))
                    f.flush()
                    os.fsync(f.fileno())
                tmp.replace(self.path)
                folder = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(folder)
                finally:
                    os.close(folder)
        except OSError as e:
            log.warning("could not save state: %s", e)


class OfflineGainsInterrupt:
    """After the game returns from the background it shows "Offline Gains":
    first an options screen (press Confirm; the Buffs/Drone-fuel checkboxes are
    never touched, so buffs stay unconsumed), then a results screen (Close)."""

    def handle(self, dev, frame: np.ndarray) -> bool:
        screen = screens.identify(frame)
        if screen == OFFLINE_OPTIONS:
            log.info("interrupt: offline gains options -> Confirm")
            press_anchor(dev, "offline_confirm", frame)
            if wait_for(dev, lambda f: screens.identify(f) != OFFLINE_OPTIONS, timeout=5) is None:
                save_failure(dev.capture(), "offline_confirm_stuck")
            return True
        if screen == OFFLINE_RESULTS:
            log.info("interrupt: offline gains results -> Close")
            press_anchor(dev, "offline_close", frame)
            if wait_for(dev, lambda f: screens.identify(f) != OFFLINE_RESULTS, timeout=5) is None:
                save_failure(dev.capture(), "offline_close_stuck")
            return True
        return False


def keep_pi_save(state: State) -> "str | None":
    """Resolve a conflict in this device's favour: the next session's upload
    overwrites the cloud save. Returns the reason if it must not be done: during
    a version block clearing the conflict would only remove the check, and the Pi
    could overwrite the newer save later."""
    blocked = state.get("cloud_version_block")
    if blocked:
        return blocked
    state.set("cloud_conflict", None)
    state.set("cloud_last_upload", None)
    state.set("cloud_upload_unverified", None)
    state.set("cloud_upload_allowed", True)     # the one way past "no record of the last upload"
    return None


class CloudSync:
    """Cloud save sync, policy "B": never overwrite progress from another device.

    The bot remembers the cloud play time right after each of its uploads
    (state "cloud_last_upload"). If the cloud save later shows anything else,
    another device has uploaded: the bot sets "cloud_conflict" and stops
    playing/uploading until resolved (python -m obelisk cloud --keep-pi, or a
    download once the cloud has more play time). The game itself only enables
    Download when the cloud save has MORE play time than this device's, and
    not when that save comes from a different game version (see _version_block).

    Upload = two presses (red -> orange "armed" -> upload), verified by the
    cloud play time changing; Download is never pressed (not automated).
    """

    name = "cloud"
    LOAD_WAIT = 10.0          # s for the cloud info to load / a toast to pass
    UPLOAD_WAIT = 45.0        # s to see the cloud time change (usually ~3 s)
    LATE_UPLOAD_SLACK = 120   # s of play time either side of the Pi's when judging a late upload

    def __init__(self, state: "State | None" = None):
        self.state = state if state is not None else State()

    def check(self, dev) -> str:
        return self._visit(dev, upload=False)

    def upload(self, dev) -> str:
        return self._visit(dev, upload=True)

    def _visit(self, dev, upload: bool) -> str:
        frame = ensure_mining(dev, "cloud: ")
        press_anchor(dev, "grid_button", frame)
        frame = wait_for(dev, lambda f: screens.identify(f) == GRID_MENU, timeout=5)
        if frame is None:
            raise JobAborted("cloud: grid menu did not open", dev.capture())
        press_anchor(dev, "grid_cloud_button", frame)
        frame = wait_for(dev, lambda f: screens.identify(f) == CLOUD, timeout=6)
        if frame is None:
            raise JobAborted("cloud: Cloud screen did not open", dev.capture())
        try:
            frame = self._wait_loaded(dev, frame)
            result = self._decide(dev, frame, upload)
        finally:
            self._close(dev)
        return result

    def _wait_loaded(self, dev, frame):
        """A frame whose cloud play time is readable and not zero, or the last
        one seen after LOAD_WAIT (then _decide skips the upload).

        The Cloud screen can show before the game has fetched the cloud info:
        on 2026-11-08 19:25 it read 00d 00h 00m 00s with no version, and the bot
        raised a false conflict. A "Free bomb" toast across the cloud row made
        it unreadable on 2026-11-08 16:55 and 2026-11-09 21:40 (no upload)."""
        from .playtime import CLOUD_ROW, read_seconds

        def loaded(f):
            return screens.identify(f) == CLOUD and bool(read_seconds(f, CLOUD_ROW))

        if loaded(frame):
            return frame
        got = wait_for(dev, loaded, timeout=self.LOAD_WAIT, interval=0.5)
        if got is not None:
            log.info("cloud: cloud info readable after a short wait")
            return got
        last = dev.capture()
        return last if screens.identify(last) == CLOUD else frame

    def _conflict(self, frame, why: str) -> str:
        if not self.state.get("cloud_conflict"):
            shot = save_failure(frame, "cloud_conflict")
            from .notify import alert
            alert("cloud_conflict", "Obelisk bot: cloud save conflict",
                  f"{why}. The bot is paused (no playing/uploading) until you decide: "
                  "keep the Pi's save with 'python -m obelisk cloud --keep-pi', or "
                  "play longer on the phone and upload, then ask for a download.",
                  priority="high", tags="warning,cloud", state=self.state, image=shot, force=True)
        self.state.set("cloud_conflict", why)
        log.warning("cloud: CONFLICT (%s). Not playing or uploading until resolved: "
                    "python -m obelisk cloud --keep-pi  (or make the other device's save "
                    "longer and download it)", why)
        return "conflict"

    def _remind(self, key: str, why: str) -> None:
        """Same key as the first alert, so at most one per 6 h. One alert wasn't
        enough: a conflict on 2026-11-03 held the bot up for 13 h."""
        from .notify import alert
        alert(key, "Obelisk bot: still held up by the cloud save",
              f"{why}. Sessions are being skipped, so nothing is being claimed. "
              "See the Cloud row on the dashboard.", priority="high", tags="warning,cloud",
              state=self.state)

    def _version_block(self, frame) -> "str | None":
        """Why the game refuses a cloud save with more play time, or None.

        The cloud having more play time normally enables Download. If it stays
        greyed out, the saves' game versions are the known cause (2026-11-05:
        iPhone v2.2.52, Pi v2.2.51; updating the Pi enabled it). No --keep-pi
        or longer play on the phone can fix that, only updating the game."""
        from .playtime import (CLOUD_ROW, CLOUD_VERSION_ROW, LOCAL_ROW, LOCAL_VERSION_ROW,
                               read_seconds, read_version)
        cloud_v = read_version(frame, CLOUD_VERSION_ROW)
        local_v = read_version(frame, LOCAL_VERSION_ROW)
        if cloud_v is not None and cloud_v == local_v:
            return None
        cloud_t, local_t = read_seconds(frame, CLOUD_ROW), read_seconds(frame, LOCAL_ROW)
        if cloud_t is None or local_t is None or cloud_t <= local_t:
            return None
        if cloud_v is None or local_v is None:
            return "the game refuses the cloud save, probably a game version difference"
        shown = (".".join(map(str, cloud_v)), ".".join(map(str, local_v)))
        return "the cloud save is from game v%s but the Pi's save is from v%s" % shown

    def _version_blocked(self, frame, why: str) -> str:
        if not self.state.get("cloud_version_block"):
            shot = save_failure(frame, "cloud_version_block")
            from .notify import alert
            alert("cloud_version_block", "Obelisk bot: update the game",
                  f"{why}, so the game greys out Download. The bot is paused and "
                  "nothing was overwritten. Update whichever device has the older "
                  "version (Pi: Play Store); the next session then loads the cloud "
                  "save by itself. Don't use --keep-pi.",
                  priority="high", tags="warning,cloud", state=self.state, image=shot, force=True)
        else:
            self._remind("cloud_version_block", why)
        self.state.set("cloud_version_block", why)
        self.state.set("cloud_conflict", why)
        log.warning("cloud: VERSION BLOCK (%s). Not playing or uploading until the game "
                    "is updated", why)
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

    def _adopt_late_upload(self, frame) -> None:
        """An upload can land after UPLOAD_WAIT (2026-11-06: the next session then
        saw an unknown cloud time and raised a false conflict). If the cloud time
        now fits the Pi's play time at that attempt, it is the Pi's own upload;
        anything else goes through the normal checks."""
        from .playtime import CLOUD_ROW, read_seconds
        window = self.state.get("cloud_upload_unverified")
        if not window:
            return
        cloud_t = read_seconds(frame, CLOUD_ROW)
        if not cloud_t or cloud_t == self.state.get("cloud_last_upload"):
            return                      # unreadable, not loaded or nothing arrived: a later visit decides
        if window[0] <= cloud_t <= window[1]:
            log.info("cloud: the unverified upload did arrive (cloud play time %ds)", cloud_t)
            self.state.set("cloud_last_upload", cloud_t)
            self.state.set("cloud_upload_allowed", None)
        self.state.set("cloud_upload_unverified", None)

    def _decide(self, dev, frame, upload: bool) -> str:
        from .playtime import CLOUD_ROW, LOCAL_ROW, read_seconds
        frame = self._autosave_off(dev, frame)
        self._adopt_late_upload(frame)
        last = self.state.get("cloud_last_upload")
        download_on = screens.cloud_button(frame, CLOUD_DOWNLOAD) != "disabled"
        blocked = None if download_on else self._version_block(frame)
        if blocked:
            return self._version_blocked(frame, blocked)
        if self.state.get("cloud_version_block"):
            # the versions match again: judge the saves afresh
            log.info("cloud: version block over")
            self.state.set("cloud_version_block", None)
            self.state.set("cloud_conflict", None)
        if download_on:
            cloud_t = read_seconds(frame, CLOUD_ROW)
            if cloud_t is not None and last is not None and cloud_t == last:
                # our own upload; the Pi's stored local save just lags behind it
                return "in_sync"
            if cloud_t is None:
                return self._conflict(frame, "cloud save has more play time, but its time is unreadable")
            return self._download(dev, frame, cloud_t)
        if self.state.get("cloud_conflict"):
            log.warning("cloud: conflict still unresolved (%s)", self.state.get("cloud_conflict"))
            self._remind("cloud_conflict", self.state.get("cloud_conflict"))
            return "conflict"
        cloud_t = read_seconds(frame, CLOUD_ROW)
        if cloud_t is None:
            log.warning("cloud: couldn't read the cloud play time; not uploading this session")
            save_failure(frame, "cloud_time_unreadable")
            return "unreadable"
        if cloud_t == 0:
            # the game hadn't loaded the cloud info (00d 00h 00m 00s, no version):
            # not another device's save, so no conflict; the next session looks again
            log.warning("cloud: the cloud info didn't load; not uploading this session")
            save_failure(frame, "cloud_not_loaded")
            return "unreadable"
        if last is None and not self.state.get("cloud_upload_allowed"):
            # e.g. state.json lost: without the record, the check that protects
            # another device's save can't work, so nothing may be overwritten
            return self._conflict(frame, "the bot has no record of its last upload, so it can't "
                                         "tell whether the cloud save is safe to overwrite")
        if last is not None and cloud_t != last:
            return self._conflict(frame, f"cloud play time {cloud_t}s != this bot's last upload {last}s")
        up = screens.cloud_button(frame, CLOUD_UPLOAD)
        if not upload or up == "disabled":
            return "in_sync" if up == "disabled" else "local_newer"
        x1, y1, x2, y2 = CLOUD_TIME
        before = frame[y1:y2, x1:x2].copy()
        local_t = read_seconds(frame, LOCAL_ROW)
        dev.press(*_center(CLOUD_UPLOAD))
        armed = wait_for(dev, lambda f: screens.cloud_button(f, CLOUD_UPLOAD) == "armed", timeout=3)
        if armed is None:
            raise JobAborted("cloud: Upload did not arm", dev.capture())
        if screens.cloud_button(armed, CLOUD_DOWNLOAD) != "disabled":
            raise JobAborted("cloud: Download became active; not uploading", armed)
        dev.press(*_center(CLOUD_UPLOAD))
        done = wait_for(dev, lambda f: not np.array_equal(f[y1:y2, x1:x2], before),
                        timeout=self.UPLOAD_WAIT)
        if done is None:
            self._remember_attempt(local_t)
            raise JobAborted("cloud: upload not verified (cloud time unchanged)", dev.capture())
        self.state.set("cloud_upload_unverified", None)
        dev.sleep(0.5)
        new_t = read_seconds(dev.capture(), CLOUD_ROW)
        if new_t is None:
            log.warning("cloud: uploaded, but couldn't read the new cloud play time")
            save_failure(done, "cloud_time_unreadable_after_upload")
            self._remember_attempt(local_t)     # the next visit reads it instead
        else:
            self.state.set("cloud_last_upload", new_t)
            self.state.set("cloud_upload_allowed", None)
            log.info("cloud: uploaded (cloud play time now %ds)", new_t)
        return "uploaded"

    def _remember_attempt(self, local_t: "int | None") -> None:
        """For _adopt_late_upload at the next visit."""
        if local_t is not None:
            self.state.set("cloud_upload_unverified",
                           [local_t - self.LATE_UPLOAD_SLACK,
                            local_t + self.UPLOAD_WAIT + self.LATE_UPLOAD_SLACK])

    def _download(self, dev, frame, cloud_t: int) -> str:
        """Another device uploaded MORE play time: load it (the user's rule:
        keep the newer save). Two presses, like Upload; the game then reloads
        and shows its Offline Gains popups (handled by the interrupts)."""
        from .notify import alert
        log.warning("cloud: another device's save has more play time (%ds): downloading it", cloud_t)
        dev.press(*_center(CLOUD_DOWNLOAD))
        armed = wait_for(dev, lambda f: screens.cloud_button(f, CLOUD_DOWNLOAD) == "armed", timeout=3)
        if armed is None:
            raise JobAborted("cloud: Download did not arm", dev.capture())
        dev.press(*_center(CLOUD_DOWNLOAD))
        loaded = wait_for(dev, lambda f: screens.identify(f) != CLOUD, timeout=20)
        if loaded is None:
            raise JobAborted("cloud: game did not reload after Download", dev.capture())
        self.state.set("cloud_last_upload", cloud_t)
        self.state.set("cloud_upload_allowed", None)
        self.state.set("cloud_conflict", None)
        alert("cloud_download", "Obelisk bot: loaded your newer save from the cloud",
              f"Another device (e.g. your iPhone) uploaded a save with more play time "
              f"({cloud_t // 3600}h total); the Pi downloaded it and continues from there.",
              tags="arrow_down,cloud", state=self.state, force=True)
        log.info("cloud: downloaded (cloud play time %ds)", cloud_t)
        return "downloaded"

    @staticmethod
    def _close(dev) -> None:
        # an overlay (e.g. a "Free bomb" toast) can briefly hide the anchors:
        # wait until the screen is recognisable before deciding
        frame = wait_for(dev, lambda f: screens.identify(f) in
                         (CLOUD, GRID_MENU, MINING, OFFLINE_OPTIONS, OFFLINE_RESULTS), timeout=5)
        if frame is None:
            raise JobAborted("cloud: screen not recognised when closing", dev.capture())
        if screens.identify(frame) in (CLOUD, GRID_MENU):
            press_anchor(dev, "store_close", frame)
            if wait_for(dev, lambda f: screens.identify(f) == MINING, timeout=6) is None:
                raise JobAborted("cloud: could not get back to the mining screen", dev.capture())


def _center(box) -> tuple[int, int]:
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


class NotificationInterrupt:
    """Dismiss a left-side notification box by pressing it: it covers the
    stored-lootbug row and part of the field (2026-11-10 00:44 in keep-open, a
    bug went unlooted for ~7 min behind "Elixir Drone is now Grade 6!").
    Verified by the box disappearing; backs off for 10 min if a press doesn't
    dismiss it, so a box that won't go away is never pressed in a loop.
    A crop of each box goes to logs/notifications/ first, for the dashboard;
    "Free bomb" / "Cherry charges" reward toasts are pressed away but not kept."""

    MIN_GAP = 60
    BACKOFF = 600
    PRESS_X = 180            # inside the box, right of the timer rows it covers

    def __init__(self):
        self.next_ok = float("-inf")
        self.last_crop: Optional[np.ndarray] = None

    def save(self, frame: np.ndarray, box: tuple[int, int]) -> Optional[Path]:
        """Keep a crop of the box for the dashboard, unless it is the same
        notice as the last one saved (a box that wouldn't close, pressed again)."""
        x0, y0, x1, y1 = screens.notification_crop(frame, box)
        crop = frame[y0:y1, x0:x1]
        last = self.last_crop
        if last is not None and last.shape == crop.shape and \
                np.abs(last.astype(int) - crop).mean() < 3:
            return None
        self.last_crop = crop.copy()
        try:
            NOTIF_DIR.mkdir(parents=True, exist_ok=True)
            path = NOTIF_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.png"
            cv2.imwrite(str(path), crop)
            for old in sorted(NOTIF_DIR.glob("*.png"))[:-NOTIF_KEEP]:
                old.unlink()
            return path
        except OSError as e:
            log.debug("could not save the notification picture: %s", e)
            return None

    def handle(self, dev, frame: np.ndarray) -> bool:
        now = time.monotonic()
        if now < self.next_ok or screens.identify(frame) != MINING:
            return False
        box = screens.notification_box(frame)
        if box is None:
            return False
        self.next_ok = now + self.MIN_GAP
        if screens.is_reward_toast(box):      # not counted on the stats page either
            log.info("interrupt: dismissing a reward toast at y %d-%d", *box)
        else:
            self.save(frame, box)
            log.info("interrupt: dismissing a notification at y %d-%d", *box)
        dev.press(self.PRESS_X, (box[0] + box[1]) // 2)
        if wait_for(dev, lambda f: screens.notification_box(f) is None, timeout=3) is None:
            log.warning("notification still shown after the press; leaving it for %d min",
                        self.BACKOFF // 60)
            self.next_ok = now + self.BACKOFF
        return True


class MenuCloseInterrupt:
    """Safety net: if the screen sits on one of the bot's own menus (Store,
    grid menu, Cloud) for AFTER consecutive polls, e.g. after an aborted job,
    press their (matched) close X to get back to the mining screen."""

    AFTER = 3

    def __init__(self):
        self.count = 0

    def handle(self, dev, frame: np.ndarray) -> bool:
        if screens.identify(frame) not in (STORE, GRID_MENU, CLOUD):
            self.count = 0
            return False
        self.count += 1
        if self.count < self.AFTER:
            return False
        self.count = 0
        x = A.get("store_close").find(frame)
        if x is None:
            return False
        log.warning("interrupt: still on a menu after %d polls; closing it", self.AFTER)
        dev.press(*x.center)
        wait_for(dev, lambda f: screens.identify(f) == MINING, timeout=5)
        return True


class AnrInterrupt:
    """Android's "Process system isn't responding" dialog (seen after Waydroid
    froze the container): press "Wait", never "Close app"."""

    def handle(self, dev, frame: np.ndarray) -> bool:
        if screens.identify(frame) != screens.ANR:
            return False
        log.warning("interrupt: Android 'isn't responding' dialog -> Wait")
        press_anchor(dev, "anr_wait", frame)
        wait_for(dev, lambda f: screens.identify(f) != screens.ANR, timeout=5)
        return True
