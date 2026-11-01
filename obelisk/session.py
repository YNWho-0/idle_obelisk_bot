"""Session planning: keep the game in the background (= offline: buff timers
don't tick, freebies and lootbugs bank up) and bring it to the front only for
short hourly sessions around the Founder drop.

Measured 2026-11-01: in the background the game uses ~0% CPU and on return
shows "Offline Gains" (buffs not consumed with that option off), so HOME is
enough; no force-stop (which could lose unsaved progress).
"""

from __future__ import annotations

import time


class SessionPlanner:
    LEAD = 90            # open this many seconds before the expected drop
    FALLBACK_EVERY = 3600
    MAX_SESSION = 12 * 60

    def __init__(self, founder, clock=time.time):
        self.founder = founder            # FounderJob (knows the learned phase)
        self.clock = clock
        self.next_start = clock()         # first session right away
        self.started = None

    def expected_drop(self, t: float) -> float | None:
        """The drop time this session is for: the next one not yet over."""
        last = self.founder.state.get("founder_last_claim")
        if last is None:
            return None
        period, hi = self.founder.PERIOD, self.founder.WINDOW[1]
        k = max(0, -(-(t - hi - last) // period))      # ceil
        return last + k * period

    def start(self) -> None:
        self.started = self.clock()

    def founder_done(self, now: float) -> bool:
        """Founder claimed in this session, or its window has passed."""
        last = self.founder.state.get("founder_last_claim")
        if last is not None and self.started is not None and last >= self.started:
            return True
        exp = self.expected_drop(self.started)
        if exp is None:
            return now - self.started >= self.founder.WINDOW[1]   # no phase: give it 5 min
        if exp > self.started + self.LEAD + 60:
            return True          # e.g. the startup session: next drop is far off, don't wait
        return now > exp + self.founder.WINDOW[1]

    def timed_out(self, now: float) -> bool:
        return now - self.started >= self.MAX_SESSION

    def finish(self) -> float:
        """End the session; returns its length and schedules the next one."""
        now = self.clock()
        length = now - self.started
        exp = self.expected_drop(now + 1)
        if exp is None:
            self.next_start = self.started + self.FALLBACK_EVERY
        else:
            nxt = exp if exp - self.LEAD > now else exp + self.founder.PERIOD
            self.next_start = nxt - self.LEAD
        self.started = None
        return length
