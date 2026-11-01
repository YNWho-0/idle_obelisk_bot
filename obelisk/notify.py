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


def _post(title: str, message: str, priority: str, tags: str) -> None:
    t = topic()
    if not t:
        log.info("no notify.conf topic; alert not sent: %s", title)
        return
    req = urllib.request.Request(
        f"{SERVER}/{t}", data=message.encode("utf-8"), method="POST",
        headers={"Title": title, "Priority": priority, "Tags": tags})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def alert(key: str, title: str, message: str, priority: str = "default",
          tags: str = "robot", state=None, force: bool = False, sender=_post) -> bool:
    """Send unless the same key was sent within REPEAT_AFTER. Returns True if sent."""
    if state is None:
        from .jobs import State
        state = State()
    sent = state.get("alerts_sent") or {}
    now = time.time()
    if not force and now - sent.get(key, 0) < REPEAT_AFTER:
        return False
    try:
        sender(title, message, priority, tags)
    except Exception as e:                      # never let an alert break the bot
        log.warning("alert %r not sent: %s", key, e)
        return False
    sent[key] = now
    state.set("alerts_sent", sent)
    log.info("alert sent: %s", title)
    return True
