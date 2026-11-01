#!/usr/bin/env python3
"""Offline tests against saved screenshots. No device needed.

  venv/bin/python tests/test_offline.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obelisk import screens                       # noqa: E402
from obelisk.scheduler import Scheduler          # noqa: E402
from obelisk.session import SessionPlanner       # noqa: E402
from obelisk.jobs import (FounderJob, FreebieJob, JobAborted, LootbugJob,  # noqa: E402
                          LootbugPopupInterrupt, NotificationInterrupt, OfflineGainsInterrupt)

S = ROOT / "screens"

# keep test artefacts out of the real logs/ (they looked like real alarms)
import tempfile                                    # noqa: E402
import obelisk.gems as _gems                       # noqa: E402
import obelisk.jobs as _jobs                       # noqa: E402
_TMP = Path(tempfile.mkdtemp(prefix="obelisk-tests-"))
_jobs.FAIL_DIR = _TMP / "failures"
_gems.UNREAD_DIR = _TMP / "gem_unread"
_jobs.State.PATH = _TMP / "state.json"


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(S / name))
    assert img is not None, name
    return img


MINING_READY = load("01_wm720x1280.png")
MINING_READY2 = load("02b_after_tap.png")
MINING_EMPTY = load("13_store_closed.png")
MINING_CLEAN = load("40_mining_after_settings.png")   # after Options cleanup, gauge ~6/19
STORE_READY = load("10_store_freebie_ready.png")
STORE_TOAST = load("11_after_claim_popup.png")
STORE_EMPTY = load("12_after_claim_settled.png")

GEM_150 = (521, 615, 602, 668)     # "150 gems" Resource Pack button
CLAIM = (521, 495, 602, 548)
STORE_X = (403, 1100, 453, 1147)
STORE_ICON = (596, 1098, 664, 1137)


def inside(pt, box) -> bool:
    return box[0] <= pt[0] < box[2] and box[1] <= pt[1] < box[3]


class FakeDevice:
    """Plays back screenshots. `script` maps the current frame name to a list of
    (allowed press box, frames shown afterwards). Any other press fails."""

    def __init__(self, start: str, frames: dict, script: dict):
        self.frames, self.script = frames, script
        self.queue = [start]
        self.presses: list[tuple[str, tuple[int, int]]] = []

    @property
    def current(self) -> str:
        return self.queue[0]

    def capture(self) -> np.ndarray:
        # show queued frames in order; the last one stays on screen
        name = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        self._shown = name
        return self.frames[name]

    def press(self, x, y):
        here = self._shown
        for box, after in self.script.get(here, []):
            if inside((x, y), box):
                self.presses.append((here, (x, y)))
                self.queue = list(after)
                return
        raise AssertionError(f"unexpected press at {(x, y)} on {here}")

    def sleep(self, s):
        pass


BUG_POPUP = load("23_redbug_press.png")      # 3-row gem shop
BUG_POPUP_BIG = load("21_bug_popup_1.png")   # 6-row gem shop + reward toasts
NO_BUGS = load("24_after_redbug.png")
FRAMES = {"mining": MINING_READY, "mining_empty": MINING_EMPTY, "store": STORE_READY,
          "toast": STORE_TOAST, "store_empty": STORE_EMPTY, "mining_clean": MINING_CLEAN,
          "bug_popup": BUG_POPUP, "bug_popup_big": BUG_POPUP_BIG, "no_bugs": NO_BUGS}
FRAMES["founder"] = load("50_founder_2.png")
FRAMES["founder_gone"] = load("51_founder_press_3.png")
LOOTBUG_ALL = (55, 700, 85, 725)
FRAMES["redbug"] = load("26_redbug_banked.png")
BUG_SLOTS = (215, 600, 450, 700)
CRATE = (315, 525, 425, 590)
BUG_POPUP_X = (542, 222, 586, 266)


# ---------------------------------------------------------------- detection

def test_identify():
    assert screens.identify(load("00_current.png")) == screens.UNKNOWN  # old 1024x732 layout
    for img in (MINING_READY, MINING_READY2, MINING_EMPTY, MINING_CLEAN):
        assert screens.identify(img) == screens.MINING
    for name in ("30_grid_menu.png", "32_options_display.png"):
        assert screens.identify(load(name)) == screens.UNKNOWN, name
    for name in ("21_bug_popup_1.png", "21_bug_popup_3.png", "23_redbug_press.png"):
        assert screens.identify(load(name)) == screens.LOOTBUG_POPUP, name
    for img in (STORE_READY, STORE_TOAST, STORE_EMPTY):
        assert screens.identify(img) == screens.STORE


def test_badge():
    assert screens.store_badge(MINING_READY) and screens.store_badge(MINING_READY2)
    assert not screens.store_badge(MINING_EMPTY)
    assert screens.store_badge(MINING_CLEAN)
    assert screens.store_fill(MINING_READY) < screens.store_fill(MINING_CLEAN)


def test_freebie_states():
    assert screens.freebie_state(STORE_READY)[0] == "ready"
    assert screens.freebie_state(STORE_TOAST)[0] == "unknown"
    assert screens.freebie_state(STORE_EMPTY)[0] == "empty"


def test_gem_button_in_claim_slot_is_never_ready():
    img = STORE_READY.copy()
    x1, y1, x2, y2 = GEM_150
    img[CLAIM[1]:CLAIM[3], CLAIM[0]:CLAIM[2]] = STORE_READY[y1:y2, x1:x2]
    assert screens.freebie_state(img)[0] == "unknown"


def test_missing_header_is_never_ready():
    img = STORE_READY.copy()
    img[455:490, 165:315] = STORE_READY[574:609, 165:315]   # "Resource Pack" header
    assert screens.freebie_state(img)[0] == "unknown"


def test_scrolled_store_follows_header():
    img = STORE_READY.copy()
    img[210:1010] = STORE_READY[290:1090]   # content scrolled up 80 px
    state, m = screens.freebie_state(img)
    assert state == "ready" and m.center == (561, 441), (state, m)


def test_claim_button_does_not_match_maxed_or_gem_buttons():
    """Best ready-Claim score anywhere in the button column must be the real one."""
    tpl = screens.A.get("claim_ready")
    for (y1, y2) in [(270, 345), (600, 680), (720, 800), (840, 920), (960, 1040)]:
        m = tpl.score(STORE_READY, (500, y1, 620, y2))
        assert m.score < 0.6, (y1, m.score)


def test_lootbug_row():
    for img in (MINING_READY, MINING_READY2, MINING_EMPTY, MINING_CLEAN, load("20_giftbug_3.png")):
        row = screens.lootbug_row(img)
        assert row is not None and inside(row.center, LOOTBUG_ALL), row
    for img in (NO_BUGS, STORE_READY, BUG_POPUP, load("30_grid_menu.png")):
        assert screens.lootbug_row(img) is None


def test_founder_crate():
    for n in (1, 2, 3):
        m = screens.founder_crate(load(f"50_founder_{n}.png"))
        assert m is not None and inside(m.center, CRATE), (n, m)
    for img in (MINING_READY, MINING_CLEAN, NO_BUGS, STORE_READY, BUG_POPUP,
                load("51_founder_press_1.png"), load("51_founder_press_3.png")):
        assert screens.founder_crate(img) is None


def test_lootbug_sprite():
    for name in ("01_wm720x1280.png", "20_giftbug_1.png", "20_giftbug_3.png",
                 "25_after_notification_press.png", "26_redbug_banked.png", "50_founder_3.png"):
        m = screens.lootbug_sprite(load(name))
        assert m is not None and inside(m.center, BUG_SLOTS), (name, m)
    for img in (NO_BUGS, STORE_READY, BUG_POPUP):
        assert screens.lootbug_sprite(img) is None


def test_wiki_skins_find_real_bugs():
    """The 14 wiki-derived templates alone must find both real skins and
    nothing on bug-free frames."""
    for name, skin in (("26_redbug_banked.png", "default"), ("50_founder_2.png", "default"),
                       ("20_giftbug_1.png", "halloween"), ("13_store_closed.png", "halloween")):
        m = screens.wiki_lootbug(load(name))
        assert m is not None and m.name.endswith(skin) and inside(m.center, BUG_SLOTS), (name, m)
    for name in ("24_after_redbug.png", "22_bug_popup_closed.png", "40_mining_after_settings.png"):
        assert screens.wiki_lootbug(load(name)) is None, name


# ---------------------------------------------------------------- job flow

def test_founder_job_claims():
    dev = FakeDevice("founder", FRAMES, {"founder": [(CRATE, ["founder_gone"])]})
    assert FounderJob().run(dev) == "claimed" and len(dev.presses) == 1


def test_founder_job_aborts_if_crate_stays():
    dev = FakeDevice("founder", FRAMES, {"founder": [(CRATE, ["founder"])]})
    try:
        FounderJob().run(dev)
    except JobAborted as e:
        assert "still there" in str(e)
    else:
        raise AssertionError("expected abort")


def test_lootbug_job_loots_and_closes():
    for popup in ("bug_popup", "bug_popup_big"):
        dev = FakeDevice("redbug", FRAMES, {
            "redbug": [(BUG_SLOTS, [popup])],
            popup: [(BUG_POPUP_X, ["no_bugs"])],
        })
        assert LootbugJob().run(dev) == "looted"
        assert [p[0] for p in dev.presses] == ["redbug", popup], dev.presses


def test_lootbug_job_aborts_if_popup_never_opens():
    dev = FakeDevice("redbug", FRAMES, {"redbug": [(BUG_SLOTS, ["redbug"])]})
    try:
        LootbugJob().run(dev)
    except JobAborted as e:
        assert "did not open" in str(e) and len(dev.presses) == 1
    else:
        raise AssertionError("expected abort")


def test_popup_interrupt_only_presses_x():
    dev = FakeDevice("bug_popup_big", FRAMES, {"bug_popup_big": [(BUG_POPUP_X, ["no_bugs"])]})
    assert LootbugPopupInterrupt().handle(dev, BUG_POPUP_BIG)
    assert len(dev.presses) == 1
    dev2 = FakeDevice("mining_clean", FRAMES, {})
    assert not LootbugPopupInterrupt().handle(dev2, MINING_CLEAN) and not dev2.presses


def test_job_claims_and_closes():
    dev = FakeDevice("mining", FRAMES, {
        "mining": [(STORE_ICON, ["store"])],
        "store": [(CLAIM, ["toast", "toast", "store_empty"]), (STORE_X, ["mining_empty"])],
        "store_empty": [(STORE_X, ["mining_empty"])],
    })
    assert FreebieJob().run(dev) == "claimed"
    assert [p[0] for p in dev.presses] == ["mining", "store", "store_empty"], dev.presses


def test_job_nothing_banked():
    dev = FakeDevice("mining_empty", FRAMES, {
        "mining_empty": [(STORE_ICON, ["store_empty"])],
        "store_empty": [(STORE_X, ["mining_empty"])],
    })
    assert FreebieJob().run(dev) == "nothing"
    assert len(dev.presses) == 2


def test_job_unverified_claim_aborts_and_closes():
    dev = FakeDevice("mining", FRAMES, {
        "mining": [(STORE_ICON, ["store"])],
        "store": [(CLAIM, ["store"]), (STORE_X, ["mining_empty"])],
    })
    # the claim press "does nothing": frames keep showing a ready button
    try:
        FreebieJob().run(dev)
    except JobAborted as e:
        assert "not verified" in str(e)
        assert dev.presses[-1][1] == (428, 1123), dev.presses   # closed via X
    else:
        raise AssertionError("expected abort")


def test_job_refuses_off_mining_screen():
    dev = FakeDevice("store", FRAMES, {})
    try:
        FreebieJob().run(dev)
    except JobAborted as e:
        assert "not on mining" in str(e) and not dev.presses
    else:
        raise AssertionError("expected abort")


def test_notification_box():
    for name in ("40_mining_after_settings.png", "20_giftbug_1.png", "24_after_redbug.png",
                 "22_bug_popup_closed.png"):
        box = screens.notification_box(load(name))
        assert box is not None and 615 <= box[0] <= 630 and 695 <= box[1] <= 710, (name, box)
    for name in ("25_after_notification_press.png", "13_store_closed.png", "01_wm720x1280.png",
                 "51_founder_press_1.png", "50_founder_2.png", "10_store_freebie_ready.png"):
        assert screens.notification_box(load(name)) is None, name


def test_notification_interrupt_dismisses_once():
    FRAMES["notif"] = load("24_after_redbug.png")
    FRAMES["no_notif"] = load("25_after_notification_press.png")
    NOTIF = (0, 615, 205, 710)
    dev = FakeDevice("notif", FRAMES, {"notif": [(NOTIF, ["no_notif"])]})
    intr = NotificationInterrupt()
    frame = dev.capture()                                  # the scheduler captures first
    assert intr.handle(dev, frame) and len(dev.presses) == 1
    assert not intr.handle(dev, FRAMES["notif"])          # rate-limited
    assert not NotificationInterrupt().handle(dev, FRAMES["no_notif"])


def test_read_gems():
    from obelisk.gems import read_gems
    expect = {"01_wm720x1280.png": 3852, "13_store_closed.png": 3882, "22_bug_popup_closed.png": 3884,
              "33_opt_menus_2.png": 3884, "24_after_redbug.png": 3974, "50_founder_3.png": 3994,
              "51_founder_press_1.png": 4014, "64_skins_scrolled.png": 4057,
              "65_gems_4110.png": 4110, "66_gems_6294.png": 6294}
    for name, value in expect.items():
        assert read_gems(load(name)) == value, (name, read_gems(load(name)))
    assert read_gems(load("00_current.png")) is None      # old layout
    dimmed = np.zeros((1280, 720, 3), np.uint8)             # counter dimmed by an overlay
    dimmed[126:148, 466:568] = load("67_gems_dimmed_6580_crop.png")
    assert read_gems(dimmed) == 6580


def test_founder_window_learns_hourly_phase():
    t0 = 1_000_000.0
    clock = [t0]
    job = FounderJob(state=_jobs.State(_TMP / "founder_state.json"), clock=lambda: clock[0])
    assert job.in_window(t0)                                   # unknown phase: always look
    dev = FakeDevice("founder", FRAMES, {"founder": [(CRATE, ["founder_gone"])]})
    assert job.run(dev) == "claimed"                           # claim at t0 -> phase learned
    for offset, expect in [(600, False), (1800, False), (3600 - 180, False), (3600 - 100, True),
                           (3600, True), (3600 + 240, True), (3600 + 400, False), (7200 + 10, True)]:
        assert job.in_window(t0 + offset) == expect, offset
    # persisted: a fresh job (after a restart) knows the phase too
    again = FounderJob(state=_jobs.State(_TMP / "founder_state.json"), clock=lambda: clock[0])
    assert not again.in_window(t0 + 1800)


def test_founder_outside_window_looks_only_every_minute():
    clock = [5_000_000.0]
    st = _jobs.State(_TMP / "founder_state2.json")
    st.set("founder_last_claim", clock[0] - 1800)              # mid-hour: outside window
    job = FounderJob(state=st, clock=lambda: clock[0])
    crate_frame = load("50_founder_2.png")
    assert job.due(100.0, crate_frame)                         # first look happens
    assert not job.due(130.0, crate_frame)                     # 30 s later: skipped
    assert job.due(161.0, crate_frame)                         # after 60 s: looks again


def test_daily_summary():
    from obelisk.summary import summarize
    d = _TMP / "sumlogs"
    (d / "failures").mkdir(parents=True, exist_ok=True)
    (d / "obelisk.log.2026-01-02").write_text("\n".join([
        "2026-01-02 00:00:05,1 INFO    obelisk: bot started",
        "2026-01-02 01:23:20,1 INFO    obelisk.scheduler: job founder -> claimed",
        "2026-01-02 01:30:00,1 INFO    obelisk.scheduler: gem guard: 6000 -> 6020 ok",
        "2026-01-02 01:30:00,1 INFO    obelisk.scheduler: job lootbug -> looted",
        "2026-01-02 02:00:00,1 INFO    obelisk.scheduler: gem guard: couldn't read gems (None -> 6020); not checked",
        "2026-01-02 02:23:21,1 INFO    obelisk.scheduler: job founder -> claimed",
        "2026-01-02 02:52:00,1 INFO    obelisk.scheduler: job freebie -> claimed",
        "2026-01-02 02:52:01,1 INFO    obelisk.scheduler: gem guard: 6040 -> 6230 ok",
        "2026-01-03 00:00:01,1 INFO    obelisk.scheduler: job founder -> claimed",   # other day
    ]) + "\n")
    text = summarize("2026-01-02", d)
    assert "Founder drops claimed                      2" in text, text
    assert "Lootbug batches looted                     1" in text
    assert "6000 -> 6230" in text and "2 checked ok, 1 unreadable" in text
    assert "Founder times: 01:23 02:23" in text and "Status: OK" in text and "(1 start)" in text
    assert "No log lines" in summarize("2026-01-05", d)


# ---------------------------------------------------------------- scheduler

class FakeGameDevice(FakeDevice):
    def __init__(self, *a, in_front=True, after_launch=("mining_clean",), **kw):
        super().__init__(*a, **kw)
        self.in_front, self.after_launch, self.launches = in_front, after_launch, 0

    def game_in_front(self):
        return self.in_front

    def launch_game(self):
        self.launches += 1
        self.in_front = True
        self.queue = list(self.after_launch)


def test_scheduler_relaunches_game_when_not_in_front():
    dev = FakeGameDevice("store", FRAMES, {}, in_front=False)
    sched = Scheduler(dev, [], poll=0)
    sched.tick()
    assert dev.launches == 1 and not dev.presses


def test_scheduler_runs_first_due_job_from_mining():
    # frames 25 -> 23 -> 24 all show 3974 gems, so the gem guard stays quiet
    FRAMES["redbug_3974"] = load("25_after_notification_press.png")
    dev = FakeGameDevice("redbug_3974", FRAMES, {
        "redbug_3974": [(BUG_SLOTS, ["bug_popup"])],
        "bug_popup": [(BUG_POPUP_X, ["no_bugs"])],
    })
    sched = Scheduler(dev, [FounderJob(), LootbugJob()], poll=0)
    sched.tick()
    assert [p[0] for p in dev.presses] == ["redbug_3974", "bug_popup"], dev.presses


def test_scheduler_never_presses_off_mining_screen():
    dev = FakeGameDevice("store", FRAMES, {})
    sched = Scheduler(dev, [FounderJob(), LootbugJob(), FreebieJob()], poll=0)
    for _ in range(3):
        sched.tick()
    assert not dev.presses and sched.unknown == 3


def test_gem_guard_stops_on_loss():
    class SpendJob:
        name = "spend"
        def run(self, dev):
            dev.queue = ["poorer"]
            return "done"
    FRAMES["richer"] = load("51_founder_press_3.png")     # 4014 gems
    FRAMES["poorer"] = load("24_after_redbug.png")        # 3974 gems
    dev = FakeGameDevice("richer", FRAMES, {})
    try:
        Scheduler(dev, [], poll=0).run_job(SpendJob())
    except SystemExit as e:
        assert e.code == 3
    else:
        raise AssertionError("gem guard did not trip")


def test_gem_guard_allows_gain():
    class EarnJob:
        name = "earn"
        def run(self, dev):
            dev.queue = ["richer"]
            return "done"
    FRAMES["richer"] = load("51_founder_press_3.png")
    FRAMES["poorer"] = load("24_after_redbug.png")
    dev = FakeGameDevice("poorer", FRAMES, {})
    Scheduler(dev, [], poll=0).run_job(EarnJob())       # must not raise


# ---------------------------------------------------------------- sessions

def test_offline_gains_screens():
    assert screens.identify(load("73_buffs_after_5min.png")) == screens.OFFLINE_OPTIONS
    assert screens.identify(load("74_after_offline_confirm.png")) == screens.OFFLINE_RESULTS


def test_planner_schedules_around_drop():
    E = 2_000_000.0 + 23 * 60 + 22                     # an expected drop at hh:23:22
    clock = [E - 3600 + 30]                             # just after the previous hour's claim
    st = _jobs.State(_TMP / "planner_state.json")
    st.set("founder_last_claim", E - 3600)
    founder = FounderJob(state=st, clock=lambda: clock[0])
    p = SessionPlanner(founder, clock=lambda: clock[0])
    p.start()
    assert not p.founder_done(clock[0])                 # a claim from before the session doesn't count
    assert p.founder_done(E - 3600 + 301)               # ... but that drop's window ends at +300
    clock[0] = E - 3600 + 400
    p.finish()
    assert p.next_start == E - p.LEAD                   # next hour, 90 s early
    # session for E: founder claimed at E+5 -> done; next session an hour later
    clock[0] = E - 90; p.start(); assert p.expected_drop(clock[0]) == E
    assert not p.founder_done(E - 10)
    st.set("founder_last_claim", E); clock[0] = E + 30
    assert p.founder_done(clock[0])
    p.finish(); assert p.next_start == E + 3600 - p.LEAD
    # no crate this hour: done after the window, next session the hour after
    clock[0] = E + 3600 - 90; p.start()
    assert not p.founder_done(E + 3600 + 200) and p.founder_done(E + 3600 + 301)
    clock[0] = E + 3600 + 301; p.finish(); assert p.next_start == E + 7200 - p.LEAD


def test_session_opens_handles_offline_popups_and_goes_home():
    FRAMES["off_opts"] = load("73_buffs_after_5min.png")
    FRAMES["off_res"] = load("74_after_offline_confirm.png")
    CONFIRM = (120, 1089, 600, 1128)
    dev = FakeGameDevice("off_opts", FRAMES, {
        "off_opts": [(CONFIRM, ["off_res"])],
        "off_res": [(CONFIRM, ["no_bugs"])],
    }, in_front=False, after_launch=("off_opts",))
    dev.homes = 0
    def go_home():
        dev.homes += 1; dev.in_front = False
    dev.go_home = go_home
    clock = [3_000_000.0]
    st = _jobs.State(_TMP / "session_state.json")
    st.set("founder_last_claim", clock[0] - 3600 - 400)  # next drop ~53 min away: don't wait
    founder = FounderJob(state=st, clock=lambda: clock[0])
    sched = Scheduler(dev, [], interrupts=[OfflineGainsInterrupt()], poll=0,
                      planner=SessionPlanner(founder, clock=lambda: clock[0]))
    for _ in range(6):
        sched.session_step()
        if dev.homes:
            break
    assert dev.launches == 1 and dev.homes == 1, (dev.launches, dev.homes, dev.presses)
    assert [p[0] for p in dev.presses] == ["off_opts", "off_res"], dev.presses
    assert sched.planner.started is None and sched.planner.next_start > clock[0]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except BaseException:          # incl. SystemExit from the gem guard
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
