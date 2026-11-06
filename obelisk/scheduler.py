"""One loop: watchdog -> capture -> interrupts (popups) -> first due job.

* Watchdog: every FOREGROUND_EVERY seconds, make sure the game is the app in
  front; if not, relaunch it and wait for the mining screen.
* Interrupts get first look at every frame (popups that can appear anywhere,
  e.g. the Lootbug gem-shop popup, which is closed via its X).
* Jobs only run from the mining screen, in priority order; the first due job
  runs and the loop starts over with a fresh frame.
* Keep-open mode (dashboard): a session that stays open until a chosen time, so
  the buffs tick. It ends through the normal path: HOME, then a fresh session
  whose game restart gets current cloud info before anything is uploaded.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2

from . import screens
from .gems import stable_gems
from .notify import alert
from .outage import report_up
from .jobs import JobAborted, State, save_failure, wait_for

# Exit code for "stopped on purpose after repeated job failures"; the
# systemd unit must NOT restart on it (RestartPreventExitStatus=3).
EXIT_TOO_MANY_FAILURES = 3

# Screens that mean "the game is up" after a (re)launch; popups on them are
# handled by the interrupts.
GAME_READY = (screens.MINING, screens.OFFLINE_OPTIONS, screens.OFFLINE_RESULTS,
              screens.LOOTBUG_POPUP, screens.ANR)

log = logging.getLogger("obelisk.scheduler")


LATEST = Path(__file__).resolve().parent.parent / "logs" / "latest.jpg"


def save_latest(dev) -> None:
    """Last frame of each session, for the dashboard (small JPEG)."""
    try:
        frame = dev.capture()
        small = cv2.resize(frame, (360, 640), interpolation=cv2.INTER_AREA)
        LATEST.parent.mkdir(parents=True, exist_ok=True)
        tmp = LATEST.with_suffix(".tmp.jpg")
        cv2.imwrite(str(tmp), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        tmp.replace(LATEST)
    except Exception as e:                      # never let this break a session
        log.debug("could not save latest frame: %s", e)


def pause_active(until, now: float) -> bool:
    """`until` is state "paused_until": None, "forever" or a timestamp."""
    return until is not None and (until == "forever" or now < float(until))


class Scheduler:
    """Failure policy: a JobAborted counts as a failure. The bot stops (exit 3,
    systemd won't restart it) after `max_failures` failures in a row within
    one session, or when MAX_FAILED_SESSIONS sessions in a row each had a
    failure. A one-off glitch in sessions hours apart never stops it (it once
    did, on 2026-11-01). The gem guard stops at once."""

    FOREGROUND_EVERY = 60      # seconds between "is the game in front?" checks
    LAUNCH_WAIT = 90           # seconds to wait for the mining screen after a relaunch
    MAX_FAILED_SESSIONS = 3
    KEEP_OPEN_SHOT_EVERY = 5 * 60   # seconds between dashboard pictures in keep-open mode

    def __init__(self, dev, jobs, interrupts=(), poll: float = 2.0,
                 max_unknown: int = 15, max_failures: int = 3, planner=None, cloud=None):
        self.dev = dev
        self.jobs = list(jobs)
        self.interrupts = list(interrupts)
        self.poll = poll
        self.max_unknown = max_unknown
        self.max_failures = max_failures
        self.unknown = 0
        self.failures = 0               # failures in a row (this session)
        self.session_failed = False
        self.failed_sessions = 0        # sessions in a row that had a failure
        self.last_foreground_check = float("-inf")
        self.planner = planner          # SessionPlanner, or None = game always open
        self.last_frame = None
        self.last_screen = None
        self.session_t0 = None          # monotonic start of the current session
        self.cloud = cloud              # CloudSync, or None
        self.pause_logged = False
        self.kept_until = None          # this session is being held open until then
        self.last_shot = float("-inf")

    def run_forever(self) -> None:
        while True:
            if self.planner is None:
                self.tick()
                if self.last_screen == screens.MINING:
                    report_up()
            else:
                self.session_step()

    # ---- sessions (game in the background between them) -------------------

    def control(self, now: float) -> str | None:
        """Dashboard controls via state.json: 'paused' (with reason) or None.
        Also applies a requested 'session_now'."""
        st = State()
        until = st.get("paused_until")
        if until is not None:
            if pause_active(until, now):
                if not self.pause_logged:
                    log.info("paused from the dashboard (%s)", "until resumed" if until == "forever"
                             else "until " + time.strftime("%H:%M", time.localtime(float(until))))
                    self.pause_logged = True
                return "paused"
            st.set("paused_until", None)
            log.info("pause ended")
        if self.pause_logged:
            log.info("resumed")
            self.pause_logged = False
        if st.get("session_now"):
            st.set("session_now", None)
            log.info("session requested from the dashboard")
            self.planner.next_start = now
        return None

    def session_step(self) -> None:
        p = self.planner
        now = p.clock()
        if p.started is None:
            if self.control(now) == "paused":
                self.dev.sleep(10.0)
                return
            start_at = p.earliest if self.keep_open_until(now) else max(p.next_start, p.earliest)
            if now < start_at:
                self.dev.sleep(min(10.0, start_at - now))
                return
            self.begin_session()
            return
        self.tick()
        now = p.clock()
        until = self.keep_open_until(now)
        if until:
            self.keep_open_step(now, until)
        elif self.kept_until:
            self.end_keep_open()
        elif self.session_complete(now):
            self.end_session()

    # ---- keep-open mode (the game stays in front, buffs tick) --------------

    def keep_open_until(self, now: float) -> float | None:
        """State "keep_open_until" if the mode is on. The dashboard sets it, so
        it survives bot restarts; running out or a pause clears it."""
        st = State()
        until = st.get("keep_open_until")
        if until is None:
            return None
        if now < float(until) and not pause_active(st.get("paused_until"), now):
            return float(until)
        st.set("keep_open_until", None)
        return None

    def keep_open_step(self, now: float, until: float) -> None:
        if until != self.kept_until:    # just started, or the dashboard changed the time
            self.kept_until = until
            log.info("keep-open: the game stays in front until %s",
                     time.strftime("%H:%M", time.localtime(until)))
        if self.last_screen == screens.MINING and not self.session_failed:
            report_up()                 # hours can pass before this session ends
        if now - self.last_shot >= self.KEEP_OPEN_SHOT_EVERY:
            self.last_shot = now
            save_latest(self.dev)

    def end_keep_open(self) -> None:
        """No upload yet: the cloud info on screen is as old as this session, and
        the phone may have uploaded since. HOME saves the game; the next session
        restarts it, reads the cloud afresh and uploads if that is safe."""
        self.kept_until = None
        log.info("keep-open: over; game to background, then a normal session")
        self.end_session(upload=False, next_soon=True)

    def begin_session(self) -> None:
        self.planner.start()
        self.session_t0 = time.monotonic()
        self.failures, self.session_failed = 0, False
        log.info("session start")
        for job in self.jobs:                 # banked while offline: look right away
            if job.name == "freebie":
                job.last_run = float("-inf")
            if hasattr(job, "last_check"):
                job.last_check = float("-inf")
        if self.cloud is not None:
            # the game only fetches cloud-save info at startup; restart it so a
            # newer save from another device is seen (it saves when backgrounded)
            self.dev.restart_game()
            self.wait_for_game()
        else:
            self.ensure_game_in_front(planned=True)
        self.last_foreground_check = time.monotonic()
        if not self.settle():
            log.warning("session: mining screen not stable after %d s; continuing", self.SETTLE_TIMEOUT)
        result = self.cloud_step("check") if self.cloud is not None else None
        if result == "downloaded":
            self.settle()                      # reloaded: Offline Gains popups again
        if result in ("conflict", "cloud_newer"):
            log.warning("session skipped: cloud save conflict (see 'python -m obelisk cloud --status')")
            if State().get("keep_open_until") is not None:
                log.warning("keep-open: cancelled by the cloud save conflict")
                State().set("keep_open_until", None)
            self.end_session(upload=False)

    SETTLE_TIMEOUT = 60

    def settle(self) -> bool:
        """After the game returns it shows stale and then fresh "Offline Gains"
        popups and ignores the first presses; handle popups until the mining
        screen is seen 3 times in a row, before any job may run."""
        self.dev.sleep(3)
        deadline = time.monotonic() + self.SETTLE_TIMEOUT
        stable = 0
        while time.monotonic() < deadline:
            frame = self.dev.capture()
            if self.handle_interrupts(frame):
                stable = 0
                continue
            stable = stable + 1 if screens.identify(frame) == screens.MINING else 0
            if stable >= 3:
                return True
            self.dev.sleep(1.5)
        return False

    def session_complete(self, now: float) -> bool:
        p = self.planner
        if p.timed_out(now):
            self.session_failed = True
            log.warning("session hit the %d s limit", p.MAX_SESSION)
            shot = save_failure(self.last_frame, "session_timeout")
            alert("session_timeout", "Obelisk bot: a session couldn't finish",
                  f"Stopped after {p.MAX_SESSION // 60} min on screen '{self.last_screen}'. "
                  "Possibly a new popup or a game update; screenshot in logs/failures/.",
                  tags="hourglass", image=shot)
            return True
        if self.last_screen != screens.MINING:
            return False
        for job in self.jobs:
            if job.name == "freebie" and job.last_run < self.session_t0:
                return False
        if any(j.name == "lootbug" for j in self.jobs) and self.last_frame is not None \
                and screens.lootbug_sprite(self.last_frame) is not None:
            return False
        return True

    def cloud_step(self, what: str):
        try:
            result = getattr(self.cloud, what)(self.dev)
            log.info("cloud %s -> %s", what, result)
            return result
        except JobAborted as e:
            self.job_failed(f"cloud {what}", e)
            return None

    def job_failed(self, what: str, e: JobAborted) -> None:
        self.failures += 1
        self.session_failed = True
        log.error("%s aborted: %s (%d/%d)", what if what.startswith("cloud") else f"job {what}",
                  e, self.failures, self.max_failures)
        save_failure(e.frame, f"{what.replace(' ', '_')}_{e}")
        if self.failures >= self.max_failures:
            self.stop("repeated_failures", "Obelisk bot STOPPED: repeated failures",
                      f"{self.failures} failures in a row this session. Last: {what}: {e}.")

    def stop(self, key: str, title: str, message: str, tags: str = "rotating_light") -> None:
        """Stop on purpose (exit 3: systemd won't restart). The alert carries the
        health picture, which says whether the Pi/Android are fine (then it's a
        game problem: see logs/failures/) or need a restart/reboot."""
        log.critical("%s; stopping (exit %d)", message, EXIT_TOO_MANY_FAILURES)
        image = None
        try:
            from .health import run as health_run
            image, h = health_run(self.dev, copy_to_failures=True, stopped=True)
            message = f"{h.summary()} {message}"
        except Exception as he:                 # never let this block the alert
            log.warning("health picture failed: %s", he)
        alert(key, title, f"{message} Failure screenshots: dashboard / logs/failures/. "
              "Restart: dashboard -> Restart bot.", priority="urgent", tags=tags, force=True,
              image=image)
        raise SystemExit(EXIT_TOO_MANY_FAILURES)

    def handle_interrupts(self, frame) -> bool:
        """True if an interrupt handled this frame. An interrupt that can't
        finish (e.g. its button vanished) is logged with a screenshot; it never
        crashes the bot, and the next poll looks again."""
        for h in self.interrupts:
            try:
                if h.handle(self.dev, frame):
                    return True
            except JobAborted as e:
                log.error("interrupt %s failed: %s", type(h).__name__, e)
                save_failure(e.frame, f"interrupt_{e}")
                return True
        return False

    def end_session(self, upload: bool = True, next_soon: bool = False) -> None:
        if upload and self.cloud is not None and self.last_screen == screens.MINING:
            self.cloud_step("upload")
        save_latest(self.dev)
        self.dev.go_home()
        length = self.planner.finish()
        if next_soon:
            self.planner.next_start = self.planner.earliest
        log.info("session end: %d s online; next session %s", length,
                 time.strftime("%H:%M:%S", time.localtime(self.planner.next_start)))
        self.record_session_result()

    def record_session_result(self) -> None:
        self.failed_sessions = self.failed_sessions + 1 if self.session_failed else 0
        if self.failed_sessions >= self.MAX_FAILED_SESSIONS:
            self.stop("repeated_failures", "Obelisk bot STOPPED: failing every session",
                      f"The last {self.failed_sessions} sessions in a row each had a failure.")
        if not self.session_failed:
            report_up()                 # "OK again" if this ends an outage

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_foreground_check >= self.FOREGROUND_EVERY:
            self.last_foreground_check = now
            self.ensure_game_in_front()

        frame = self.dev.capture()
        self.last_frame, self.last_screen = frame, None
        if self.handle_interrupts(frame):
            return

        screen = screens.identify(frame)
        self.last_screen = screen
        if screen != screens.MINING:
            self.unknown += 1
            if self.unknown == self.max_unknown:
                log.warning("off the mining screen for %d polls (%s); waiting, not pressing",
                            self.unknown, screen)
                shot = save_failure(frame, f"stuck_{screen}")
                alert("stuck_screen", "Obelisk bot: stuck on an unknown screen",
                      f"Not on the mining screen for {self.unknown} polls ({screen}). "
                      "It waits and presses nothing; screenshot in logs/failures/.",
                      tags="question", image=shot)
            self.dev.sleep(self.poll)
            return
        self.unknown = 0

        for job in self.jobs:
            if job.due(now, frame):
                self.run_job(job)
                break
        self.dev.sleep(self.poll)

    def run_job(self, job) -> None:
        before = stable_gems(self.dev)
        try:
            log.info("job %s -> %s", job.name, job.run(self.dev))
            self.failures = 0
        except JobAborted as e:
            self.job_failed(job.name, e)
        finally:
            self.check_gems(job, before)

    def check_gems(self, job, before) -> None:
        """Gem guard: no bot job may ever leave us with fewer gems."""
        after = stable_gems(self.dev)
        if before is None or after is None:
            log.info("gem guard: couldn't read gems (%s -> %s); not checked", before, after)
            return
        if after < before:
            save_failure(self.dev.capture(), f"GEM_GUARD_{job.name}_{before}_to_{after}")
            self.stop("gem_guard", "Obelisk bot STOPPED: gems dropped",
                      f"GEM GUARD: gems went {before} -> {after} during job '{job.name}'. "
                      "Check the game before restarting.", tags="rotating_light,gem")
        log.info("gem guard: %d -> %d ok", before, after)

    def wait_for_game(self) -> bool:
        if wait_for(self.dev, lambda f: screens.identify(f) in GAME_READY,
                    timeout=self.LAUNCH_WAIT, interval=3) is None:
            log.error("game (re)started but no known screen within %d s", self.LAUNCH_WAIT)
            save_failure(self.dev.capture(), "relaunch_no_known_screen")
            return False
        return True

    def ensure_game_in_front(self, planned: bool = False) -> None:
        if self.dev.game_in_front():
            return
        if planned:
            log.info("bringing the game to the front for the session")
        else:
            log.warning("game is not in front; relaunching it")
        self.dev.launch_game()
        if self.wait_for_game():        # popups (Offline Gains etc.): interrupts handle them
            log.info("game in front again")
