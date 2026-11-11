"""Outage tracking across systemd restarts (state in logs/state.json).

The bot process dies on a device error and systemd restarts it every 60 s, so
"still down" can only be known from state that outlives the process:

  * first failure of an outage  -> "crashed" alert (always sent)
  * still failing after 30 min / 1 h -> "still down" reminders
  * still failing after GIVE_UP -> "STOPPED" alert, exit 3 (systemd then
    stays stopped; see RestartPreventExitStatus in obelisk-bot.service)
  * first completed session afterwards -> "OK again" alert

On 2026-11-02 the bot was down 3 h with a single alert: the per-restart
alerts were swallowed by the 6 h repeat limit and nothing said it recovered.
"""

from __future__ import annotations

import logging
import time

from .notify import alert as _alert

log = logging.getLogger("obelisk.outage")

REMINDERS = (30 * 60, 60 * 60)   # seconds into an outage to say "still down"
GIVE_UP = 2 * 3600               # stop retrying after this long
NEW_OUTAGE_GAP = 15 * 60         # no failure for this long = a later failure is a new outage
EXIT_GAVE_UP = 3                 # systemd: RestartPreventExitStatus=3

RESTART_HINT = ("Connect with VNC (rpi-connect) to wake the desktop, then "
                "systemctl --user restart obelisk-bot")


def _state(state):
    if state is None:
        from .jobs import State
        state = State()
    return state


def _fmt(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def report_down(title: str, error: BaseException | str, state=None, now: float | None = None,
                sender=None, image_fn=None) -> bool:
    """Record a failure that is about to end the process. Returns True if the bot
    should give up (caller exits with EXIT_GAVE_UP instead of re-raising).
    image_fn() -> (picture path, verdict text) is only called when an alert is
    actually sent (it takes a few seconds: see health.py)."""
    state = _state(state)
    now = time.time() if now is None else now
    kw = {"state": state, "force": True}
    if sender is not None:
        kw["sender"] = sender
    err = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)

    def alert(key, title, message, **more):         # attach the health picture + verdict
        if image_fn is not None:
            try:
                path, verdict = image_fn()
                more["image"], message = path, f"{verdict} {message}"
            except Exception as e:                    # never let this block the alert
                log.warning("health picture failed: %s", e)
        return _alert(key, title, message, **more)

    o = state.get("outage") or {}
    if not o or now - o.get("last_fail", 0) > NEW_OUTAGE_GAP:
        state.set("outage", {"since": now, "last_fail": now, "reminders": 0})
        alert("crash", title, f"{err}. systemd retries every 60 s; you'll get a "
              "message when it's OK again.", priority="high", tags="warning", **kw)
        return False
    elapsed = now - o["since"]
    o["last_fail"] = now
    if elapsed >= GIVE_UP:
        o["gave_up"] = now
        state.set("outage", o)
        log.error("down for %s; giving up (exit %d)", _fmt(elapsed), EXIT_GAVE_UP)
        alert("outage_gave_up", "Obelisk bot STOPPED: down too long",
              f"Failing for {_fmt(elapsed)}, no more retries. Last: {err}. {RESTART_HINT}",
              priority="urgent", tags="rotating_light", **kw)
        return True
    n = o.get("reminders", 0)
    if n < len(REMINDERS) and elapsed >= REMINDERS[n]:
        o["reminders"] = n + 1
        alert("outage_reminder", f"Obelisk bot still down ({_fmt(elapsed)})",
              f"Still failing: {err}. Gives up after {_fmt(GIVE_UP)}. {RESTART_HINT}",
              priority="high", tags="warning", **kw)
    state.set("outage", o)
    return False


FREEZE_RESTART_AFTER = 5 * 60    # frozen this long (several systemd retries): restart Waydroid
FREEZE_RESTART_GAP = 6 * 3600    # frozen again within this of the last restart: it didn't help
FREEZE_REBOOT_AFTER = 10 * 60    # still/again frozen this long after a Waydroid restart: reboot
FREEZE_REBOOT_GAP = 6 * 3600     # at most one reboot in this long; then the outage path carries on


def screen_frozen(error: BaseException | str) -> bool:
    """Device.capture() timed out even after nudging the desktop and retrying."""
    text = str(error)
    return text.startswith("timeout:") and "screencap" in text


