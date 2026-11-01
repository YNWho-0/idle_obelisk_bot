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

from . import screens
from .jobs import JobAborted, save_failure, wait_for

# Exit code for "stopped on purpose after repeated job failures"; the
# systemd unit must NOT restart on it (RestartPreventExitStatus=3).
EXIT_TOO_MANY_FAILURES = 3

log = logging.getLogger("obelisk.scheduler")


class Scheduler:
    FOREGROUND_EVERY = 60      # seconds between "is the game in front?" checks
    LAUNCH_WAIT = 90           # seconds to wait for the mining screen after a relaunch

    def __init__(self, dev, jobs, interrupts=(), poll: float = 2.0,
                 max_unknown: int = 15, max_failures: int = 3):
        self.dev = dev
        self.jobs = list(jobs)
        self.interrupts = list(interrupts)
        self.poll = poll
        self.max_unknown = max_unknown
        self.max_failures = max_failures
        self.unknown = 0
        self.failures = 0
        self.last_foreground_check = float("-inf")

    def run_forever(self) -> None:
        while True:
            self.tick()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_foreground_check >= self.FOREGROUND_EVERY:
            self.last_foreground_check = now
            self.ensure_game_in_front()

        frame = self.dev.capture()
        if any(h.handle(self.dev, frame) for h in self.interrupts):
            return

        screen = screens.identify(frame)
        if screen != screens.MINING:
            self.unknown += 1
            if self.unknown == self.max_unknown:
                log.warning("off the mining screen for %d polls (%s); waiting, not pressing",
                            self.unknown, screen)
                save_failure(frame, f"stuck_{screen}")
            self.dev.sleep(self.poll)
            return
        self.unknown = 0

        for job in self.jobs:
            if job.due(now, frame):
                self.run_job(job)
                break
        self.dev.sleep(self.poll)

    def run_job(self, job) -> None:
        try:
            log.info("job %s -> %s", job.name, job.run(self.dev))
            self.failures = 0
        except JobAborted as e:
            self.failures += 1
            log.error("job %s aborted: %s (%d/%d)", job.name, e, self.failures, self.max_failures)
            save_failure(e.frame, f"{job.name}_{e}")
            if self.failures >= self.max_failures:
                log.critical("too many job failures; stopping (exit %d)", EXIT_TOO_MANY_FAILURES)
                raise SystemExit(EXIT_TOO_MANY_FAILURES)

    def ensure_game_in_front(self) -> None:
        if self.dev.game_in_front():
            return
        log.warning("game is not in front; relaunching it")
        self.dev.launch_game()
        if wait_for(self.dev, lambda f: screens.identify(f) == screens.MINING,
                    timeout=self.LAUNCH_WAIT, interval=3) is None:
            log.error("game relaunched but mining screen not seen within %d s", self.LAUNCH_WAIT)
            save_failure(self.dev.capture(), "relaunch_no_mining_screen")
        else:
            log.info("game relaunched; back on the mining screen")
