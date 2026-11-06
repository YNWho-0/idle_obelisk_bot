"""Health check: one phone-sized picture that says whether to reboot the Pi.

  venv/bin/python -m obelisk health        # also the dashboard's "Check health now"

Checks, in order: Pi desktop (grim) -> Waydroid session -> adb -> Android
screen (screencap) -> game in front -> the bot itself (service up, sessions
still happening). The picture has a coloured verdict
banner on top, the check list, the Android screen (or "NO PICTURE") and the
Pi desktop. Saved as logs/health.png; crash alerts attach a copy.

Only reads: screencap, `waydroid status`, `adb get-state`, dumpsys, systemctl. Never
presses anything, so it is safe to run while the bot is in a session.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .device import Device, DeviceError, grab_host_display

log = logging.getLogger("obelisk.health")

LOGS = Path(__file__).resolve().parent.parent / "logs"
LATEST = LOGS / "health.png"

OK, RESTART, REBOOT = "ok", "restart", "reboot"
BOT_STALE = 2 * 3600    # no session for this long means the bot is hung (normally 50 min)
VERDICTS = {   # level -> (banner colour BGR, headline, what to do)
    OK: ((60, 150, 40), "NO REBOOT NEEDED",
         "Pi and Android look healthy. The bot retries by itself."),
    RESTART: ((20, 140, 230), "RESTART WAYDROID + BOT",
              "Dashboard -> Restart Waydroid + bot. Not fixed in 5 min? Reboot Pi."),
    REBOOT: ((40, 40, 200), "REBOOT THE PI",
             "Dashboard -> Reboot Pi (or power-cycle it if the dashboard is down)."),
}


@dataclass
class Health:
    checks: list = field(default_factory=list)      # (name, ok, detail)
    level: str = OK
    reason: str = ""
    stopped: bool = False          # the bot stopped itself (exit 3): it won't retry
    bot_ok: bool = True            # service up and sessions happening
    bot_service_down: bool = False
    android: "np.ndarray | None" = None
    desktop: "np.ndarray | None" = None
    when: dt.datetime = field(default_factory=dt.datetime.now)

    @property
    def headline(self) -> str:
        return VERDICTS[self.level][1]

    @property
    def advice(self) -> str:
        if self.level == OK and self.stopped:
            return "Pi + Android fine: a game problem. See failure shots, then Restart bot."
        return VERDICTS[self.level][2]

    def summary(self) -> str:
        bad = [f"{n}: {d}" for n, ok, d in self.checks if not ok]
        ok_text = "Pi and Android OK" if bad else "all checks OK"
        return f"{self.headline} - {self.reason or ok_text}." + (
            f" Failed: {'; '.join(bad)}." if bad else "")


def _run(*cmd: str, timeout: float = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    except OSError as e:
        return -1, str(e)


def waydroid_status() -> dict:
    _, out = _run("waydroid", "status")
    st = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def bot_status(state=None, service=None, now: "float | None" = None) -> tuple[bool, str, bool]:
    """(fine, detail, service down). Separate from the verdict: the Pi can be
    perfectly healthy with the bot dead, and nothing else would say so."""
    from .scheduler import pause_active
    if state is None:
        from .jobs import State
        state = State()
    now = time.time() if now is None else now
    active = service() if service else _run("systemctl", "--user", "is-active", "obelisk-bot")[1]
    if active in ("inactive", "failed"):
        return False, f"service {active}", True
    if pause_active(state.get("paused_until"), now):
        return True, "paused from the dashboard", False
    last = state.get("last_session_at")
    if last is not None and now - last > BOT_STALE:
        return False, f"no session for {int((now - last) // 60)} min", False
    return True, "running", False


def check(dev: "Device | None" = None, grab=grab_host_display, wd_status=waydroid_status,
          adb_state=None) -> Health:
    dev = dev or Device()
    h = Health()

    h.desktop = grab()
    h.checks.append(("Pi desktop", h.desktop is not None,
                     "responding" if h.desktop is not None else "no answer from the compositor"))

    wd = wd_status()
    session, container = wd.get("Session", "?"), wd.get("Container", "?")
    wd_ok = session == "RUNNING" and container == "RUNNING"
    h.checks.append(("Waydroid", wd_ok, f"session {session}, container {container}"))

    if adb_state is None:
        rc, out = _run("adb", "-s", dev.serial, "get-state", timeout=8)
        adb_ok = rc == 0 and out.strip() == "device"
    else:
        adb_ok, out = adb_state()
    h.checks.append(("adb", adb_ok, "connected" if adb_ok else (out or "not connected")[:60]))

    screen_ok = game_ok = False
    if adb_ok:
        try:
            h.android = dev.capture()           # nudges the desktop + retries on a hang
            screen_ok = True
        except DeviceError as e:
            h.checks.append(("Android screen", False,
                             "frozen (screencap hangs)" if "timeout" in str(e) else str(e)[:60]))
        if screen_ok:
            h.checks.append(("Android screen", True, "picture OK"))
            try:
                game_ok = dev.game_in_front()
                h.checks.append(("Game", True, "in front" if game_ok else
                                 "in background (normal between sessions)"))
            except DeviceError as e:
                h.checks.append(("Game", False, str(e)[:60]))
    else:
        h.checks.append(("Android screen", False, "skipped: no adb"))

    h.bot_ok, detail, h.bot_service_down = bot_status()
    h.checks.append(("Bot", h.bot_ok, detail))
    h.stopped = h.bot_service_down

    if h.desktop is None:
        h.level, h.reason = REBOOT, "the Pi's desktop is not responding"
    elif not wd_ok:
        h.level, h.reason = RESTART, "Waydroid is not running"
    elif not adb_ok:
        h.level, h.reason = RESTART, "Android can't be reached over adb"
    elif not screen_ok:
        h.level, h.reason = RESTART, "Android's screen is frozen"
    else:
        h.level, h.reason = OK, ""
    return h


# ---------------------------------------------------------------- picture

W = 720
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, txt, x, y, scale=0.6, color=(235, 235, 235), thick=1):
    cv2.putText(img, txt, (x, y), FONT, scale, color, thick, cv2.LINE_AA)


def _fit(img, w):
    return cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)


def render(h: Health) -> np.ndarray:
    col_w = 352
    android = _fit(h.android, col_w) if h.android is not None else None
    desk = _fit(h.desktop, col_w) if h.desktop is not None else None
    top = 120 + 34 * len(h.checks) + 64
    shots_h = 16 + max(android.shape[0] if android is not None else 600,
                       desk.shape[0] if desk is not None else 190)
    img = np.full((top + shots_h + 12, W, 3), 28, np.uint8)

    colour, headline, _ = VERDICTS[h.level]
    advice = h.advice
    cv2.rectangle(img, (0, 0), (W, 110), colour, -1)
    _text(img, headline, 16, 48, 1.15, (255, 255, 255), 3)
    _text(img, advice, 16, 88, 0.52, (255, 255, 255), 1)

    y = 148
    for name, ok, detail in h.checks:
        mark, c = ("OK ", (110, 210, 110)) if ok else ("BAD", (90, 90, 245))
        _text(img, mark, 16, y, 0.65, c, 2)
        _text(img, f"{name}: {detail}", 80, y, 0.6)
        y += 34
    _text(img, f"checked {h.when:%a %d %b %H:%M:%S}", 16, y + 4, 0.5, (160, 160, 160))

    y0 = top
    _text(img, "Android (game)", 8, y0 + 4, 0.5, (160, 160, 160))
    if android is not None:
        img[y0 + 12:y0 + 12 + android.shape[0], 8:8 + col_w] = android
    else:
        cv2.rectangle(img, (8, y0 + 12), (8 + col_w, y0 + 600), (60, 60, 60), 2)
        _text(img, "NO PICTURE", 100, y0 + 300, 0.9, (90, 90, 245), 2)
    x1 = 8 + col_w + 8
    _text(img, "Pi desktop (what VNC shows)", x1, y0 + 4, 0.5, (160, 160, 160))
    if desk is not None:
        img[y0 + 12:y0 + 12 + desk.shape[0], x1:x1 + col_w] = desk
    else:
        cv2.rectangle(img, (x1, y0 + 12), (x1 + col_w, y0 + 190), (60, 60, 60), 2)
        _text(img, "NO PICTURE", x1 + 100, y0 + 110, 0.9, (90, 90, 245), 2)
    return img


def alert_if_unhealthy(path: Path, h: Health, state=None) -> bool:
    """For the periodic check (obelisk-health.timer, every 50 min): catch a
    frozen display etc. between sessions, before the bot trips over it, and a
    dead or hung bot. Quiet while booting (bootcheck reports) and during an outage
    the bot is already reporting, unless its service is down. Each verdict is sent
    at most once per 6 h; a send that fails is tried again at the next check."""
    from .bootcheck import uptime_s
    from .notify import alert
    if (h.level == OK and h.bot_ok) or uptime_s() < 900:
        return False
    if state is None:
        from .jobs import State
        state = State()
    if state.get("outage") and not h.bot_service_down:
        return False
    if h.level == OK:
        return alert("health_bot", "Obelisk bot isn't running", h.summary(),
                     priority="high", tags="warning", state=state, image=path)
    return alert(f"health_{h.level}", f"Obelisk: {h.headline.lower()}", h.summary(),
                 priority="high", tags="warning", state=state, image=path)


def run(dev: "Device | None" = None, copy_to_failures: bool = False,
        stopped: bool = False) -> tuple[Path, Health]:
    """Check, render, save logs/health.png (and a copy in logs/failures/).
    stopped=True: the bot has stopped itself, so "retries by itself" is wrong."""
    h = check(dev)
    h.stopped = stopped or h.stopped
    img = render(h)
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = LATEST.with_suffix(".tmp.png")
    cv2.imwrite(str(tmp), img)
    tmp.replace(LATEST)
    path = LATEST
    if copy_to_failures:
        fail_dir = LOGS / "failures"
        fail_dir.mkdir(parents=True, exist_ok=True)
        path = fail_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_health_{h.level}.png"
        shutil.copyfile(LATEST, path)
    log.info("health: %s", h.summary())
    return path, h
