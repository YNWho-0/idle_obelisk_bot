"""One loop: watchdog -> capture -> interrupts (popups) -> first due job.

* Watchdog: every FOREGROUND_EVERY seconds, make sure the game is the app in
  front; if not, relaunch it and wait for the mining screen.
* Interrupts get first look at every frame (popups that can appear anywhere,
  e.g. the Lootbug gem-shop popup, which is closed via its X).
* Jobs only run from the mining screen, in priority order; the first due job
  runs and the loop starts over with a fresh frame.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2

from . import screens
from .gems import stable_gems
from .notify import alert
from .jobs import JobAborted, save_failure, wait_for

# Exit code for "stopped on purpose after repeated job failures"; the
# systemd unit must NOT restart on it (RestartPreventExitStatus=3).
EXIT_TOO_MANY_FAILURES = 3

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


class Scheduler:
    FOREGROUND_EVERY = 60      # seconds between "is the game in front?" checks
    LAUNCH_WAIT = 90           # seconds to wait for the mining screen after a relaunch

    def __init__(self, dev, jobs, interrupts=(), poll: float = 2.0,
                 max_unknown: int = 15, max_failures: int = 3, planner=None, cloud=None):
        self.dev = dev
        self.jobs = list(jobs)
        self.interrupts = list(interrupts)
        self.poll = poll
        self.max_unknown = max_unknown
        self.max_failures = max_failures
        self.unknown = 0
        self.failures = 0
        self.last_foreground_check = float("-inf")
        self.planner = planner          # SessionPlanner, or None = game always open
        self.last_frame = None
        self.last_screen = None
        self.session_t0 = None          # monotonic start of the current session
        self.cloud = cloud              # CloudSync, or None

    def run_forever(self) -> None:
        while True:
            if self.planner is None:
                self.tick()
            else:
                self.session_step()

    # ---- sessions (game in the background between them) -------------------

    def session_step(self) -> None:
        p = self.planner
        now = p.clock()
        if p.started is None:
            if now < p.next_start:
                self.dev.sleep(min(30.0, p.next_start - now))
                return
            self.begin_session()
            return
        self.tick()
        if self.session_complete(p.clock()):
            self.end_session()

    def begin_session(self) -> None:
        self.planner.start()
        self.session_t0 = time.monotonic()
        log.info("session start")
        for job in self.jobs:                 # banked while offline: look right away
            if job.name == "freebie":
                job.last_run = float("-inf")
            if hasattr(job, "last_check"):
                job.last_check = float("-inf")
        self.ensure_game_in_front(planned=True)
        self.last_foreground_check = time.monotonic()
        if not self.settle():
            log.warning("session: mining screen not stable after %d s; continuing", self.SETTLE_TIMEOUT)
        if self.cloud is not None and self.cloud_step("check") in ("conflict", "cloud_newer"):
            log.warning("session skipped: cloud save conflict (see 'python -m obelisk cloud --status')")
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
            if any(h.handle(self.dev, frame) for h in self.interrupts):
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
            log.warning("session hit the %d s limit", p.MAX_SESSION)
            shot = save_failure(self.last_frame, "session_timeout")
            alert("session_timeout", "Obelisk bot: a session couldn't finish",
                  f"Stopped after {p.MAX_SESSION // 60} min on screen '{self.last_screen}'. "
                  "Possibly a new popup or a game update; screenshot in logs/failures/.",
                  tags="hourglass", image=shot)
            return True
        if self.last_screen != screens.MINING or not p.founder_done(now):
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
            self.failures += 1
            log.error("cloud %s aborted: %s (%d/%d)", what, e, self.failures, self.max_failures)
            shot = save_failure(e.frame, f"cloud_{what}_{e}")
            if self.failures >= self.max_failures:
                log.critical("too many job failures; stopping (exit %d)", EXIT_TOO_MANY_FAILURES)
                alert("stopped_failures", "Obelisk bot STOPPED: repeated failures",
                      f"Last: cloud {what}: {e}. Screenshots in logs/failures/.",
                      priority="urgent", tags="rotating_light", force=True, image=shot)
                raise SystemExit(EXIT_TOO_MANY_FAILURES)
            return None

    def end_session(self, upload: bool = True) -> None:
        if upload and self.cloud is not None and self.last_screen == screens.MINING:
            self.cloud_step("upload")
        save_latest(self.dev)
        self.dev.go_home()
        length = self.planner.finish()
        log.info("session end: %d s online; next session %s", length,
                 time.strftime("%H:%M:%S", time.localtime(self.planner.next_start)))

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_foreground_check >= self.FOREGROUND_EVERY:
            self.last_foreground_check = now
            self.ensure_game_in_front()

        frame = self.dev.capture()
        self.last_frame, self.last_screen = frame, None
        if any(h.handle(self.dev, frame) for h in self.interrupts):
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
            self.failures += 1
            log.error("job %s aborted: %s (%d/%d)", job.name, e, self.failures, self.max_failures)
            shot = save_failure(e.frame, f"{job.name}_{e}")
            if self.failures >= self.max_failures:
                log.critical("too many job failures; stopping (exit %d)", EXIT_TOO_MANY_FAILURES)
                alert("stopped_failures", "Obelisk bot STOPPED: repeated job failures",
                      f"Last: {job.name}: {e}. Screenshots in logs/failures/. "
                      "Restart with: systemctl --user start obelisk-bot",
                      priority="urgent", tags="rotating_light", force=True, image=shot)
                raise SystemExit(EXIT_TOO_MANY_FAILURES)
        finally:
            self.check_gems(job, before)

    def check_gems(self, job, before) -> None:
        """Gem guard: no bot job may ever leave us with fewer gems."""
        after = stable_gems(self.dev)
        if before is None or after is None:
            log.info("gem guard: couldn't read gems (%s -> %s); not checked", before, after)
            return
        if after < before:
            log.critical("GEM GUARD: gems went %d -> %d during job %s; stopping (exit %d)",
                         before, after, job.name, EXIT_TOO_MANY_FAILURES)
            shot = save_failure(self.dev.capture(), f"GEM_GUARD_{job.name}_{before}_to_{after}")
            alert("gem_guard", "Obelisk bot STOPPED: gems dropped",
                  f"Gems went {before} -> {after} during job '{job.name}'. The bot stopped "
                  "itself; check the game and logs/failures/ before restarting.",
                  priority="urgent", tags="rotating_light,gem", force=True, image=shot)
            raise SystemExit(EXIT_TOO_MANY_FAILURES)
        log.info("gem guard: %d -> %d ok", before, after)

    def ensure_game_in_front(self, planned: bool = False) -> None:
        if self.dev.game_in_front():
            return
        if planned:
            log.info("bringing the game to the front for the session")
        else:
            log.warning("game is not in front; relaunching it")
        self.dev.launch_game()
        # the game may come back with "Offline Gains" popups; interrupts handle them
        ready = (screens.MINING, screens.OFFLINE_OPTIONS, screens.OFFLINE_RESULTS,
                 screens.LOOTBUG_POPUP)
        if wait_for(self.dev, lambda f: screens.identify(f) in ready,
                    timeout=self.LAUNCH_WAIT, interval=3) is None:
            log.error("game relaunched but mining screen not seen within %d s", self.LAUNCH_WAIT)
            save_failure(self.dev.capture(), "relaunch_no_mining_screen")
        else:
            log.info("game in front again")
