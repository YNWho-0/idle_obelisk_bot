"""Session planning: keep the game in the background (= offline: buff timers
don't tick, freebies and lootbugs bank up) and bring it to the front only for
short sessions.

Measured 2026-11-01: in the background the game uses ~0% CPU and on return
shows "Offline Gains" (buffs not consumed with that option off), so HOME is
enough; no force-stop (which could lose unsaved progress).

The Founder crate drops on its own hourly cycle (the Store's Founder's Bundle
countdown read 28:48 right after a claim, so claiming does not restart it) and
then WAITS on the field until pressed. So sessions don't wait for it: one
short session every EVERY seconds (< 1 h, so two drops can never pile up
between sessions) claims whatever is there.
"""

from __future__ import annotations

import time


class SessionPlanner:
    EVERY = 50 * 60       # seconds between session starts (< the 60 min drop cycle)
    MAX_SESSION = 5 * 60

    def __init__(self, founder=None, clock=time.time):
        self.founder = founder
        self.clock = clock
        self.next_start = clock()         # first session right away
        self.started = None

    def expected_drop(self, t: float):
        return None                       # not predicted; the crate waits

    def start(self) -> None:
        self.started = self.clock()

    def founder_done(self, now: float) -> bool:
        return True                       # nothing to wait for (see module doc)

    def timed_out(self, now: float) -> bool:
        return now - self.started >= self.MAX_SESSION

    def finish(self) -> float:
        """End the session; returns its length and schedules the next one."""
        length = self.clock() - self.started
        self.next_start = self.started + self.EVERY
        self.started = None
        return length
