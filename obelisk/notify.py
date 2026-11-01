"""Phone alerts via ntfy (https://ntfy.sh): install the ntfy app and subscribe
to the topic in notify.conf (kept out of git; anyone who knows it can read the
messages, so they only carry bot status).

Each alert has a key; the same key is sent at most once per REPEAT_AFTER
seconds (remembered in logs/state.json), so a stuck bot doesn't spam. Sending
never raises: a failed alert is only logged.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("obelisk.notify")

CONF = Path(__file__).resolve().parent.parent / "notify.conf"
SERVER = "https://ntfy.sh"
REPEAT_AFTER = 6 * 3600


def topic() -> str | None:
    try:
        for line in CONF.read_text().splitlines():
            if line.startswith("topic="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


DASHBOARD_CONF = Path(__file__).resolve().parent.parent / "dashboard.conf"


def dashboard_url() -> str | None:
    """Link opened when an alert is tapped (dashboard.conf 'url=')."""
    try:
        for line in DASHBOARD_CONF.read_text().splitlines():
            if line.startswith("url="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode().replace("\n", " ")


def _post(title: str, message: str, priority: str, tags: str, image=None) -> None:
    t = topic()
    if not t:
        log.info("no notify.conf topic; alert not sent: %s", title)
        return
    headers = {"Title": _ascii(title), "Priority": priority, "Tags": tags}
    if url := dashboard_url():
        headers["Click"] = url
    data = message.encode("utf-8")
    if image is not None and Path(image).exists():
        import cv2                                  # attach a small JPEG of the screenshot
        img = cv2.imread(str(image))
        if img is not None:
            img = cv2.resize(img, (360, int(img.shape[0] * 360 / img.shape[1])),
                             interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                data = buf.tobytes()
                headers["Filename"] = "screen.jpg"
                headers["Message"] = _ascii(message)[:1000]
    req = urllib.request.Request(f"{SERVER}/{t}", data=data, method="PUT" if "Filename" in headers
                                 else "POST", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def alert(key: str, title: str, message: str, priority: str = "default",
          tags: str = "robot", state=None, force: bool = False, sender=_post,
          image=None) -> bool:
    """Send unless the same key was sent within REPEAT_AFTER. Returns True if sent."""
    if state is None:
        from .jobs import State
        state = State()
    sent = state.get("alerts_sent") or {}
    now = time.time()
    if not force and now - sent.get(key, 0) < REPEAT_AFTER:
        return False
    try:
        if image is not None:
            sender(title, message, priority, tags, image)
        else:
            sender(title, message, priority, tags)
    except Exception as e:                      # never let an alert break the bot
        log.warning("alert %r not sent: %s", key, e)
        return False
    sent[key] = now
    state.set("alerts_sent", sent)
    log.info("alert sent: %s", title)
    return True
