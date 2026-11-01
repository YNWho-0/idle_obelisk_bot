"""One loop: capture -> interrupts (popups) -> first due job.

Interrupt handlers get first look at every frame; they're for popups that can
appear over any screen. None are defined yet (gift bug / founder drop come
next, once we have reference screenshots of them).
"""

from __future__ import annotations

import logging
import time

from . import screens
from .jobs import JobAborted, save_failure

# Exit code for "stopped on purpose after repeated job failures"; the
# systemd unit must NOT restart on it (RestartPreventExitStatus=3).
EXIT_TOO_MANY_FAILURES = 3

log = logging.getLogger("obelisk.scheduler")


class Scheduler:
    def __init__(self, dev, jobs, interrupts=(), poll: float = 2.0,
                 max_unknown: int = 15, max_failures: int = 3):
        self.dev = dev
        self.jobs = list(jobs)
        self.interrupts = list(interrupts)
        self.poll = poll
        self.max_unknown = max_unknown
        self.max_failures = max_failures

    def run_forever(self) -> None:
        unknown = failures = 0
        while True:
            frame = self.dev.capture()
            now = time.monotonic()

            if any(h.handle(self.dev, frame) for h in self.interrupts):
                continue

            screen = screens.identify(frame)
            if screen != screens.MINING:
                unknown += 1
                if unknown == self.max_unknown:
                    log.warning("off the mining screen for %d polls (%s); waiting, not pressing",
                                unknown, screen)
                    save_failure(frame, f"stuck_{screen}")
                self.dev.sleep(self.poll)
                continue
            unknown = 0

            for job in self.jobs:
                if job.due(now, frame):
                    try:
                        log.info("job %s -> %s", job.name, job.run(self.dev))
                        failures = 0
                    except JobAborted as e:
                        failures += 1
                        log.error("job %s aborted: %s (%d/%d)", job.name, e,
                                  failures, self.max_failures)
                        save_failure(e.frame, f"{job.name}_{e}")
                        if failures >= self.max_failures:
                            log.critical("too many job failures; stopping (exit %d)",
                                         EXIT_TOO_MANY_FAILURES)
                            raise SystemExit(EXIT_TOO_MANY_FAILURES)
                    break
            self.dev.sleep(self.poll)
