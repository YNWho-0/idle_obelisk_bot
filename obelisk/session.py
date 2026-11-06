"""Session planning: keep the game in the background (= offline: buff timers
don't tick, freebies and lootbugs bank up) and bring it to the front only for
short sessions.

Measured 2026-11-01: in the background the game uses ~0% CPU and on return
shows "Offline Gains" (buffs not consumed with that option off). Each session
starts with a force-stop + launch (the game only fetches cloud-save info at
startup; tested safe: it saves locally when sent to the background) and ends
with HOME.

The dashboard's keep-open mode holds one session open for a chosen time, so
the buffs tick (see Scheduler.keep_open_until).

The Founder crate drops on its own hourly cycle (the Store's Founder's Bundle
countdown read 28:48 right after a claim, so claiming does not restart it) and
then WAITS on the field until pressed. So sessions don't wait for it: one
short session every EVERY seconds (< 1 h, so two drops can never pile up
between sessions) claims whatever is there.
"""

from __future__ import annotations

import time

from .device import SAVE_WAIT


class SessionPlanner:
    EVERY = 50 * 60       # seconds between session starts (< the 60 min drop cycle)
    MAX_SESSION = 5 * 60
    SAVE_WAIT = SAVE_WAIT # seconds after HOME before a force-stop: the game saves on HOME

    def __init__(self, clock=time.time):
        self.clock = clock
        self.next_start = clock()         # first session right away
        self.earliest = self.next_start   # no session before this, whatever asks for one
        self.started = None

    def start(self) -> None:
        self.started = self.clock()

    def timed_out(self, now: float) -> bool:
        return now - self.started >= self.MAX_SESSION

    def finish(self) -> float:
        """End the session; returns its length and schedules the next one."""
        now = self.clock()
        length = now - self.started
        self.next_start = self.started + self.EVERY
        self.earliest = now + self.SAVE_WAIT
        self.started = None
        return length