def restart_if_frozen(error: BaseException | str, state=None, now: float | None = None,
                      start_restart=None, sender=None) -> bool:
    """Android's screen froze (2026-11-09 08:54: every screencap hung for an
    hour until the Pi was rebooted by hand; the health check said "RESTART
    WAYDROID + BOT"). Once it has lasted FREEZE_RESTART_AFTER, start the
    dashboard's Waydroid + bot restart, at most once per FREEZE_RESTART_GAP.
    If the screen is frozen FREEZE_REBOOT_AFTER after that restart (that
    morning only a Pi reboot fixed it), reboot the Pi, at most once per
    FREEZE_REBOOT_GAP and never while the bot is paused (someone may be
    using the Pi). After that the normal outage path (reminders, give up)
    carries on. Returns True if a restart or reboot was started (the caller
    then exits 0). Call after report_down()."""
    if not screen_frozen(error):
        return False
    state = _state(state)
    now = time.time() if now is None else now
    o = state.get("outage") or {}
    if not o or now - o.get("since", now) < FREEZE_RESTART_AFTER:
        return False
    last = state.get("freeze_restart_at")
    if start_restart is None:
        from .dashboard import start_restart
    if last is not None and now - last < FREEZE_RESTART_GAP:
        return _reboot_if_still_frozen(state, now, last, start_restart, sender)
    err = start_restart("all")
    if err:
        log.error("Android's screen is frozen, but the Waydroid restart couldn't start: %s", err)
        return False
    state.set("freeze_restart_at", now)
    log.warning("Android's screen has been frozen for %s; restarting Waydroid and the bot",
                _fmt(now - o["since"]))
    kw = {"state": state, "force": True}
    if sender is not None:
        kw["sender"] = sender
    _alert("freeze_restart", "Obelisk: restarting Waydroid",
           f"Android's screen has been frozen for {_fmt(now - o['since'])}. The bot is "
           "restarting Waydroid and itself; nothing to do unless another alert follows.",
           tags="arrows_counterclockwise", **kw)
    return True


def _reboot_if_still_frozen(state, now: float, last_restart: float, start_restart, sender) -> bool:
    """The step after a Waydroid restart that didn't fix the frozen screen."""
    from .scheduler import pause_active
    since = now - last_restart
    rebooted = state.get("freeze_reboot_at")
    if since < FREEZE_REBOOT_AFTER:
        return False                 # the restart may still be settling
    if rebooted is not None and now - rebooted < FREEZE_REBOOT_GAP:
        log.info("Android's screen is frozen again %s after the last Pi reboot; "
                 "not rebooting again", _fmt(now - rebooted))
        return False
    if pause_active(state.get("paused_until"), now):
        log.info("Android's screen is still frozen after the Waydroid restart; "
                 "the bot is paused, so not rebooting the Pi")
        return False
    kw = {"state": state, "force": True}
    if sender is not None:
        kw["sender"] = sender
    # alert first: the reboot can take the network down before a later send
    _alert("freeze_reboot", "Obelisk: rebooting the Pi",
           f"Android's screen is still frozen {_fmt(since)} after the Waydroid restart. "
           "The bot is rebooting the Pi; it's back in about 10 min. Nothing to do unless "
           "another alert follows.", priority="high", tags="arrows_counterclockwise", **kw)
    state.set("freeze_reboot_at", now)     # before: the reboot may kill this process
    err = start_restart("reboot")
    if err:
        state.set("freeze_reboot_at", rebooted)
        log.error("Android's screen is frozen, but the Pi reboot couldn't start: %s", err)
        _alert("freeze_reboot_failed", "Obelisk: couldn't reboot the Pi",
               f"The reboot failed to start ({err}). Reboot it from the dashboard.",
               priority="high", tags="warning", **kw)
        return False
    log.warning("Android's screen is still frozen %s after the Waydroid restart; "
                "rebooting the Pi", _fmt(since))
    return True


def report_up(state=None, now: float | None = None, sender=None) -> bool:
    """Call after a session completed normally. Sends "OK again" once per outage."""
    state = _state(state)
    o = state.get("outage")
    if not o:
        return False
    now = time.time() if now is None else now
    state.set("outage", None)
    kw = {"state": state, "force": True}
    if sender is not None:
        kw["sender"] = sender
    _alert("outage_over", "Obelisk bot OK again",
           f"Back to normal after {_fmt(now - o['since'])} down.", tags="white_check_mark", **kw)
    return True
