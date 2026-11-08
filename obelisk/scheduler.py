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

# Auto-restarts of Waydroid for broken Android sound (see Device.audio_broken):
# if it is broken again within this long of the last one, the restart didn't
# help, so stop for a human instead of restarting in a loop.
AUDIO_RESTART_GAP = 6 * 3600

# Screens that mean "the game is up" after a (re)launch; popups on them are
# handled by the interrupts.
GAME_READY = (screens.MINING, screens.OFFLINE_OPTIONS, screens.OFFLINE_RESULTS,
              screens.LOOTBUG_POPUP, screens.ANR)

log = logging.getLogger("obelisk.scheduler")


LATEST = Path(__file__).resolve().parent.parent / "logs" / "latest.jpg"
# frames of sessions without a founder crate, to work out why some days get
# 20-21 of the ~24 expected (2026-11-06)
FOUNDER_EMPTY_DIR = LATEST.parent / "founder_empty"
FOUNDER_EMPTY_KEEP = 100


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
    KEEP_OPEN_STUCK = 5 * 60        # seconds off the mining screen before restarting the game
    KEEP_OPEN_MAX_RESTARTS = 3      # restarts in a row without reaching mining, then give up

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
        self.off_mining_since = None    # keep-open: when the mining screen was last lost
        self.stuck_restarts = 0
        self.last_gems = None           # last good gem reading this session
        self.founder_claimed = False

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
            self.note_no_founder()
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
        if self.last_screen == screens.MINING:
            self.off_mining_since, self.stuck_restarts = None, 0
            if not self.session_failed:
                report_up()             # hours can pass before this session ends
        elif self.off_mining_since is None:
            self.off_mining_since = now
        elif now - self.off_mining_since >= self.KEEP_OPEN_STUCK:
            self.recover_keep_open()
            return
        if now - self.last_shot >= self.KEEP_OPEN_SHOT_EVERY:
            self.last_shot = now
            State().set("last_session_at", now)
            save_latest(self.dev)

    def recover_keep_open(self) -> None:
        """Normal sessions recover from an unknown screen through their time limit
        and the next restart; keep-open has neither, and buffs are ticking."""
        self.off_mining_since = None
        self.stuck_restarts += 1
        shot = save_failure(self.last_frame, f"keep_open_stuck_{self.last_screen}")
        if self.stuck_restarts > self.KEEP_OPEN_MAX_RESTARTS:
            log.error("keep-open: still stuck after %d game restarts; ending it",
                      self.KEEP_OPEN_MAX_RESTARTS)
            State().set("keep_open_until", None)
            alert("keep_open_stuck", "Obelisk bot: keep-open ended early",
                  f"Stuck on screen '{self.last_screen}' through {self.KEEP_OPEN_MAX_RESTARTS} game "
                  "restarts, so the game goes to the background to save buffs. "
                  "Screenshot in logs/failures/.", priority="high", tags="warning", image=shot)
            return
        log.warning("keep-open: off the mining screen for %d min; restarting the game (%d/%d)",
                    self.KEEP_OPEN_STUCK // 60, self.stuck_restarts, self.KEEP_OPEN_MAX_RESTARTS)
        self.dev.restart_game()
        self.wait_for_game()
        self.last_foreground_check = time.monotonic()
        self.settle()
        self.last_gems = None

    def end_keep_open(self) -> None:
        """No upload yet: the cloud info on screen is as old as this session, and
        the phone may have uploaded since. HOME saves the game; the next session
        restarts it, reads the cloud afresh and uploads if that is safe."""
        self.kept_until = None
        log.info("keep-open: over; game to background, then a normal session")
        self.note_no_founder()
        self.end_session(upload=False, next_soon=True)

    def begin_session(self) -> None:
        self.planner.start()
        self.session_t0 = time.monotonic()
        self.failures, self.session_failed = 0, False
        self.last_gems, self.founder_claimed = None, False   # a phone's spending isn't a loss
        self.off_mining_since, self.stuck_restarts = None, 0
        State().set("last_session_at", self.planner.started)
        log.info("session start")
        self.note_game_version()
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

    def note_game_version(self) -> None:
        """Say when the game updates: the update of 2026-11-05 moved a button by
        about 1 px and left the bot stuck on a popup."""
        version = self.dev.game_version()
        st = State()
        old = st.get("game_version")
        if version is None or version == old:
            return
        st.set("game_version", version)
        if old is None:
            return
        log.warning("the game was updated: v%s -> v%s", old, version)
        alert("game_updated", f"Obelisk bot: game updated to v{version}",
              f"It was v{old}. The bot carries on, but updates can move buttons. If failure "
              "alerts follow, the bot's screen templates probably need updating.",
              tags="arrow_up", force=True)

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
        self.send_game_away()
        alert(key, title, f"{message} Failure screenshots: dashboard / logs/failures/. "
              "Restart: dashboard -> Restart bot.", priority="urgent", tags=tags, force=True,
              image=image)
        raise SystemExit(EXIT_TOO_MANY_FAILURES)

    def send_game_away(self) -> None:
        """Before stopping for good: a game left in front would burn buffs until
        someone notices. HOME is a key event, never a press in the game."""
        if self.planner is None:        # --always-open: staying in front is the point
            return
        State().set("keep_open_until", None)
        try:
            self.dev.go_home()
        except Exception as e:          # never let this block the alert
            log.warning("could not send the game to the background: %s", e)

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

    def note_no_founder(self) -> None:
        """Keep the last frame of a session that claimed no founder crate, so a
        crate the bot failed to recognise can be seen afterwards."""
        if self.founder_claimed or self.last_frame is None \
                or not any(j.name == "founder" for j in self.jobs):
            return
        try:
            FOUNDER_EMPTY_DIR.mkdir(parents=True, exist_ok=True)
            path = FOUNDER_EMPTY_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.jpg"
            cv2.imwrite(str(path), self.last_frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            for old in sorted(FOUNDER_EMPTY_DIR.glob("*.jpg"))[:-FOUNDER_EMPTY_KEEP]:
                old.unlink()
            log.info("founder: no crate this session (%s)", path.name)
        except OSError as e:
            log.debug("could not save the no-crate frame: %s", e)

    def end_session(self, upload: bool = True, next_soon: bool = False) -> None:
        if self.last_screen == screens.MINING and self.last_gems is not None:
            self.check_gem_gap(stable_gems(self.dev), "outside any job this session")
        if upload and self.cloud is not None and self.last_screen == screens.MINING:
            self.cloud_step("upload")
        save_latest(self.dev)
        self.dev.go_home()
        length = self.planner.finish()
        State().set("last_session_at", self.planner.clock())
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
        self.check_gem_gap(before, f"before job '{job.name}'")
        try:
            result = job.run(self.dev)
            log.info("job %s -> %s", job.name, result)
            if job.name == "founder" and result == "claimed":
                self.founder_claimed = True
            self.failures = 0
        except JobAborted as e:
            self.job_failed(job.name, e)
        finally:
            self.check_gems(job, before)

    def check_gems(self, job, before) -> None:
        """Gem guard: no bot job may ever leave us with fewer gems. An unreadable
        'before' falls back on the last good reading."""
        after = stable_gems(self.dev)
        if before is None:
            before = self.last_gems
        if before is None or after is None:
            log.info("gem guard: couldn't read gems (%s -> %s); not checked", before, after)
            return
        if after < before:
            self.gem_stop(f"during job '{job.name}'", before, after)
        self.last_gems = after
        log.info("gem guard: %d -> %d ok", before, after)

    def check_gem_gap(self, gems, where: str) -> None:
        """The same rule for what happens between jobs: popups and cloud steps press
        buttons too. No drop in 213 such gaps in the logs up to 2026-11-06."""
        if gems is None:
            return
        if self.last_gems is not None and gems < self.last_gems:
            self.gem_stop(where, self.last_gems, gems)
        self.last_gems = gems

    def gem_stop(self, where: str, before: int, after: int) -> None:
        save_failure(self.dev.capture(), f"GEM_GUARD_{where}_{before}_to_{after}")
        self.stop("gem_guard", "Obelisk bot STOPPED: gems dropped",
                  f"GEM GUARD: gems went {before} -> {after} {where}. "
                  "Check the game before restarting.", tags="rotating_light,gem")

    def wait_for_game(self) -> bool:
        started = time.time() - 30        # the launch was just before this call
        if wait_for(self.dev, lambda f: screens.identify(f) in GAME_READY,
                    timeout=self.LAUNCH_WAIT, interval=3) is None:
            log.error("game (re)started but no known screen within %d s", self.LAUNCH_WAIT)
            save_failure(self.dev.capture(), "relaunch_no_known_screen")
            if self.dev.audio_broken(started):
                self.restart_for_audio()
            return False
        return True

    def restart_for_audio(self, now: "float | None" = None, start_restart=None) -> None:
        """The game crashes at start because Android's sound is out of memory:
        restart the Waydroid session + bot (the dashboard's restart, in its own
        unit, which also starts this service again). Exits; never returns."""
        now = time.time() if now is None else now
        st = State()
        last = st.get("audio_restart_at")
        if last is not None and now - last < AUDIO_RESTART_GAP:
            self.stop("android_audio", "Obelisk bot STOPPED: game crashes on start",
                      "The game crashes at start because Android's sound is out of memory, "
                      f"and restarting Waydroid {(now - last) / 60:.0f} min ago didn't fix it. "
                      "Try rebooting the Pi.")
        log.error("the game crashes at start: Android's sound is out of memory; "
                  "restarting the Waydroid session and the bot")
        if start_restart is None:
            from .dashboard import start_restart
        err = start_restart("all")
        if err:
            self.stop("android_audio", "Obelisk bot STOPPED: game crashes on start",
                      "The game crashes at start because Android's sound is out of memory, "
                      f"and the Waydroid restart couldn't start ({err}). "
                      "Use the dashboard's Restart Waydroid + bot.")
        st.set("audio_restart_at", now)
        alert("android_audio", "Obelisk: restarting Waydroid",
              "The game crashed at start because Android's sound ran out of memory. "
              "The bot is restarting Waydroid and itself; nothing to do unless another alert follows.",
              tags="arrows_counterclockwise", force=True)
        raise SystemExit(0)           # Restart=on-failure: the restart script starts us again

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
